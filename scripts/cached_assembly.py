"""Assemble actual frame ranges and derive timestamps from verified original caches."""
from argparse import Namespace
from pathlib import Path
import shutil
from common import digest,ff,probe,project,read,require_aliyun,verify_source,write

MODE='derived-assembly-cache-v1'

def mapped_words(parts, fps):
    result=[];cursor=0;sentence=0
    for i,part in enumerate(parts):
        parent=Path(part['cache']);words=read(parent/'asr/words.json')['words']
        start,end=part['start_frame']/fps,part['end_frame']/fps
        last=None
        for index,w in enumerate(words):
            a,b=w['begin_time']/1000,w['end_time']/1000
            if b<=start or a>=end:continue
            group=(w.get('channel_id',0),w.get('sentence_id',index))
            if group!=last:sentence+=1;last=group
            item={**w,'begin_time':round((cursor+max(a,start)-start)*1000),
                'end_time':round((cursor+min(b,end)-start)*1000),'sentence_id':sentence,
                'origin':{'segment':i,'word_index':index,'begin_time':w['begin_time'],
                'end_time':w['end_time'],'edge_clipped':a<start or b>end}}
            if item['end_time']>item['begin_time']:result.append(item)
        cursor+=end-start
    return result

def validate_derived(job,proof):
    manifest=read(job/'asr/assembly.json')
    if digest(job/'asr/assembly.json')!=proof.get('assembly_sha256'):raise ValueError('组装映射已改变')
    if proof.get('task_id'):raise ValueError('派生转写不能冒用云端任务ID')
    parts=[]
    for part in manifest['parts']:
        parent=(job/part['cache']).resolve();verify_source(parent);require_aliyun(parent)
        if digest(parent/'asr/provenance.json')!=part['provenance_sha256']:raise ValueError('原片缓存来源已改变')
        parts.append({**part,'cache':str(parent)})
    expected=mapped_words(parts,manifest['fps'])
    if read(job/'asr/words.json')['words']!=expected:raise ValueError('派生字词必须严格按原片真实时间与组装帧映射')
    if abs(project(job)['fps']-manifest['fps'])>1e-6:raise ValueError('组装帧率不匹配')
    return proof

def assemble_cached_sources(args):
    from media_steps import ingest
    job=Path(args.job).resolve()
    if (job/'project.json').exists():raise ValueError('请用新任务组装，不能覆盖旧来源')
    plan=read(args.plan);fps=plan.get('fps',25);parts=plan['parts']
    if not parts or not 1<=fps<=120:raise ValueError('需要真实组装区间及帧率')
    parents={};inputs=[];filters=[];mapped=[]
    for i,part in enumerate(parts):
        parent=Path(part['cache']).resolve();p=verify_source(parent);require_aliyun(parent)
        if p['active_audio']!='audio/original.wav':raise ValueError('原片缓存须匹配原声，不能将降噪缓存映射到另一音轨')
        if abs(p['fps']-fps)>1e-6:raise ValueError('暂只支持同帧率原片，不推算变速字词')
        start,end=part['start_frame'],part['end_frame']
        if any(isinstance(n,bool) or not isinstance(n,int) for n in (start,end)) or not 0<=start<end<=int(p['duration']*fps):raise ValueError('原片范围必须有效整帧')
        if parent not in parents:
            parents[parent]=len(parents);inputs+=['-i',str(parent/p['source']),'-i',str(parent/p['active_audio'])]
        slot=parents[parent]*2
        vf=f'[{slot}:v]trim=start_frame={start}:end_frame={end},setpts=N/({fps}*TB),'
        if p['rotate']!='auto':vf+=f'transpose={p["rotate"]},'
        vf+='scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2,setsar=1'
        filters.append(vf+f'[v{i}]')
        filters.append(f'[{slot+1}:a]atrim=start={start/fps}:end={end/fps},asetpts=PTS-STARTPTS,aresample=48000[a{i}]')
        mapped.append({**part,'cache':str(parent)})
    filters.append(''.join(f'[v{i}][a{i}]' for i in range(len(parts)))+f'concat=n={len(parts)}:v=1:a=1[v][a]')
    job.mkdir(parents=True,exist_ok=True);assembled=job/'assembled-source.mov'
    script=job/'assembly-filter.txt';script.write_text(';'.join(filters),encoding='utf-8')
    ff([*inputs,'-filter_complex_threads','1','-filter_complex_script',script,'-map','[v]','-map','[a]',
        '-r',fps,'-c:v','libx264','-crf','14','-preset','fast','-threads','3','-pix_fmt','yuv420p',
        '-c:a','pcm_s24le',assembled],job)
    total=sum(x['end_frame']-x['start_frame'] for x in parts)
    info=probe(assembled);video=next(s for s in info['streams'] if s['codec_type']=='video')
    if int(video['nb_frames'])!=total:raise ValueError('真实组装帧数与映射不符')
    ingest(Namespace(job=str(job),video=str(assembled),script=args.script,title=args.title,layout='portrait',rotate='auto'))
    assembled.unlink()
    records=[]
    for part in mapped:
        parent=Path(part['cache']);dest=job/'asr/parents'/f'cache-{parents[parent]+1:03}'
        if not dest.exists():
            p=project(parent)
            for name in ('project.json',p['source'],p['active_audio'],'asr/provenance.json','asr/transcription.json','asr/words.json'):
                target=dest/name;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(parent/name,target)
        records.append({**part,'cache':str(dest.relative_to(job)),'provenance_sha256':digest(dest/'asr/provenance.json')})
    write(job/'asr/assembly.json',{'version':1,'fps':fps,'total_frames':total,'parts':records,
        'note':'原速整帧组装；只映射已有原片真实转写，不是新云端识别；边界截词保留标记供独立审核。'})
    words=mapped_words(mapped,fps);write(job/'asr/words.json',{'unit':'milliseconds','words':words})
    sentences=[]
    for w in words:
        if not sentences or sentences[-1]['sentence_id']!=w['sentence_id']:
            sentences.append({'sentence_id':w['sentence_id'],'begin_time':w['begin_time'],'end_time':w['end_time'],'text':w['text']+w.get('punctuation','')})
        else:
            sentences[-1]['end_time']=w['end_time'];sentences[-1]['text']+=w['text']+w.get('punctuation','')
    write(job/'asr/transcription.json',{'mode':MODE,'transcripts':[{'sentences':sentences}],
        'note':'原片缓存按确定性组装映射派生的文本记录；原始云端返回保存在parents内。'})
    p=project(job)
    write(job/'asr/provenance.json',{'provider':'derived','mode':MODE,'source_sha256':p['source_sha256'],
        'audio_sha256':digest(job/p['active_audio']),'raw_sha256':digest(job/'asr/transcription.json'),
        'words_sha256':digest(job/'asr/words.json'),'assembly_sha256':digest(job/'asr/assembly.json')})
    require_aliyun(job)
    print(f'离线组装完成：{total}真实帧，{len(words)}个原片缓存映射字词；尚待音频质检与独立审片。')
