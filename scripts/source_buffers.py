"""Retain real source frames around the first/last kept speech before locking."""
from copy import deepcopy
import math


def retain_source_buffers(timeline, words, source_frames, seconds=.35):
    if not math.isfinite(seconds) or not 0 <= seconds <= 2:
        raise ValueError('原片首尾缓冲须为0至2秒')
    if timeline.get('source_buffers', {}).get('requested_seconds', 0):
        raise ValueError('时间轴已有原片缓冲，须从未加缓冲的时间轴重建')
    result=deepcopy(timeline);segments=result['segments'];fps=result['fps']
    if not segments:raise ValueError('没有保留片段')
    kept=[w for w in words if any(w['begin_time']/1000 < s['source_end_frame']/fps
        and w['end_time']/1000 > s['source_start_frame']/fps for s in segments)]
    if not kept:raise ValueError('无法定位保留内容的首尾真实字词')
    count=math.ceil(seconds*fps-1e-9)
    first=segments[0]['source_start_frame'];last=segments[-1]['source_end_frame']
    speech_start=math.floor(kept[0]['begin_time']/1000*fps)
    speech_end=math.ceil(kept[-1]['end_time']/1000*fps)
    start=min(first,max(0,speech_start-count)) if count else first
    end=max(last,min(source_frames,speech_end+count)) if count else last
    def buffer(left,right,kind):
        return {'source_start_frame':left,'source_end_frame':right,
            'source_frames':right-left,'frames':right-left,'buffer_kind':kind}
    head=first-start;tail=end-last
    if head:segments.insert(0,buffer(start,first,'head'))
    if tail:segments.append(buffer(last,end,'tail'))
    cursor=0
    for segment in segments:
        segment['output_start_frame']=cursor;cursor+=segment['frames']
    result.update(total_frames=cursor,duration=cursor/fps)
    result['source_buffers']={'requested_seconds':seconds,'source_buffer_frames':count,
        'head_added_frames':head,'tail_added_frames':tail,
        'head_source_range':[start/fps,first/fps],'tail_source_range':[last/fps,end/fps],
        'head_output_range':[0,head/fps],'tail_output_range':[(cursor-tail)/fps,cursor/fps],
        'speech_source_start':kept[0]['begin_time']/1000,'speech_source_end':kept[-1]['end_time']/1000,
        'available_head_seconds':max(0,kept[0]['begin_time']/1000-start/fps),
        'available_tail_seconds':max(0,end/fps-kept[-1]['end_time']/1000),
        'buffer_speed':1.0,'note':'原片真实连续帧及原声；首尾原速保留，正文速度不变。原片不足时不补静帧或静音。'}
    return result
