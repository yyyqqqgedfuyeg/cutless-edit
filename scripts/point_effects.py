"""Validate agent-reviewed expanded explanations and build fixed-centre zooms."""
import math
import re
from common import require_script

POINT_SOUNDS={'ding-ding-ding','swish'}


def validate_points(events, job, timeline, words):
    point_events=[e for e in events if e['template']=='expanded-point']
    groups={}
    clean=lambda text:re.sub(r'[\s【】]','',text)
    script=clean(require_script(job/'script.txt'))
    starts=[];fps=timeline['fps']
    for word in words:
        start,end=word['begin_time']/1000,word['end_time']/1000
        for segment in timeline['segments']:
            left,right=segment['source_start_frame']/fps,segment['source_end_frame']/fps
            if left-.0001<=start and end<=right+.0001:
                starts.append(start+segment['output_start_frame']/fps-left);break
    for e in events:
        if e.get('sound') in POINT_SOUNDS and e['template']!='expanded-point':
            raise ValueError('叮叮叮/唰仅用于逐项展开讲解，不能用于普通重点事件')
    for e in point_events:
        index=e.get('point_index');group=e.get('sequence_group','')
        if not isinstance(group,str) or not group.strip() or isinstance(index,bool) or not isinstance(index,int) or index<1:
            raise ValueError('逐项讲解需sequence_group及从1开始的point_index')
        if e.get('expanded_explanation') is not True or not e.get('reason','').strip():
            raise ValueError('agent须确认每项独立展开讲解并记录理由；一句话枚举不触发')
        quote=clean(e.get('script_quote',''))
        if not quote or quote not in script:raise ValueError('逐项讲解事件必须引用原稿的对应观点及展开说明')
        if e['end']-e['start']<1:raise ValueError('一句简短枚举不触发逐项讲解效果')
        if not any(abs(e['start']-start)<=max(.04,1/fps) for start in starts):
            raise ValueError('逐项讲解效果起点需对齐锁定后真实字词起点')
        if e.get('sound') not in POINT_SOUNDS:raise ValueError('逐项讲解选择叮叮叮或唰，优先叮叮叮')
        e['zoom']=1.2
        transition=e.get('zoom_transition_seconds',.25)
        if isinstance(transition,bool) or not isinstance(transition,(int,float)) or not math.isfinite(transition) or not .08<=transition<=1:
            raise ValueError('平滑推进时长需0.08–1秒')
        e['zoom_transition_seconds']=min(transition,(e['end']-e['start'])/2)
        e['sound_gain_db']=e.get('sound_gain_db',-24)
        if not -40<=e['sound_gain_db']<=-18:raise ValueError('强调音效增益需-40至-18dB，并自动避让人声')
        groups.setdefault(group,[]).append(e)
    for group,items in groups.items():
        indices=[e['point_index'] for e in items]
        if len(items)<2 or indices!=sorted(set(indices)):
            raise ValueError('逐项讲解需至少两个按顺序展开的不同观点，不触发孤立序号或简单枚举')


def zoom_expression(events,fps):
    expression='1'
    for e in events:
        t=f'on/{fps}';start=e['start'];end=e['end'];amount=e['zoom']-1
        if e['template']=='expanded-point':
            progress=f'min(1,max(0,({t}-{start})/{e["zoom_transition_seconds"]}))'
            # Smoothstep to 120%, hold, then reset on the first frame at/after end.
            expression+=f'+if(gte({t},{start})*lt({t},{end}),{amount}*({progress})*({progress})*(3-2*({progress})),0)'
        else:
            expression+=f'+if(gte({t},{start})*lt({t},{end}),{amount}*sin(PI*({t}-{start})/{end-start}),0)'
    return expression


def safe_sound_gain(sound_path,voice_path,start,end,requested_db):
    """Bound effect RMS and peak below speech in the corresponding spoken window."""
    import array
    import wave
    def levels(path,left=0,right=None):
        with wave.open(str(path),'rb') as source:
            if source.getsampwidth()!=2:raise ValueError('音效混音测量需16位PCM')
            rate=source.getframerate()
            source.setpos(min(source.getnframes(),round(left*rate)))
            samples=array.array('h',source.readframes(source.getnframes() if right is None else max(0,round((right-left)*rate))))
            import sys
            if sys.byteorder!='little':samples.byteswap()
            if not samples:return 0.,0.
            return math.sqrt(sum(v*v for v in samples)/len(samples))/32768,max(abs(v) for v in samples)/32768
    effect_rms,effect_peak=levels(sound_path)
    voice_rms,voice_peak=levels(voice_path,start,end)
    if not effect_peak:return requested_db
    ceilings=[10**(requested_db/20),voice_peak*.4/effect_peak]
    if effect_rms:ceilings.append(voice_rms*.25/effect_rms)
    return max(-100,20*math.log10(max(1e-5,min(ceilings))))
