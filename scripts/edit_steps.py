from difflib import SequenceMatcher
import math
from pathlib import Path
import re

from common import SKILL, check_ranges, digest, ff, fingerprint, lock_payload, project, read, require_aliyun, require_script, unlocked, write


def analyze_edits(a):
    job=Path(a.job).resolve()
    unlocked(job)
    require_script(job/'script.txt');require_aliyun(job)
    if (job/'decisions.json').exists():
        raise ValueError('已有剪辑决策，不能覆盖复核结果')
    p=project(job)
    profile=read(SKILL/f'assets/profiles/{p["layout"]}.json')
    words=read(job/'asr/words.json')['words']
    if not (job/'asr/preview.srt').exists():
        raise ValueError('先生成只读预览字幕')
    candidates=[]
    def add(kind,start,end,suggestion,context):
        if end-start>.04:
            candidates.append({'id':f'c{len(candidates)+1:04}','kind':kind,'start':start,'end':end,
                'suggested_action':suggestion,'context':context,'action':'undecided','reason':''})
    add('head',0,max(0,words[0]['begin_time']/1000-.08),'drop','开场准备区间，检查是否存在未识别语音')
    for i,(left,right) in enumerate(zip(words,words[1:])):
        start,end=left['end_time']/1000,right['begin_time']/1000
        if end-start>profile['gap_threshold']:
            before=''.join(w['text'] for w in words[max(0,i-4):i+1])
            after=''.join(w['text'] for w in words[i+1:i+7])
            emotional=bool(re.search('但是|不过|然而|所以|关键|注意|最后|第一|第二|第三|第四',after))
            add('pause',start,end,'keep' if emotional else 'shorten',before+' 【停顿】 '+after)
    for i,word in enumerate(words):
        if word['text'].strip() in {'嗯','呃','额'}:
            start=max(0,word['begin_time']/1000-.05)
            end=min(p['duration'],word['end_time']/1000+.05)
            if i:start=max(start,words[i-1]['end_time']/1000)
            if i+1<len(words):end=min(end,words[i+1]['begin_time']/1000)
            add('filler',start,end,'drop',word['text']+'：仅候选，确认是否有表达作用')
    add('tail',min(p['duration'],words[-1]['end_time']/1000+.18),p['duration'],'drop','结尾准备区间')
    seen={}
    raw=read(job/'asr/transcription.json')
    for transcript in raw.get('transcripts',[]):
        for sentence in transcript.get('sentences',[]):
            text=re.sub(r'\W','',sentence.get('text',''))
            if len(text)>=8 and text in seen:
                add('repetition',sentence['begin_time']/1000,sentence['end_time']/1000,
                    'keep',f'与 {seen[text]:.3f}s 文本相同；辨别重录与强调性重复：{text}')
            seen[text]=sentence['begin_time']/1000
    actual=''.join(w['text'] for w in words)
    reference=re.sub(r'\W','',(job/'script.txt').read_text(encoding='utf-8'))
    spoken=re.sub(r'\W','',actual)
    differences=[]
    for tag,i,j,k,l in SequenceMatcher(None,reference,spoken,autojunk=False).get_opcodes():
        if tag!='equal':differences.append({'type':tag,'script':reference[i:j],'spoken':spoken[k:l]})
    write(job/'script_comparison.json',{'note':'文本差异仅用于定位；合理口语改写不判为口误。',
        'differences':differences})
    write(job/'decisions.json',{'version':1,'profile':profile,'source_sha256':p['source_sha256'],
        'words_sha256':digest(job/'asr/words.json'),'candidates':candidates})
    print(f'生成 {len(candidates)} 个候选；agent结合原稿、上下文和试听，通过 review_edits.py 写入决定')


def review_edits(a):
    job=Path(a.job).resolve()
    unlocked(job)
    data=read(job/'decisions.json')
    updates=read(a.reviews)
    by_id={c['id']:c for c in data['candidates']}
    for change in updates:
        if change['id'] not in by_id:
            if change.get('kind')!='manual':
                raise ValueError('新增剪辑必须设置 kind=manual，明确start/end和理由')
            check_ranges([change],project(job)['duration'])
            c={**change,'suggested_action':'keep','context':change.get('context','agent原稿与音频复核')}
            data['candidates'].append(c)
            by_id[c['id']]=c
        c=by_id[change['id']]
        if change['action'] not in {'keep','drop','shorten'} or not change.get('reason','').strip():
            raise ValueError('每项需 action=keep/drop/shorten 和非空 reason')
        if change['action']=='shorten':
            keep=change.get('keep_seconds',data['profile']['ordinary_pause'])
            if not .12<=keep<c['end']-c['start']:
                raise ValueError('缩短后停顿至少0.12秒且必须短于原停顿')
            c['keep_seconds']=keep
        c.update(action=change['action'],reason=change['reason'])
    write(job/'decisions.json',data)
    print(f'已记录复核；剩余 {sum(c["action"]=="undecided" for c in data["candidates"])} 项')


