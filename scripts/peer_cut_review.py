"""A proposes, B independently reviews ordered frames and audio evidence before lock."""
import array
import math
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

from common import digest, exe, ff, fingerprint, project, read, require_aliyun, unlocked, verify_source, write

POLICY = 'two-agent-cut-review-v1'


def binding(job):
    p=verify_source(job)
    return {'timeline_sha256':digest(job/'timeline.json'), 'decisions_sha256':digest(job/'decisions.json'),
            'source_sha256':p['source_sha256'], 'audio_sha256':digest(job/p['active_audio']),
            'words_sha256':digest(job/'asr/words.json'), 'script_sha256':digest(job/'script.txt')}


def locations(job,phase):
    directory=job/('qc' if phase=='proposal' else 'output')
    return directory/'cut-review-proposal.json',directory/'cut-peer-review.json'


def source_for_output(t,n):
    s=next(s for s in t['segments'] if s['output_start_frame']<=n<s['output_start_frame']+s['frames'])
    count=s['source_end_frame']-s['source_start_frame']
    # FFmpeg fps uses the last input in the nearest output timestamp bin.
    offset=min(count-1,max(0,math.ceil((n-s['output_start_frame']+.5)*count/s['frames']-1e-9)-1))
    return s['source_start_frame']+offset


def sample_indices(center,radius,limit,dense=False):
    if dense:return list(range(max(0,center-radius),min(limit,center+radius+1)))
    offsets={round(radius*q) for q in (-1,-.75,-.5,-.25,0,.25,.5,.75,1)}
    offsets.update(range(-3,4))  # Every immediately adjacent frame, even in overview mode.
    return sorted({max(0,min(limit-1,center+n)) for n in offsets})


def capture_frames(video,indices,directory,job,rotate='auto'):
    indices=sorted(set(indices));directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    if not indices:return {}
    filters=['select='+ '+'.join(f'eq(n\\,{n})' for n in indices)]
    if rotate!='auto':filters.append('transpose='+rotate)
    filters.append('scale=320:320:force_original_aspect_ratio=decrease')
    script=directory/'select.ffilter';script.write_text(','.join(filters))
    ff(['-i',video,'-filter_script:v',script,'-vsync','0','-threads','2',directory/'frame-%06d.png'],job)
    paths=sorted(directory.glob('frame-*.png'))
    if len(paths)!=len(indices):raise ValueError('没有提取全部精确源帧，请检查时间轴与素材')
    return dict(zip(indices,paths))


def contact_sheet(strips,paths,target,width=144,columns=12):
    first=next(iter(paths.values()))
    with Image.open(first) as pic:height=round(pic.height*width/pic.width)
    rows=sum(math.ceil(len(numbers)/columns) for _,numbers,_ in strips)
    canvas=Image.new('RGB',(columns*width,rows*(height+34)),'#eeeeee');draw=ImageDraw.Draw(canvas)
    row=0
    for label,numbers,marker in strips:
        for start in range(0,len(numbers),columns):
            y=row*(height+34);draw.text((3,y+2),label,fill='black')
            for column,n in enumerate(numbers[start:start+columns]):
                x=column*width
                with Image.open(paths[n]) as pic:
                    frame=pic.convert('RGB');frame.thumbnail((width,height));canvas.paste(frame,(x,y+32))
                draw.text((x+2,y+16),f'F{n}',fill='red' if n==marker else 'black')
                if n==marker:draw.rectangle((x,y+30,x+width-1,y+height+32),outline='red',width=2)
            row+=1
    Path(target).parent.mkdir(parents=True,exist_ok=True);canvas.save(target)


class PCM:
    def __init__(self,path):
        raw=subprocess.check_output([exe('ffmpeg'),'-v','error','-i',str(path),'-vn','-ac','2','-ar','48000','-f','s16le','-'])
        self.samples=array.array('h',raw)
        if sys.byteorder!='little':self.samples.byteswap()

    def stats(self,start,end):
        a=max(0,round(start*48000))*2;b=min(len(self.samples),round(end*48000)*2)
        values=self.samples[a:b]
        if not values:return {'available':False}
        peak=max(abs(v) for v in values)/32768
        rms=math.sqrt(sum(v*v for v in values)/len(values))/32768
        return {'available':True,'peak_dbfs':20*math.log10(max(peak,1e-9)),
                'rms_dbfs':20*math.log10(max(rms,1e-9))}


def edge_words(words,time,side):
    result=[]
    for w in words:
        a,b=w['begin_time']/1000,w['end_time']/1000
        if a<time+.35 and b>time-.35:
            result.append({'text':w['text'],'start':a,'end':b,'cut_inside_asr_word':a<time<b,'edge':side})
    return result


def evidence(job,path):return {'file':str(path.relative_to(job)),'sha256':digest(path)}


