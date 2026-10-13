"""Shared, deterministic job IO. No credentials are stored in a job."""
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

SKILL = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temp.replace(path)


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def exe(name):
    path = os.getenv(name.upper() + '_BINARY') or shutil.which(name)
    if not path:
        raise ValueError(f'缺少 {name}；安装或设置 {name.upper()}_BINARY')
    return path


def run(args, log=None, cwd=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True,
        text=True, errors='replace', timeout=7200)
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with Path(log).open('a', encoding='utf-8') as out:
            out.write(json.dumps([str(a) for a in args], ensure_ascii=False) + '\n' + result.stderr + '\n')
    if result.returncode:
        raise ValueError(f'命令失败（退出码 {result.returncode}），详见 {log or "终端"}: {result.stderr[-500:]}')
    return result.stdout


def ff(args, job=None, cwd=None):
    return run([exe('ffmpeg'), '-hide_banner', '-nostdin', '-y', *args],
        Path(job) / 'logs/ffmpeg.log' if job else None, cwd)


def probe(path):
    return json.loads(run([exe('ffprobe'), '-v', 'error', '-show_streams',
        '-show_format', '-of', 'json', path]))


def project(job):
    return read(Path(job) / 'project.json')


def require_script(path):
    path=Path(path)
    if not path.is_file():raise ValueError('必须提供用户原稿文件；不能用ASR转写冒充原稿')
    text=path.read_text(encoding='utf-8-sig')
    if not any(c.isalnum() for c in text):raise ValueError('原稿不能为空、纯空白或只有标点；请提供用户原稿')
    return text


def require_aliyun(job):
    job=Path(job);p=project(job);proof=read(job/'asr/provenance.json')
    derived=proof.get('provider')=='derived' and proof.get('mode')=='derived-assembly-cache-v1'
    if not derived and (proof.get('provider')!='aliyun' or not proof.get('task_id') or not proof.get('model')):
        raise ValueError('必须先获得带阿里模型、云端任务ID及来源记录的真实转写')
    expected={'source_sha256':p['source_sha256'],'audio_sha256':digest(job/p['active_audio']),
        'raw_sha256':digest(job/'asr/transcription.json'),'words_sha256':digest(job/'asr/words.json')}
    if any(proof.get(k)!=v for k,v in expected.items()):raise ValueError('阿里转写来源记录与当前素材/音轨/转写不一致')
    if derived:
        from cached_assembly import validate_derived
        return validate_derived(job,proof)
    return proof


def unlocked(job):
    if (Path(job) / 'timeline.lock.json').exists():
        raise ValueError('时间轴已锁定；要重新剪辑请在新任务版本执行，不能修改现有锁定任务')


def verify_source(job, p=None):
    p = p or project(job)
    if digest(Path(job) / p['source']) != p['source_sha256']:
        raise ValueError('入库素材已被改动')
    return p


def lock_payload(job):
    job = Path(job)
    p = verify_source(job)
    timeline = read(job / 'timeline.json')
    binding = {'timeline': timeline, 'source': p['source_sha256'],
        'audio': digest(job / p['active_audio']), 'project': digest(job / 'project.json'),
        'preview_words': digest(job / 'asr/words.json'),
        'raw_transcription': digest(job / 'asr/transcription.json'),
        'asr_provenance': digest(job / 'asr/provenance.json'),
        'preview_srt': digest(job / 'asr/preview.srt'),
        'decisions': digest(job / 'decisions.json'), 'original_script': digest(job / 'script.txt')}
    if read(job/'decisions.json').get('acoustic_sha256'):
        binding['acoustic_analysis']=digest(job/'qc/pauses.json')
        binding['rhythm_review']=digest(job/'previews/rhythm.json')
        binding['rhythm_voice']=digest(job/'previews/rhythm-voice.wav')
    if timeline.get('peer_cut_review_policy'):
        binding['peer_proposal']=digest(job/'qc/cut-review-proposal.json')
        binding['peer_review']=digest(job/'qc/cut-peer-review.json')
    return fingerprint(binding)


def locked(job):
    job = Path(job)
    record = read(job / 'timeline.lock.json')
    if record['sha256'] != lock_payload(job):
        raise ValueError('锁定校验失败：素材、音轨、原始字幕或时间轴被改动')
    if record['review_sha256'] != digest(job / 'previews/review.json'):
        raise ValueError('锁定后的复核记录被改动')
    return read(job / 'timeline.json'), record['sha256']


def stamp(seconds, ass=False):
    unit = 100 if ass else 1000
    ticks = round(seconds * unit)
    sec, frac = divmod(ticks, unit)
    hour, sec = divmod(sec, 3600)
    minute, sec = divmod(sec, 60)
    return (f'{hour}:{minute:02}:{sec:02}.{frac:02}' if ass else
        f'{hour:02}:{minute:02}:{sec:02},{frac:03}')


def srt(path, cues):
    Path(path).write_text('\n\n'.join(f'{i}\n{stamp(c["start"])} --> {stamp(c["end"])}\n{c["text"]}'
        for i, c in enumerate(cues, 1)) + '\n', encoding='utf-8')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_ranges(ranges, duration):
    for r in ranges:
        if not all(finite(r[k]) for k in ('start', 'end')) or not 0 <= r['start'] < r['end'] <= duration + 1e-6:
            raise ValueError(f'无效时间段: {r}')


def dimensions(p):
    if 'output_width' in p:
        return p['output_width'], p['output_height']
    return (1080, 1920) if p['layout'] == 'portrait' else (1920, 1080)


def mapped_time(segment, seconds, fps):
    left=segment['source_start_frame']/fps
    source_frames=segment['source_end_frame']-segment['source_start_frame']
    ratio=segment.get('frames',source_frames)/source_frames
    return segment['output_start_frame']/fps+(seconds-left)*ratio


def mapped_word(timeline,start,end):
    # Acoustic cuts may shorten the silence included in an ASR word; keep its text.
    spans=[];fps=timeline['fps']
    for segment in timeline['segments']:
        left=max(start,segment['source_start_frame']/fps)
        right=min(end,segment['source_end_frame']/fps)
        if right>left:spans.append((mapped_time(segment,left,fps),mapped_time(segment,right,fps)))
    return (spans[0][0],spans[-1][1]) if spans else None
