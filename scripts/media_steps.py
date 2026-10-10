import array
from datetime import datetime
from fractions import Fraction
import json
import math
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys

from common import SKILL, digest, exe, ff, probe, project, read, require_script, run, unlocked, verify_source, write


def doctor(a):
    found={'python_3_11_plus':sys.version_info>=(3,11)};paths={};details={}
    for name in ('ffmpeg','ffprobe'):
        try:paths[name]=exe(name);run([paths[name],'-version']);found[name]=True
        except ValueError:found[name]=False
    if found['ffmpeg']:
        filters=run([paths['ffmpeg'],'-hide_banner','-filters'])
        encoders=run([paths['ffmpeg'],'-hide_banner','-encoders'])
        for name in ['afftdn','astats','silencedetect','loudnorm','ass','sidechaincompress',
                     'zoompan','eq','colorbalance','alimiter','amix','apad','adelay','aresample']:
            found['filter:'+name]=bool(re.search(r'\b'+name+r'\b',filters))
        for name in ['libx264','aac','libmp3lame','pcm_s16le','pcm_s24le','mjpeg','png']:
            found['encoder:'+name]=bool(re.search(r'\b'+name+r'\b',encoders))
    for name in ['requests','dotenv','requests_toolbelt','PIL']:
        try:__import__(name);found[name]=True
        except ImportError:found[name]=False
    font=getattr(a,'font','Smiley Sans Oblique')
    try:
        from visual_assets import font_metrics
        f,height=font_metrics(font)
        details['font']={'requested':font,'resolved':str(f.path),'family':f.getname()[0]}
        found['font_metrics']=height>0
        # fc-match can silently fall back to a font that lacks Chinese: reject name fallback.
        found['requested_font']=Path(font).is_file() or font.casefold() in f.getname()[0].casefold() or (font=='Smiley Sans Oblique' and f.getname()[0]=='Smiley Sans')
    except (ValueError,ImportError,OSError):found['font_metrics']=False
    report={'python':sys.executable,'python_version':sys.version.split()[0],
        'programs':paths,'capabilities':found,'details':details,'missing':[k for k,v in found.items() if not v],
        'api_check':'离线检查，不验证API余额、权限或连通性'}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not all(found.values()):raise ValueError('依赖缺失：'+', '.join(report['missing'])+'；见 references/dependencies-and-errors.md')


