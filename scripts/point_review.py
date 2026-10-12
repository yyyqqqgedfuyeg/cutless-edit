"""Require an explicit semantic inventory before styling or delivering a video."""
from pathlib import Path
from common import digest, read, write
from point_effects import validate_points


def binding(job):
    job = Path(job)
    return {name: digest(job / path) for name, path in {
        'script_sha256': 'script.txt', 'timeline_sha256': 'timeline.json',
        'words_sha256': 'asr/words.json'}.items()}


def record_review(job, proposal):
    job = Path(job)
    assessment = proposal.get('assessment')
    if assessment not in {'expanded_points', 'no_expanded_points'} or not proposal.get('reason', '').strip():
        raise ValueError('必须明确记录逐项讲解判定及语义理由，不能省略效果审阅')
    points = proposal.get('points')
    if not isinstance(points, list):
        raise ValueError('逐项讲解审阅必须包含points列表')
    if (assessment == 'expanded_points') != bool(points):
        raise ValueError('逐项讲解判定与观点清单不一致')
    if any(point.get('template', 'expanded-point') != 'expanded-point' for point in points):
        raise ValueError('points清单只能包含expanded-point，不能以其他事件替代')
    events = [{**point, 'template': 'expanded-point'} for point in points]
    validate_points(events, job, read(job/'timeline.json'), read(job/'asr/words.json')['words'])
    previous_end = 0
    for event in events:
        if not 0 <= event['start'] < event['end'] <= read(job/'timeline.json')['duration'] or event['start'] < previous_end:
            raise ValueError('逐项讲解时间范围无效或重叠')
        previous_end = event['end']
    report = {**proposal, 'binding': binding(job), 'points': events}
    write(job/'style/point-review.json', report)
    return report


def require_review(job, events):
    job = Path(job)
    path = job/'style/point-review.json'
    if not path.exists():
        raise ValueError('缺少逐项讲解语义审阅；先运行review_points.py，不能跳过缩放/音效阶段')
    review = read(path)
    if review.get('binding') != binding(job):
        raise ValueError('原稿、时间轴或真实字词改变，逐项讲解审阅已过期')
    expected = review['points']
    actual = [event for event in events if event['template'] == 'expanded-point']
    keys = ('sequence_group', 'point_index', 'start', 'end', 'script_quote',
            'expanded_explanation', 'sound', 'sound_gain_db', 'zoom_transition_seconds')
    if len(actual) != len(expected) or any(
        any(a.get(key) != b.get(key) for key in keys) for a, b in zip(actual, expected)):
        raise ValueError('逐项讲解事件遗漏或与语义审阅清单不一致')
    for event in actual:
        if event.get('zoom') != 1.2 or event.get('sound') not in {'ding-ding-ding', 'swish'}:
            raise ValueError('每项必须同时具有120%缩放及assets音效')
    return review


def review_points(args):
    review = record_review(args.job, read(args.review))
    print(f"逐项讲解审阅已记录：{len(review['points'])}项；每项必须同时应用120%缩放和音效")