def audio_plot(audio,item,fps,radius,limit,path):
    canvas=Image.new('RGB',(768,280),'white');draw=ImageDraw.Draw(canvas)
    for row,(side,center) in enumerate((('OUT',item['source_out_frame']),('IN',item['source_in_frame']))):
        frames=list(range(max(0,center-radius),min(limit,center+radius+1)))
        y=row*140
        draw.text((5,y+3),f'{side}: source peaks, not a listening verdict',fill='black')
        points=[]
        for i,n in enumerate(frames):
            value=audio.stats(n/fps,(n+1)/fps)
            level=max(-60,min(0,value.get('peak_dbfs',-60)))
            x=30+round(i*700/max(1,len(frames)-1));height=round((level+60)*90/60)
            points.append((x,y+120-height))
            if n==center:draw.line((x,y+20,x,y+123),fill='red',width=2)
        if len(points)>1:draw.line(points,fill='black',width=2)
        threshold=y+120-round(25*90/60)
        draw.line((30,threshold,730,threshold),fill='#aaaaaa')
        draw.text((32,threshold-13),'-35 dBFS reference',fill='#666666')
    canvas.save(path)


def prepare_cut_review(args):
    job=Path(args.job).resolve();phase=getattr(args,'phase','proposal')
    if phase=='proposal':unlocked(job)
    p=verify_source(job);require_aliyun(job);t=read(job/'timeline.json')
    if t['decisions_sha256']!=digest(job/'decisions.json'):raise ValueError('先按当前决策重建时间轴')
    planner=args.planner.strip()
    if not planner:raise ValueError('记录实际提出方案的Agent A身份')
    context=getattr(args,'context',.6)
    if not math.isfinite(context) or not .1<=context<=2:raise ValueError('context应为0.1到2秒')
    fps=t['fps'];radius=max(3,round(context*fps));source_limit=math.floor(p['duration']*fps+1e-5)
    dense=getattr(args,'dense',False)
    directory=job/('qc/cut-review-evidence' if phase=='proposal' else 'output/cut-review-evidence')
    directory.mkdir(parents=True,exist_ok=True)
    words=read(job/'asr/words.json')['words'];decisions=read(job/'decisions.json')['candidates']
    items=[];source_indices=set();output_indices=set()
    for i,(left,right) in enumerate(zip(t['segments'],t['segments'][1:]),1):
        outgoing,incoming=left['source_end_frame'],right['source_start_frame'];n=right['output_start_frame']
        join=sample_indices(n,radius,t['total_frames'],dense)
        source_join=[source_for_output(t,k) for k in join]
        overview_join=sorted({max(0,min(t['total_frames']-1,n+k)) for k in [-radius,-radius//2,-2,-1,0,1,2,radius//2,radius]})
        overview_source=[source_for_output(t,k) for k in overview_join]
        out=sample_indices(max(0,outgoing-1),radius,source_limit,dense)
        into=sample_indices(incoming,radius,source_limit,dense)
        source_indices.update(out+into+source_join+overview_source);output_indices.update(join+overview_join)
        touching=[c for c in decisions if c['action']!='keep' and c['start']<incoming/fps and c['end']>outgoing/fps]
        items.append({'id':f'cut-{i:03}','continuous_source':outgoing==incoming,
            'source_out_frame':outgoing,'source_in_frame':incoming,'output_boundary_frame':n,
            'source_out_indices':out,'source_in_indices':into,'joined_output_indices':join,'joined_source_indices':source_join,
            'overview_output_indices':overview_join,'overview_source_indices':overview_source,
            'edit_reasons':[{'id':c['id'],'kind':c.get('kind'),'reason':c['reason']} for c in touching],
            'word_edges':edge_words(words,outgoing/fps,'out')+edge_words(words,incoming/fps,'in'),
            'evidence':[]})
    audio=PCM(job/p['active_audio'])
    with tempfile.TemporaryDirectory(prefix='peer-cut-',dir=job) as temp:
        source_paths=capture_frames(job/p['source'],source_indices,Path(temp)/'source',job,p['rotate'])
        export_paths=capture_frames(job/'output/final.mp4',output_indices,Path(temp)/'export',job) if phase=='export' else None
        for item in items:
            path=directory/f'{item["id"]}-context.jpg'
            contact_sheet([('Original OUT: red = first deleted frame',item['source_out_indices'],item['source_out_frame']),
                ('Original IN: red = first incoming frame',item['source_in_indices'],item['source_in_frame']),
                ('Proposed JOIN: retained source sequence',item['joined_source_indices'],item['source_in_frame'])],source_paths,path)
            item['evidence'].append(evidence(job,path))
            if export_paths:
                path=directory/f'{item["id"]}-export.jpg'
                contact_sheet([('Actual export JOIN',item['joined_output_indices'],item['output_boundary_frame'])],export_paths,path)
                item['evidence'].append(evidence(job,path))
            item['audio_edges']={side:[{'frame':frame,**audio.stats(frame/fps,(frame+1)/fps)}
                for frame in range(max(0,center-3),min(source_limit,center+3))]
                for side,center in [('out',item['source_out_frame']),('in',item['source_in_frame'])]}
            path=directory/f'{item["id"]}-audio.jpg'
            audio_plot(audio,item,fps,radius,source_limit,path);item['evidence'].append(evidence(job,path))
        # An overview contains ordered JOIN frames for every cut. Reviewer can
        # inspect it first, then open full source context/dense frames for doubts.
        overview=[]
        for page in range(0,len(items),5):
            group=items[page:page+5];path=directory/f'overview-{page//5+1:02}.jpg'
            strips=[]
            for item in group:
                n=item['output_boundary_frame']
                strips.append((item['id']+' JOIN',item['overview_output_indices'] if export_paths else item['overview_source_indices'],n if export_paths else item['source_in_frame']))
            contact_sheet(strips,export_paths or source_paths,path,columns=12)
            entry={**evidence(job,path),'cut_ids':[item['id'] for item in group]};overview.append(entry)
            for item in group:item['evidence'].append({k:v for k,v in entry.items() if k!='cut_ids'})
    report={'policy':POLICY,'phase':phase,'binding':binding(job),'planner':planner,
            'context_seconds':context,'dense_all_frames':dense,'items':items,'overview':overview,
            'audio_listening':'not_performed_by_tool','guidance':'Short-video jumps are acceptable. Review noticeable gesture interruption and audible-word risk, not pixel-distance thresholds.'}
    if phase=='export':report['video_sha256']=digest(job/'output/final.mp4')
    proposal,_=locations(job,phase);write(proposal,report)
    print(f'生成{len(items)}处剪辑方案证据，{len(report["overview"])}张总览；交给独立Agent B审片。')
    return report


def validate_proposal(job,proposal):
    if proposal['binding']!=binding(job):raise ValueError('剪辑方案已改变，请重新提方案及独立复核')
    t=read(job/'timeline.json')
    expected=[f'cut-{i:03}' for i in range(1,len(t['segments']))]
    if [i['id'] for i in proposal['items']]!=expected:raise ValueError('方案切口清单不完整')
    if proposal['phase']=='export' and proposal.get('video_sha256')!=digest(job/'output/final.mp4'):
        raise ValueError('成片已改变，请重新审片')


def review_cut_plan(args):
    job=Path(args.job).resolve();phase=getattr(args,'phase','proposal')
    if phase=='proposal':unlocked(job)
    path,output=locations(job,phase);proposal=read(path);validate_proposal(job,proposal)
    report=read(args.reviews)
    reviewer=report.get('reviewer','').strip()
    if not reviewer or reviewer==proposal['planner']:raise ValueError('Agent B必须是区别于方案Agent A的独立审片者')
    items=report.get('items',[]);lookup={i['id']:i for i in proposal['items']}
    if len(items)!=len(lookup) or {i.get('id') for i in items}!=set(lookup):raise ValueError('独立复核需覆盖所有切口')
    for item in items:
        planned=lookup[item['id']]
        if item.get('decision') not in {'approve','adjust','keep_pause','defer'}:raise ValueError('复核决定应为approve/adjust/keep_pause/defer')
        if not item.get('visual_note','').strip() or not item.get('audio_note','').strip():raise ValueError('记录实际动作与音频依据，不批量自动确认')
        if item.get('listening_status') not in {'listened','unavailable'}:raise ValueError('明确实际试听或无法试听')
        if item.get('audio_status') not in {'safe','potential_truncation','uncertain'}:raise ValueError('明确声音边界检查结果')
        if item['audio_status']=='potential_truncation' and item['decision']=='approve':raise ValueError('已确认可能截声不能直接通过；微调或保留气口')
        allowed={e['file']:e['sha256'] for e in planned['evidence']}
        proofs=item.get('evidence',[])
        if not proofs:raise ValueError('需记录实际查看的方案图证据')
        for proof in proofs:
            path=(job/proof.get('file','')).resolve()
            if not path.is_relative_to(job) or allowed.get(proof.get('file'))!=proof.get('sha256') or digest(path)!=proof['sha256']:
                raise ValueError('查看证据不属于本切口或已改变')
        if phase=='export' and not any(e['file'].endswith('-export.jpg') or 'overview-' in e['file'] for e in proofs):
            raise ValueError('导出复核需实际查看成片序列')
    saved={**report,'phase':phase,'proposal_sha256':digest(locations(job,phase)[0]),
           'binding':proposal['binding'],'passed':all(i['decision']=='approve' for i in items)}
    write(output,saved);print(f'Agent B复核已记录；通过={saved["passed"]}')


def require_peer_review(job,phase='proposal'):
    job=Path(job);t=read(job/'timeline.json')
    if t.get('peer_cut_review_policy')!=POLICY:return None
    proposal_path,review_path=locations(job,phase)
    if not proposal_path.exists() or not review_path.exists():raise ValueError('缺少Agent A方案或独立Agent B复核')
    proposal=read(proposal_path);validate_proposal(job,proposal);report=read(review_path)
    if report.get('proposal_sha256')!=digest(proposal_path) or report.get('binding')!=proposal['binding']:
        raise ValueError('独立审片记录对应旧方案')
    if not report.get('passed'):raise ValueError('Agent B提出的切点/声音调整尚未处理，先返给Agent A')
    if report.get('reviewer')==proposal['planner']:raise ValueError('不能由方案agent自审代替独立审片')
    lookup={i['id']:i for i in proposal['items']}
    if len(report['items'])!=len(lookup) or {i['id'] for i in report['items']}!=set(lookup):raise ValueError('独立审片记录不完整')
    for i in report['items']:
        if i['decision']!='approve' or i.get('audio_status')=='potential_truncation':raise ValueError('仍有未解决的切口')
        for proof in i['evidence']:
            if digest(job/proof['file'])!=proof['sha256']:raise ValueError('审片画面已改变，重新复核')
    return report


def fully_dropped_words(t,words):
    fps=t['fps']
    return [w for w in words if not any(w['begin_time']/1000<s['source_end_frame']/fps and
            w['end_time']/1000>s['source_start_frame']/fps for s in t['segments'])]


def apply_peer_adjustments(args):
    job=Path(args.job).resolve();unlocked(job)
    pp,rp=locations(job,'proposal');proposal=read(pp);validate_proposal(job,proposal);review=read(rp)
    if review.get('proposal_sha256')!=digest(pp):raise ValueError('调整要求对应旧方案')
    if review.get('reviewer')==proposal['planner']:raise ValueError('缺少独立审片要求')
    t=read(job/'timeline.json');changes=read(args.changes);byid={i['id']:i for i in review['items']}
    if not changes or len({c['id'] for c in changes})!=len(changes):raise ValueError('需不重复的具体回帧调整')
    dropped=fully_dropped_words(t,read(job/'asr/words.json')['words']);fps=t['fps']
    pending=[]
    for change in changes:
        item=byid.get(change.get('id'))
        if not item or item['decision'] not in {'adjust','keep_pause'} or not change.get('reason','').strip():raise ValueError('仅实施Agent B已提出且Agent A说明理由的调整')
        a,b=change.get('restore_out_frames',0),change.get('restore_in_frames',0)
        if any(isinstance(n,bool) or not isinstance(n,int) or n<0 for n in (a,b)) or not a+b:raise ValueError('回帧必须是正的整帧数')
        index=int(change['id'].split('-')[1])-1;left,right=t['segments'][index:index+2]
        oldout,oldin=left['source_end_frame'],right['source_start_frame']
        if a+b>oldin-oldout:raise ValueError('回帧不能越过相邻保留片段')
        restored=[(oldout/fps,(oldout+a)/fps),((oldin-b)/fps,oldin/fps)]
        if any(start< w['end_time']/1000 and end>w['begin_time']/1000
               for start,end in restored if end>start for w in dropped):
            raise ValueError('回帧会恢复已删掉的完整讲话/重说；先按原稿修改决策再重建')
        pending.append((index,a,b,change,oldout,oldin))
    for index,a,b,change,oldout,oldin in pending:
        left,right=t['segments'][index:index+2];left['source_end_frame']+=a;right['source_start_frame']-=b
        t.setdefault('peer_adjustments',[]).append({**change,'old_out_frame':oldout,'old_in_frame':oldin,
              'reviewer':review['reviewer'],'proposal_sha256':digest(pp)})
    cursor=0
    for s in t['segments']:
        s['source_frames']=s['source_end_frame']-s['source_start_frame']
        s['frames']=s['source_frames'] if s.get('buffer_kind') else max(1,round(s['source_frames']/t.get('speed',1.3)))
        s['output_start_frame']=cursor;cursor+=s['frames']
    t.update(total_frames=cursor,duration=cursor/fps)
    for kind in ('head','tail'):
        selected=[s for s in t['segments'] if s.get('buffer_kind')==kind]
        if selected:t['source_buffers'][kind+'_output_range']=[selected[0]['output_start_frame']/fps,
                (selected[-1]['output_start_frame']+selected[-1]['frames'])/fps]
    write(job/'timeline.json',t);print('已回帧；重新提方案、生成预览/节奏，再由Agent B复核。')
