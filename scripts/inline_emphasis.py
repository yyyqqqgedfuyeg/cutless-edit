"""Apply agent-selected visual emphasis without changing subtitle text or timing."""
import math
from visual_assets import text_width


def emphasized_text(text, spans, size, layout):
    from finish_steps import ass_color, ass_text
    base={'size_scale':1,'color':layout.get('text_color','#FFFFFF'),'bold':layout.get('bold',True),
        'italic':False,'underline':False,'outline_color':layout.get('outline_color','#101010'),
        'outline_width':layout.get('outline_width',2),'shadow_color':layout.get('shadow_color','#00000080'),
        'shadow_depth':layout.get('shadow_depth',1)}
    def tags(style):
        allowed=set(base)
        if set(style)-allowed:raise ValueError('未知强调样式字段：'+','.join(sorted(set(style)-allowed)))
        value={**base,**style}
        for key in ('bold','italic','underline'):
            if not isinstance(value[key],bool):raise ValueError(key+'必须是布尔值')
        for key,lo,hi in [('size_scale',.5,2),('outline_width',0,12),('shadow_depth',0,12)]:
            v=value[key]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo<=v<=hi:
                raise ValueError(f'{key}需在{lo}–{hi}之间')
        def color(key,channel):
            c=ass_color(value[key]);return f'\\{channel}c{c}&\\{channel}a&H{c[2:4]}&'
        result=(f'\\fs{size*value["size_scale"]:.2f}\\b{int(value["bold"])}'
            f'\\i{int(value["italic"])}\\u{int(value["underline"])}'
            f'\\bord{value["outline_width"]}\\shad{value["shadow_depth"]}'
            +color('color',1)+color('outline_color',3)+color('shadow_color',4))
        return '{'+result+'}',value
    intervals=[]
    for span in spans:
        if set(span)-{'text','occurrence','style'}:raise ValueError('强调项仅支持text、occurrence、style')
        phrase=span.get('text','');occurrence=span.get('occurrence',1)
        if not phrase or '\n' in phrase:raise ValueError('强调词不能为空或跨行')
        if isinstance(occurrence,bool) or not isinstance(occurrence,int) or occurrence<1:
            raise ValueError('occurrence从1开始')
        start=-len(phrase)
        for _ in range(occurrence):
            start=text.find(phrase,start+len(phrase))
            if start<0:raise ValueError('当前字幕中找不到指定强调词或出现次数：'+phrase)
        tag,value=tags(span.get('style',{}))
        intervals.append((start,start+len(phrase),tag,value))
    intervals.sort(key=lambda item:item[0])
    for left,right in zip(intervals,intervals[1:]):
        if left[1]>right[0]:raise ValueError('同一字幕的强调词区间不能重叠')
    reset,base=tags({});runs=[];cursor=0
    for start,end,tag,value in intervals:
        runs.append((text[cursor:start],reset,base))
        runs.append((text[start:end],tag,value));cursor=end
    runs.append((text[cursor:],reset,base))
    encoded=[];widths=[0.];heights=[float(size)]
    for fragment,tag,value in runs:
        if not fragment:continue
        encoded.append(tag+ass_text(fragment))
        for i,part in enumerate(fragment.split('\n')):
            if i:widths.append(0.);heights.append(float(size))
            if part:
                actual=size*value['size_scale']
                widths[-1]+=text_width(part,actual,layout['font'],value['bold'])*(1.08 if value['italic'] else 1)
                heights[-1]=max(heights[-1],actual)
    return ''.join(encoded),(max(widths),sum(heights))


def emphasis_map(items,cues):
    lookup={c['id']:c for c in cues};result={}
    if not isinstance(items,list):raise ValueError('强调配置必须是JSON列表')
    for item in items:
        if item.get('id') not in lookup or item['id'] in result:raise ValueError('强调配置含未知或重复字幕id')
        if item.get('text')!=lookup[item['id']]['text']:
            raise ValueError('强调配置text必须与当前字幕完全一致；文字校对后需重新检查强调词')
        if not isinstance(item.get('spans'),list):raise ValueError('强调配置需要spans列表')
        result[item['id']]=item['spans']
    return result