def ingest(a):
    require_script(a.script)
    job = Path(a.job).resolve()
    if (job / 'project.json').exists():
        raise ValueError('任务已存在；请使用新的任务目录')
    source = Path(a.video).resolve()
    info = probe(source)
    video = next((s for s in info['streams'] if s['codec_type'] == 'video'), None)
    audio = next((s for s in info['streams'] if s['codec_type'] == 'audio'), None)
    if not video or not audio:
        raise ValueError('需要同时含画面和音轨的视频')
    fps = float(Fraction(video['avg_frame_rate']))
    if not 1 <= fps <= 120:
        raise ValueError('帧率不支持')
    sar = video.get('sample_aspect_ratio', '1:1')
    sar_value = float(Fraction(sar.replace(':', '/'))) if sar not in {'N/A', '0:1'} else 1
    width, height = video['width'] * sar_value, video['height']
    rotation = float(video.get('tags', {}).get('rotate', 0))
    for side in video.get('side_data_list', []):
        rotation = float(side.get('rotation', rotation))
    if round(rotation / 90) % 2:
        width, height = height, width
    if a.rotate in {'clock', 'cclock'}:
        width, height = height, width
    layout = a.layout if a.layout != 'auto' else ('portrait' if width < height else 'landscape')
    expected = 9/16 if layout == 'portrait' else 16/9
    if a.layout == 'auto' and abs(width/height - expected) > .04:
        raise ValueError('非标准横竖画幅；用 --layout 明确输出画幅，渲染时等比适配加边')
    source_hash = digest(source)
    title = re.sub(r'[^\w\u4e00-\u9fff-]+', '_', a.title).strip('_') or '口播'
    name = f'{datetime.now():%Y%m%d}_{title}_{"P" if layout == "portrait" else "L"}_001_{source_hash[:8]}{source.suffix.lower()}'
    for folder in ['media', 'audio', 'qc', 'asr', 'previews', 'subtitles', 'style', 'output', 'logs']:
        (job / folder).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, job / 'media' / name)
    shutil.copy2(Path(a.script), job / 'script.txt')
    ff(['-i', source, '-map', '0:a:0', '-vn', '-c:a', 'pcm_s24le', '-ar', '48000',
        job / 'audio/original.wav'], job)
    source_duration = float(info['format']['duration'])
    p = {'version': 1, 'title': title, 'source': 'media/' + name, 'source_sha256': source_hash,
        'original_name': source.name, 'layout': layout, 'rotate': a.rotate,
        'fps': fps, 'duration': source_duration, 'width': video['width'], 'height': video['height'],
        'sample_aspect_ratio': sar_value, 'metadata_rotation': rotation,
        'active_audio': 'audio/original.wav', 'audio_review': 'pending',
        'audio_video_start_offset': float(audio.get('start_time', 0)) - float(video.get('start_time', 0))}
    # Preserve native detail; use an exact 9:16 or 16:9 canvas without forced upscaling.
    unit = max(2, min(120, math.floor(min(width/(9 if layout=='portrait' else 16),
        height/(16 if layout=='portrait' else 9))/2)*2))
    p['output_width'] = unit*(9 if layout=='portrait' else 16)
    p['output_height'] = unit*(16 if layout=='portrait' else 9)
    write(job / 'project.json', p)
    write(job / 'qc/probe.json', info)
    filters = ([f'transpose={a.rotate}'] if a.rotate != 'auto' else []) + ['scale=480:-2']
    ff(['-ss', min(2, source_duration/2), '-i', source, '-vf', ','.join(filters),
        '-frames:v', '1', job / 'qc/orientation.jpg'], job)
    print(f'已入库 {name}；检查 qc/orientation.jpg 确认方向，随后执行 audio_qc.py')


