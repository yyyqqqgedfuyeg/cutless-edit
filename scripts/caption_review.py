"""Record agent-authored proofreading. Validate records, never judge or rewrite text."""
from pathlib import Path
import shutil

from common import digest, fingerprint, locked, read, require_aliyun, require_script, write


def cue_hash(job):
    return fingerprint([{k:c[k] for k in ('id','text','start','end')}
        for c in read(Path(job)/'subtitles/captions.json')['cues']])


def review_binding(job):
    job=Path(job)
    require_script(job/'script.txt');require_aliyun(job)
    _,lock_hash=locked(job)
    return {'lock_sha256':lock_hash,'script_sha256':digest(job/'script.txt'),
        'asr_sha256':digest(job/'asr/provenance.json'),'cue_sha256':cue_hash(job)}


def promote(job):
    job=Path(job);binding=review_binding(job)
    path=job/'subtitles/review.json'
    if not path.exists():raise ValueError('字幕尚未逐条参考原稿校对；先运行 review_subtitles.py')
    review=read(path)
    if review.get('binding')!=binding:raise ValueError('字幕文字/时间或原稿已改变，必须重新校对')
    generated=read(job/'subtitles/generated.json')
    if (generated.get('cue_sha256')!=binding['cue_sha256']
            or generated.get('layout_sha256')!=digest(job/'subtitles/layout.json')):
        raise ValueError('字幕草稿未更新；请重新运行 subtitles.py')
    for ext in ('ass','srt'):
        draft=job/f'subtitles/draft.{ext}'
        if generated.get(ext+'_sha256')!=digest(draft):raise ValueError('字幕排版文件被改动，请重新生成')
        shutil.copy2(draft,job/f'subtitles/final.{ext}')


def require_final(job):
    job=Path(job);binding=review_binding(job)
    path=job/'subtitles/review.json'
    if not path.exists() or read(path).get('binding')!=binding:
        raise ValueError('未完成参考原稿的字幕校对，或校对记录已过期；禁止导出')
    generated=read(job/'subtitles/generated.json')
    if generated.get('cue_sha256')!=binding['cue_sha256'] or generated.get('layout_sha256')!=digest(job/'subtitles/layout.json'):
        raise ValueError('字幕内容或样式尚未重新生成')
    for ext in ('ass','srt'):
        if digest(job/f'subtitles/final.{ext}')!=generated[ext+'_sha256']:
            raise ValueError('最终字幕与已生成的校对版本不一致')


def review_subtitles(a):
    job=Path(a.job).resolve();binding=review_binding(job)
    original=require_script(job/'script.txt')
    cues=read(job/'subtitles/captions.json')['cues']
    report=read(a.reviews)
    items=report['items']
    if len(items)!=len(cues) or {i['id'] for i in items}!={c['id'] for c in cues}:
        raise ValueError('必须逐条校对全部字幕，包括确认无需修改的字幕')
    lookup={i['id']:i for i in items}
    normalize=lambda s:''.join(s.split())
    for cue in cues:
        item=lookup[cue['id']]
        if any(item.get(k)!=cue[k] for k in ('text','start','end')):
            raise ValueError(f'{cue["id"]} 的校对记录与当前文字/时间不符')
        quote=item.get('script_quote','')
        if not quote.strip() or normalize(quote) not in normalize(original):
            raise ValueError(f'{cue["id"]} 必须引用原稿中实际存在的对应句或上下文')
        if item.get('decision') not in {'matches_script','asr_corrected','spoken_variant','spoken_addition'}:
            raise ValueError('校对决定无效或仍有未解决问题')
        if item.get('alignment_checked') is not True or not item.get('reason','').strip():
            raise ValueError('每条字幕必须检查与人声时间对齐并记录校对理由')
    write(job/'subtitles/review.json',{'binding':binding,'items':items,
        'reviewer':report.get('reviewer','agent')})
    promote(job)
    print(f'已接收agent对{len(cues)}条字幕的校对记录；格式及版本一致性通过，生成final.ass/srt。脚本不判断文字语义。')
