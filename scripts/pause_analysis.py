"""Cut measured very-low-volume intervals under the user-authorized policy."""
from pathlib import Path
import re
from common import digest, exe, project, read, write, ff, unlocked


def detect_pauses(audio, minimum=.12):
    detections=[]
    for threshold in (-30,-35,-40):
        # -35 dBFS is the cutting threshold; other thresholds are diagnostic only.
        import subprocess
        result=subprocess.run([exe('ffmpeg'),'-hide_banner','-nostdin','-i',str(audio),
            '-af',f'silencedetect=noise={threshold}dB:d={minimum}',
            '-vn','-f','null','-'],capture_output=True,text=True,check=True)
        start=None
        for line in result.stderr.splitlines():
            left=re.search(r'silence_start: ([0-9.]+)',line)
            right=re.search(r'silence_end: ([0-9.]+)',line)
            if left:start=float(left[1])
            if right and start is not None:
                end=float(right[1])
                if end-start>=minimum-1e-4:
                    detections.append({'start':start,'end':end,'threshold_db':threshold})
                start=None
    clusters=[]
    for interval in sorted(detections,key=lambda x:x['start']):
        if clusters and interval['start']<min(x['end'] for x in clusters[-1]):clusters[-1].append(interval)
        else:clusters.append([interval])
    intervals=[]
    for group in clusters:
        primary=next((x for x in group if x['threshold_db']==-35),None)
        if primary is None:continue
        intervals.append({'id':f'a{len(intervals)+1:04}', 'start':primary['start'],
            'end':primary['end'],'thresholds_db':sorted(x['threshold_db'] for x in group),
            'observations':group,'confidence':'cross_threshold' if len(group)>1 else 'single_threshold'})
    return intervals


def analyze_audio(job,words):
    job=Path(job);p=project(job);audio=job/p['active_audio']
    intervals=detect_pauses(audio)
    for item in intervals:
        nearby=[w for w in words if w['begin_time']/1000<item['end']+.5 and w['end_time']/1000>item['start']-.5]
        item['context']=''.join(w['text'] for w in nearby)
        item['overlaps_asr_word']=any(w['begin_time']/1000<item['end'] and w['end_time']/1000>item['start'] for w in nearby)
    report={'audio_sha256':digest(audio),'minimum_source_seconds':.12,'policy':'low-volume-cut-v1','low_volume_threshold_dbfs':-35,
        'note':'用户授权按音量剪辑：峰值低于-35dBFS且持续至少0.12秒的区间可整段删除，不保留尾音、起音或40ms余量；-30/-40dB仅供对照。',
        'intervals':intervals}
    write(job/'qc/pauses.json',report)
    return report


def verified_low_volume_cut(job,c,start,end):
    """Verify audio identity and measured bounds; no speech-free approval or padding."""
    job=Path(job);path=job/'qc/pauses.json'
    if not path.exists():return False
    report=read(path);p=project(job);data=read(job/'decisions.json')
    if report.get('policy')!='low-volume-cut-v1' or report.get('low_volume_threshold_dbfs')!=-35:return False
    if data.get('acoustic_sha256')!=digest(path) or report['audio_sha256']!=digest(job/p['active_audio']):return False
    item=next((x for x in report['intervals'] if x['id']==c.get('acoustic_id')),None)
    return bool(item and item['start']-1e-6<=start<end<=item['end']+1e-6)


def check_rhythm(a):
    job=Path(a.job).resolve();unlocked(job);p=project(job);t=read(job/'timeline.json');fps=t['fps']
    # Voice-only preview avoids music/effects hiding a residual pause.
    filters=[];labels=[]
    for i,s in enumerate(t['segments']):
        start=s['source_start_frame']/fps;end=s['source_end_frame']/fps
        duration=s['frames']/fps;speed=(end-start)/duration
        filters.append(f'[0:a]atrim=start={start}:end={end},asetpts=PTS-STARTPTS,atempo={speed},apad,atrim=duration={duration}[a{i}]')
        labels.append(f'[a{i}]')
    filters.append(''.join(labels)+f'concat=n={len(labels)}:v=0:a=1[out]')
    target=job/'previews/rhythm-voice.wav'
    ff(['-i',job/p['active_audio'],'-filter_complex',';'.join(filters),'-map','[out]','-c:a','pcm_s16le',target],job)
    intervals=detect_pauses(target,.12)
    buffers=t.get('source_buffers',{})
    for item in intervals:
        item['source_buffer_boundary']=any(left<=item['start']<item['end']<=right+.001
            for left,right in (buffers.get('head_output_range',[0,0]),buffers.get('tail_output_range',[0,0])))
    write(job/'previews/rhythm.json',{'timeline_sha256':digest(job/'timeline.json'),
        'voice_sha256':digest(target),'remaining_pauses':[dict(x,reviewed=False,classification='',reason='') for x in intervals],
        'whole_track_reviewed':False,'note':''})
    print(f'生成纯人声节奏预览及 {len(intervals)} 处剩余停顿候选；按原片/成片秒数区别复核，填写rhythm.json后锁轨')
