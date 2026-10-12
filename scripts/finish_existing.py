#!/usr/bin/env python3
"""Complete effects on a previously cut video, without rebuilding its cuts or speed."""
import argparse
import shutil
import hashlib
import subprocess
import re
from pathlib import Path
from common import digest, ff, probe, read, require_aliyun, write
from point_review import require_review
from point_effects import safe_sound_gain, validate_points, duck_point_sound
from finish_steps import video_filters, ass_header
from visual_assets import asset_entry


def finish(args):
    job = Path(args.job).resolve()
    base = Path(args.base).resolve()
    reference = Path(args.reference_video).resolve()
    captions = Path(args.subtitles).resolve()
    destination = Path(args.output).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    require_aliyun(job)
    timeline = read(job/'timeline.json')
    fps = timeline['fps']
    review = read(job/'style/point-review.json')
    events = [dict(point) for point in review['points']]
    validate_points(events, job, timeline, read(job/'asr/words.json')['words'])
    require_review(job, events)
    text_review = read(args.text_review)
    cues = read(args.captions)['cues']
    lookup = {item['id']: item for item in text_review['items']}
    if len(lookup) != len(cues) or any(cue['id'] not in lookup or any(
        cue[key] != lookup[cue['id']][key] for key in ('text', 'start', 'end')) for cue in cues):
        raise ValueError('既有字幕的逐条文字记录缺失或过期，不能补效果交付')
    manuscript = ''.join((job/'script.txt').read_text(encoding='utf-8').split())
    if any(not item.get('reference_review_reason', '').strip() or not item.get('script_quote', '').strip()
           or ''.join(item['script_quote'].split()) not in manuscript for item in lookup.values()):
        raise ValueError('既有字幕文字记录必须逐条引用当前原稿并说明文字判断')
    ass_lines = captions.read_text(encoding='utf-8-sig').splitlines()
    visible_text = [re.sub(r'\{[^}]*\}', '', line.split(',', 9)[9])
                    for line in ass_lines if line.startswith('Dialogue: 1,')]
    from finish_steps import ass_text
    if visible_text != [ass_text(cue['text']) for cue in cues]:
        raise ValueError('ASS烧录文字与当前逐条校对的captions不一致')
    info = probe(base)
    video = next(stream for stream in info['streams'] if stream['codec_type']=='video')
    if int(video['nb_frames']) != timeline['total_frames'] or abs(float(video['duration'])-timeline['duration']) > 1/fps:
        raise ValueError('无字幕基底与已剪时间轴不一致；不得用补效果改变原剪辑')
    width, height = video['width'], video['height']
    reference_video = next(stream for stream in probe(reference)['streams'] if stream['codec_type']=='video')
    if any(video[key] != reference_video[key] for key in ('width', 'height', 'nb_frames')):
        raise ValueError('用户指定字幕版与无字幕基底的画幅或帧数不同')
    def audio_hash(path):
        return hashlib.sha256(subprocess.check_output(['ffmpeg','-v','error','-i',str(path),
            '-vn','-c:a','copy','-f','adts','-'])).hexdigest()
    if audio_hash(base)!=audio_hash(reference):
        raise ValueError('用户指定字幕版与无字幕基底的声音不一致，不能替换既有剪辑')
    work = job/'completion'
    work.mkdir(exist_ok=True)
    snapshot = {'mode':'finish-existing-cut', 'base_sha256':digest(base),
        'reference_video_sha256':digest(reference),
        'subtitle_sha256':digest(captions), 'timeline_sha256':digest(job/'timeline.json'),
        'point_review_sha256':digest(job/'style/point-review.json'),
        'text_review_sha256':digest(args.text_review), 'captions_sha256':digest(args.captions)}
    write(work/'source-snapshot.json', snapshot)
    shutil.copy2(captions, work/'captions.ass')
    (work/'overlay.ass').write_text(ass_header(width,height,'Smiley Sans Oblique',30,.3), encoding='utf-8')
    voice = work/'voice.wav'
    ff(['-i',base,'-vn','-ac','2','-ar','48000','-c:a','pcm_s16le',voice],job)
    beds=[]
    for i, event in enumerate(events):
        asset = asset_entry('audio',event['sound'])
        from common import SKILL
        source = SKILL/'assets'/asset['path']
        if asset.get('sha256') and digest(source)!=asset['sha256']:
            raise ValueError('assets音效指纹与清单不一致')
        raw=work/f'sound-{i}.wav'
        ff(['-i',source,'-ac','2','-ar','48000','-c:a','pcm_s16le',raw],job)
        gain=safe_sound_gain(raw,voice,event['start'],event['end'],event['sound_gain_db'])
        aligned=work/f'aligned-{i}.wav';delay=round(event['start']*1000)
        ff(['-i',raw,'-af',f'atrim=duration={event["end"]-event["start"]},asetpts=PTS-STARTPTS,volume={gain}dB,adelay={delay}|{delay},apad,atrim=duration={timeline["duration"]}',
            '-t',timeline['duration'],'-c:a','pcm_s16le',aligned],job)
        ducked=work/f'ducked-{i}.wav'
        sound_duration=float(probe(raw)['format']['duration'])
        levels=duck_point_sound(aligned,voice,ducked,timeline['duration'],
            event['start'],min(event['end'],event['start']+sound_duration),job)
        beds.append(ducked)
        event['applied_sound']={'asset_sha256':digest(source),'gain_db':gain,'delay_ms':delay,
            'sidechain':True,'ducked_sha256':digest(ducked),'levels':levels}
    mixargs=['-i',voice]
    for bed in beds:mixargs+=['-i',bed]
    count=len(beds)+1
    inputs=''.join(f'[{i}:a]' for i in range(count))
    mixed=work/'mixed.wav'
    ff(mixargs+['-filter_complex',f'{inputs}amix=inputs={count}:duration=first:dropout_transition=0,volume={count},alimiter=limit=0.95:level=false[a]',
        '-map','[a]','-t',timeline['duration'],'-c:a','pcm_s16le',mixed],job)
    filters=video_filters({'events':events,'grade_parameters':{}},width,height,fps)
    pending=work/'completed.pending.mp4'
    ff(['-i',base,'-i',mixed,'-map','0:v:0','-map','1:a:0','-vf',','.join(filters),'-t',timeline['duration'],
        '-r',fps,'-c:v','libx264','-crf','18','-preset','fast','-threads','2','-c:a','aac','-b:a','192k',
        '-pix_fmt','yuv420p','-movflags','+faststart',pending],job,cwd=work)
    result=probe(pending);v=next(s for s in result['streams'] if s['codec_type']=='video');a=next(s for s in result['streams'] if s['codec_type']=='audio')
    if int(v['nb_frames'])!=timeline['total_frames'] or abs(float(v['duration'])-float(a['duration']))>.08:
        raise ValueError('效果成片帧数或音画时长异常')
    ff(['-v','error','-i',pending,'-f','null','-'],job)
    if any(snapshot[key]!=digest(path) for key,path in {
        'base_sha256':base,'reference_video_sha256':reference,'subtitle_sha256':captions,'timeline_sha256':job/'timeline.json',
        'point_review_sha256':job/'style/point-review.json','text_review_sha256':Path(args.text_review),
        'captions_sha256':Path(args.captions)}.items()):
        raise ValueError('渲染过程中输入发生变化，禁止交付')
    shutil.copy2(pending,destination)
    write(work/'verification.json',{'mode':'finish-existing-cut','passed':True,'source_snapshot':snapshot,
        'video_sha256':digest(destination),'frames':int(v['nb_frames']),'duration':float(v['duration']),
        'events':events,'subtitle_count':len(cues),'full_decode':True,'auditory_review_completed':False,
        'note':'既有剪辑补效果交付；不伪造原流程锁轨或听感记录。听感尚未验证。'})
    print(destination)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('job','base','reference-video','subtitles','captions','text-review','output'):
        parser.add_argument('--'+name,required=True)
    finish(parser.parse_args())