def audio_qc(a):
    job = Path(a.job).resolve()
    p = verify_source(job)
    source = job / p['active_audio']
    result = subprocess.run([exe('ffmpeg'), '-v', 'error', '-i', str(source),
        '-ac', '1', '-ar', '16000', '-f', 's16le', '-'], capture_output=True, check=True)
    samples = array.array('h', result.stdout)
    if sys.byteorder != 'little':
        samples.byteswap()
    if not samples:
        raise ValueError('音频为空')
    windows = []
    for start in range(0, len(samples), 800):
        values = samples[start:start+800]
        rms = math.sqrt(sum(float(x)*x for x in values) / len(values)) / 32768
        windows.append({'start': start/16000, 'end': (start+len(values))/16000,
            'rms_dbfs': round(20 * math.log10(max(rms, 1e-9)), 2)})
    levels = sorted(w['rms_dbfs'] for w in windows)
    clipping = sum(abs(x) >= 32760 for x in samples) / len(samples)
    quiet = levels[int(len(levels)*.1)]
    warnings = []
    if quiet > -38:
        warnings.append('低能量窗口仍较响：检查底噪；连续讲话也可能触发，不代表已确定噪音严重')
    if clipping > .001:
        warnings.append('疑似削波：降噪无法恢复失真语音，需试听并标记无法修复的片段')
    if abs(p['audio_video_start_offset']) > .08:
        warnings.append('原片音视频起始偏移超过80ms，须先修复同步再入库')
    report = {'audio': p['active_audio'], 'audio_sha256': digest(source),
        'duration': len(samples)/16000, 'peak_dbfs': 20*math.log10(max(max(abs(x) for x in samples)/32768, 1e-9)),
        'clipped_sample_ratio': clipping, 'quiet_window_p10_dbfs': quiet,
        'speech_noise_separation': 'not_measured', 'warnings': warnings,
        'note': '能量指标只用于筛查，不能冒充SNR或语义/情绪判断；结合试听做降噪决定。', 'windows': windows}
    write(job / 'qc/audio.json', report)
    ranked = sorted(windows, key=lambda w:w['rms_dbfs'])
    starts = [0, ranked[len(ranked)//10]['start'], max(0, len(samples)/16000/2 - 3)]
    for i, start in enumerate(starts):
        ff(['-ss', max(0,start-1), '-i', source, '-t', '6', '-c:a', 'pcm_s16le', job/f'qc/listen-{i+1}.wav'], job)
    print(json.dumps({k:v for k,v in report.items() if k != 'windows'},ensure_ascii=False,indent=2))


def denoise(a):
    job = Path(a.job).resolve()
    unlocked(job)
    if (job / 'asr/provenance.json').exists():
        raise ValueError('已有转写。降噪必须在转写前完成；请新建任务版本')
    p = project(job)
    if not (job/'qc/audio.json').exists():
        raise ValueError('先执行 audio_qc.py')
    if not -80 <= a.noise_floor <= -20 or a.sample_start < 0:
        raise ValueError('noise-floor 范围 -80 至 -20 dB；sample-start 不得为负')
    if a.mode == 'bypass':
        p.update(active_audio='audio/original.wav', audio_review='accepted', audio_review_note=a.reason)
        write(job/'project.json',p)
        print('保留原始音轨；质检决定已记录')
        return
    if a.mode == 'accept':
        candidate = job/'audio/denoised.wav'
        if not candidate.exists():
            raise ValueError('请先执行 light 或 moderate，试听后再 accept')
        provenance = read(job/'qc/denoise.json')
        if (provenance['source_sha256'] != digest(job/'audio/original.wav')
                or provenance['candidate_sha256'] != digest(candidate)):
            raise ValueError('降噪原始音轨或候选音轨已被改动，重新处理后再选择')
        p.update(active_audio='audio/denoised.wav', audio_review='accepted', audio_review_note=a.reason)
        write(job/'project.json', p)
        print('已选择降噪音轨；重新执行 audio_qc.py 检查处理后结果')
        return
    if not (job/'qc/audio.json').exists():
        raise ValueError('先执行 audio_qc.py')
    reduction = 6 if a.mode == 'light' else 12
    filters = f'afftdn=nr={reduction}:nf={a.noise_floor}:tn=1'
    before = float(probe(job/'audio/original.wav')['format']['duration'])
    # afftdn processes windows; feed a padded tail to flush its final partial window.
    # Trim back to original duration, never append silence in place of unprocessed speech.
    filters = f'apad=pad_dur=0.2,{filters},atrim=duration={before:.9f},asetpts=PTS-STARTPTS'
    ff(['-i',job/'audio/original.wav','-af',filters,'-c:a','pcm_s24le',job/'audio/denoised.wav'],job)
    after = float(probe(job/'audio/denoised.wav')['format']['duration'])
    if abs(before-after) > .001:
        raise ValueError('降噪改变音轨时长；不允许选择此结果')
    write(job/'qc/denoise.json',{'filter':filters,'before_duration':before,'after_duration':after,
        'source_sha256':digest(job/'audio/original.wav'),'candidate_sha256':digest(job/'audio/denoised.wav'),
        'status':'awaiting_listening_review','reason':a.reason})
    for name in ['original','denoised']:
        ff(['-ss',a.sample_start,'-i',job/f'audio/{name}.wav','-t','8','-c:a','pcm_s16le',
            job/f'qc/ab-{name}.wav'],job)
    print('已生成降噪候选和 qc/ab-*.wav；对比后执行 --mode accept 或 --mode bypass。尚未切换音轨。')
