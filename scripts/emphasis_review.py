"""Persist agent proposals and explicit user decisions without modifying the script."""
from pathlib import Path
from common import digest, read, write, require_script
from inline_emphasis import emphasis_map, SCRIPT_EMPHASIS


def binding(job):
    from caption_review import cue_hash
    return {'script_sha256':digest(job/'script.txt'),'cue_sha256':cue_hash(job)}


def has_marks(script):
    # Malformed marks are rejected by script_emphasis during subtitle generation.
    return '【' in script and '】' in script


def approved_emphasis(job, required=False):
    job=Path(job)
    if has_marks(require_script(job/'script.txt')):return None
    proposal=job/'subtitles/emphasis-proposal.json';approval=job/'subtitles/emphasis-approval.json'
    if not proposal.exists() or not approval.exists():
        if required:raise ValueError('原稿无【】标记：agent需先提交重点词方案，取得用户明确审批后再生成最终字幕')
        return None
    data=read(proposal);decision=read(approval)
    if (data.get('binding')!=binding(job) or decision.get('proposal_sha256')!=digest(proposal)
            or decision.get('decision')!='approve' or not decision.get('user_response','').strip()):
        if required:raise ValueError('重点词方案未获用户批准或已过期，请重新提交用户审批')
        return None
    cues=read(job/'subtitles/captions.json')['cues']
    result=emphasis_map(data['items'],cues)
    return {key:[{**span,'style':dict(SCRIPT_EMPHASIS)} for span in spans] for key,spans in result.items()}


def emphasis_review(a):
    job=Path(a.job).resolve()
    if has_marks(require_script(job/'script.txt')):
        raise ValueError('原稿已有【】标记，直接使用原稿重点，不另行自动划重点')
    proposal=job/'subtitles/emphasis-proposal.json'
    if getattr(a,'proposal',None):
        data=read(a.proposal)
        if not isinstance(data.get('reason'),str) or not data['reason'].strip():raise ValueError('方案需说明选择重点的理由')
        cues=read(job/'subtitles/captions.json')['cues']
        emphasis_map(data['items'],cues)
        for item in data['items']:
            for span in item['spans']:
                if set(span)-{'text','occurrence'}:raise ValueError('方案只指定重点词和出现次数，样式统一固定')
        write(proposal,{'binding':binding(job),'items':data['items'],'reason':data['reason']})
        (job/'subtitles/emphasis-approval.json').unlink(missing_ok=True)
        for ext in ('ass','srt'):(job/f'subtitles/final.{ext}').unlink(missing_ok=True)
        print('重点词方案已保存；向用户展示重点词及上下文，等待明确审批。不得由agent自行批准。')
        return
    if not proposal.exists():raise ValueError('先提交agent重点词方案')
    if read(proposal)['binding']!=binding(job):raise ValueError('字幕或原稿已变化，请重提方案')
    if not getattr(a,'decision',None) or not getattr(a,'user_response','').strip():
        raise ValueError('审批需提供approve/reject和用户实际回复；等待或默认选项不代表批准')
    write(job/'subtitles/emphasis-approval.json',{'proposal_sha256':digest(proposal),
        'decision':a.decision,'user_response':a.user_response})
    for ext in ('ass','srt'):(job/f'subtitles/final.{ext}').unlink(missing_ok=True)
    print('已记录用户决定；批准后重新运行subtitles.py，拒绝时重拟方案再审批。')
