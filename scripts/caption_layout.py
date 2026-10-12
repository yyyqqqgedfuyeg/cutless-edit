"""Bounded single-line sizing and phrase-aware splits at real word boundaries."""
import re
from inline_emphasis import emphasized_text, script_emphasis

# Protect complete numeric expressions, including dates and their units.
NUMBER = r'[0-9０-９一二三四五六七八九十百千万两零〇]+'
ATOMIC = re.compile(rf'(?:{NUMBER}年)?{NUMBER}月(?:{NUMBER}[日号])?|{NUMBER}(?:[.．]{NUMBER})?(?:%|％|届|岁|个|家|人|倍|元|万|亿|天|日|号|秒|分钟|小时)')


def fit_caption(text, spans, size, layout, width):
    minimum=layout.get('min_font_scale',.82)
    if not .75<=minimum<=1:raise ValueError('最小字幕字号比例需为0.75到1')
    available=width*.9-2*layout.get('background_padding',12)
    decorated,box=emphasized_text(text,spans,size,layout)
    scale=min(1,available/max(box[0],1))
    if scale<minimum-1e-6:
        raise ValueError(f'单行字幕超出安全宽度，需按语义和真实字词边界拆句：{text}')
    actual=size*scale
    decorated,box=emphasized_text(text,spans,actual,layout)
    return actual,decorated,box


def split_words(words,layout,width,size,script=''):
    """Choose balanced short cues; preserve date/term spans without invented timing."""
    text=''.join(w['text']+w.get('punctuation','') for w in words)
    forbidden=set()
    for match in ATOMIC.finditer(text):forbidden.update(range(match.start()+1,match.end()))
    for term in layout.get('protected_phrases',[]):
        for match in re.finditer(re.escape(term),text):
            forbidden.update(range(match.start()+1,match.end()))
    groups=[];chunk=[];offset=0
    for w in words:
        chunk.append(w);offset+=len(w['text'])+len(w.get('punctuation',''))
        if offset not in forbidden:
            groups.append({'start':chunk[0]['start'],'end':chunk[-1]['end'],
                'text':''.join(x['text']+x.get('punctuation','') for x in chunk)})
            chunk=[]
    if chunk:raise ValueError('字幕保护词边界异常')
    target=layout['line_chars'];max_chars=min(24,max(target+3,12))
    duration_limit=layout.get('max_duration',3.0)
    n=len(groups);cost=[float('inf')]*(n+1);choice=[None]*n;cost[n]=0
    for i in range(n-1,-1,-1):
        candidate=''
        for j in range(i,n):
            if j>i and (groups[j]['start']-groups[j-1]['end']>.45 or groups[j-1]['text'][-1:] in '。！？；'):break
            candidate+=groups[j]['text'];visible=candidate.rstrip('，。；')
            if j>i and (len(visible)>max_chars or groups[j]['end']-groups[i]['start']>duration_limit):break
            spans=script_emphasis(script,[{'id':'candidate','text':visible}])['candidate']
            try:actual,_,_=fit_caption(visible,spans,size,layout,width)
            except ValueError:break
            # Prefer punctuation and balanced clauses; avoid isolated particles and stubs.
            score=2+((len(visible)-target)/target)**2*3+(1-actual/size)*4
            if len(visible)<3:score+=5
            if candidate[-1:] in '，。！？；：':score-=1.5
            if j+1<n and groups[j+1]['text'][:1] in '的了着号日':score+=4
            value=score+cost[j+1]
            if value<cost[i]:cost[i]=value;choice[i]=j+1
    if choice[0] is None:raise ValueError('完整术语无法在最小字号排入单行，请调整基础字号或保护词')
    cues=[];i=0
    while i<n:
        end=choice[i]
        if end is None:raise ValueError('字幕无法在真实字词边界拆分')
        cues.append({'id':f's{len(cues)+1:04}','start':groups[i]['start'],'end':groups[end-1]['end'],
            'text':''.join(g['text'] for g in groups[i:end]).rstrip('，。；')})
        i=end
    return cues