def build_timeline(a):
    job=Path(a.job).resolve()
    unlocked(job)
    p=project(job); data=read(job/'decisions.json')
    if abs(p['audio_video_start_offset'])>.08:
        raise ValueError('原片音视频偏移过大；先修复再入库')
    if data['words_sha256']!=digest(job/'asr/words.json') or data['source_sha256']!=p['source_sha256']:
        raise ValueError('决策绑定的素材或转写已改动')
    if any(c['action']=='undecided' for c in data['candidates']):
        raise ValueError('仍有未复核候选；通过review_edits记录keep/drop/shorten')
    fps=p['fps']; total_frames=math.floor(p['duration']*fps+1e-5)
    words=read(job/'asr/words.json')['words']
    cuts=[]
    for c in data['candidates']:
        if c['action']=='keep':continue
        start,end=c['start'],c['end']
        if c['action']=='shorten':
            start+=c['keep_seconds']*.6;end-=c['keep_seconds']*.4
        for w in words:
            wa,wb=w['begin_time']/1000,w['end_time']/1000
            if start<wb and end>wa and not (start<=wa+.0001 and end>=wb-.0001):
                raise ValueError(f'决策 {c["id"]} 切入字词 {w["text"]}，请调整手工边界')
        left=math.ceil(start*fps-1e-6);right=math.floor(end*fps+1e-6)
        if right>left:cuts.append((left,right))
    merged=[]
    for left,right in sorted(cuts):
        if merged and left<=merged[-1][1]:merged[-1][1]=max(merged[-1][1],right)
        else:merged.append([left,right])
    segments=[];cursor=0;out=0
    for left,right in merged+[[total_frames,total_frames]]:
        if left>cursor:
            count=left-cursor
            segments.append({'source_start_frame':cursor,'source_end_frame':left,
                'output_start_frame':out,'frames':count})
            out+=count
        cursor=max(cursor,right)
    if not segments:raise ValueError('全部素材被删除')
    timeline={'version':1,'fps':fps,'source_sha256':p['source_sha256'],
        'total_frames':out,'duration':out/fps,'speed':1.0,'segments':segments,
        'decisions_sha256':digest(job/'decisions.json')}
    write(job/'timeline.json',timeline)
    print(f'计划 {len(segments)} 段，{out/fps:.3f}秒；先 preview_cuts.py，再 lock_timeline.py')


def preview_cuts(a):
    job=Path(a.job).resolve();p=project(job)
    timeline=read(job/'timeline.json');fps=timeline['fps']
    if timeline['decisions_sha256']!=digest(job/'decisions.json'):
        raise ValueError('决策已更新，请重建时间轴')
    segments=timeline['segments'];items=[]
    for i,(left,right) in enumerate(zip(segments,segments[1:])):
        before=left['source_end_frame']/fps
        after=right['source_start_frame']/fps
        start=max(left['source_start_frame']/fps,before-a.context)
        end=min(right['source_end_frame']/fps,after+a.context)
        filters=(f'[0:v]trim=start={start}:end={before},setpts=PTS-STARTPTS[v0];'
            f'[1:a]atrim=start={start}:end={before},asetpts=PTS-STARTPTS[a0];'
            f'[0:v]trim=start={after}:end={end},setpts=PTS-STARTPTS[v1];'
            f'[1:a]atrim=start={after}:end={end},asetpts=PTS-STARTPTS[a1];'
            '[v0][a0][v1][a1]concat=n=2:v=1:a=1[joined][a];')
        rotation=f'transpose={p["rotate"]},' if p['rotate']!='auto' else ''
        filters+='[joined]'+rotation+'scale=480:-2[v]'
        filename=f'cut-{i+1:03}.mp4'
        ff(['-i',job/p['source'],'-i',job/p['active_audio'],'-filter_complex_threads','1',
            '-filter_complex',filters,'-map','[v]','-map','[a]','-c:v','libx264','-preset','ultrafast',
            '-threads','2','-c:a','aac','-movflags','+faststart',job/'previews'/filename],job)
        items.append({'id':f'cut-{i+1:03}','file':filename,'before':before,'after':after,
            'reviewed':False,'note':''})
    write(job/'previews/review.json',{'timeline_sha256':digest(job/'timeline.json'),
        'decisions_sha256':digest(job/'decisions.json'),'cuts':items,
        'retained_pauses':[{'id':c['id'],'start':c['start'],'end':c['end'],'reason':c['reason'],
            'reviewed':False} for c in read(job/'decisions.json')['candidates'] if c['action']=='keep'],
        'whole_track_reviewed':False,'note':''})
    print(f'生成 {len(items)} 个实际拼接预览；复核切口及保留停顿后填写 previews/review.json')


def lock_timeline(a):
    job=Path(a.job).resolve()
    unlocked(job)
    review=read(job/'previews/review.json')
    timeline=read(job/'timeline.json')
    if review['timeline_sha256']!=digest(job/'timeline.json') or review['decisions_sha256']!=digest(job/'decisions.json'):
        raise ValueError('预览对应旧版本，必须重新生成')
    if not review.get('whole_track_reviewed') or not review.get('note','').strip():
        raise ValueError('尚未记录完整剪辑计划的复核结论')
    expected_ids={f'cut-{i+1:03}' for i in range(len(timeline['segments'])-1)}
    if len(review['cuts'])!=len(expected_ids) or {c['id'] for c in review['cuts']}!=expected_ids:
        raise ValueError('切口复核清单缺失或重复，重新生成预览')
    expected_kept={c['id'] for c in read(job/'decisions.json')['candidates'] if c['action']=='keep'}
    if len(review['retained_pauses'])!=len(expected_kept) or {c['id'] for c in review['retained_pauses']}!=expected_kept:
        raise ValueError('保留停顿的复核清单缺失或重复')
    if not all(x.get('reviewed') is True for x in review['cuts']+review['retained_pauses']):
        raise ValueError('切口或保留停顿未全部复核')
    write(job/'timeline.lock.json',{'sha256':lock_payload(job),'review_sha256':digest(job/'previews/review.json'),
        'note':review['note']})
    print('时间轴已锁定；后续字幕与风格处理不得改变剪辑时序')
