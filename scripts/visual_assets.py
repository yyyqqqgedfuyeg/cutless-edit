"""Reusable typography animation and style-library lookup; no text correction logic."""
import math

from common import SKILL, read


def asset_entry(category, name):
    for item in read(SKILL/'assets/manifest.json').get(category,[]):
        if item['id']==name:return item
    raise ValueError(f'未知{category}素材：{name}')


def bounce_tags(duration, strength=.08, milliseconds=320):
    if not math.isfinite(strength) or not 0<=strength<=.15:
        raise ValueError('弹跳强度需为0–0.15')
    if not math.isfinite(milliseconds) or not 120<=milliseconds<=800:
        raise ValueError('弹跳时长需为120–800毫秒')
    if duration<.25 or strength==0:return ''
    end=round(min(milliseconds,duration*1000*.8))
    peak,settle=round(end*.38),round(end*.72)
    # Scale around the unchanged subtitle anchor: rise, overshoot, settle.
    start=100*(1-strength);high=100*(1+strength);low=100*(1-strength*.25)
    return (f'\\fscx{start:.2f}\\fscy{start:.2f}'
        f'\\t(0,{peak},\\fscx{high:.2f}\\fscy{high:.2f})'
        f'\\t({peak},{settle},\\fscx{low:.2f}\\fscy{low:.2f})'
        f'\\t({settle},{end},\\fscx100\\fscy100)')


def subtitle_animation(layout,cue):
    mode=layout.get('animation','none')
    if mode not in {'none','bounce'}:raise ValueError('字幕动画仅支持none或bounce')
    if mode=='none':return ''
    selected=layout.get('animated_cues',[])
    if selected and cue['id'] not in selected:return ''
    tags=bounce_tags(cue['end']-cue['start'],layout.get('bounce_strength',.08),layout.get('bounce_ms',320))
    return '{'+tags+'}' if tags else ''



from functools import lru_cache


@lru_cache(maxsize=64)
def font_metrics(font, bold=True):
    """Measure the chosen font using libass's nominal height convention."""
    from pathlib import Path
    import subprocess
    from PIL import ImageFont
    path=(SKILL/'assets/fonts/SmileySans-Oblique.ttf') if font in {'Smiley Sans','Smiley Sans Oblique'} else Path(font)
    index=0
    if font in {'Smiley Sans','Smiley Sans Oblique'} and not path.is_file():
        raise ValueError('缺少随包字体 assets/fonts/SmileySans-Oblique.ttf，请恢复字体文件')
    if not path.is_file():
        try:
            resolved=subprocess.check_output(['fc-match','-f','%{file}\n%{index}',
                font+(':style=Bold' if bold else '')],text=True).strip().splitlines()
            path=Path(resolved[0]);index=int(resolved[1]) if len(resolved)>1 else 0
        except (OSError,subprocess.CalledProcessError):
            raise ValueError('字体测量需要fontconfig，或在font参数提供字体文件路径')
    f=ImageFont.truetype(str(path),1000,index=index)
    ascent,descent=f.getmetrics()
    return f,ascent+descent


def text_width(text,size,font='Noto Sans CJK SC',bold=True):
    f,height=font_metrics(font,bold)
    return f.getlength(text)*size/height


def rounded_background(text,start,end,x,bottom,size,config,font='Noto Sans CJK SC',bold=True,layer=0,box_size=None):
    from common import stamp
    from finish_steps import ass_color
    if not config.get('background',False):return []
    color=ass_color(config.get('background_color','#203040CC'))
    pad=config.get('background_padding',12);radius=config.get('background_radius',12)
    if not all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=100 for v in (pad,radius)):
        raise ValueError('背景边距和圆角需为0–100像素')
    rows=text.split('\n')
    text_w,text_h=box_size or (max(text_width(row,size,font,bold) for row in rows),len(rows)*size)
    w=text_w+pad*2;h=text_h+pad*2;r=min(radius,w/2,h/2);k=.55228475
    # Four cubic Bezier quarter-circles, drawn behind the text, no outline/shadow.
    path=(f'm {r} 0 l {w-r} 0 b {w-r+k*r} 0 {w} {r-k*r} {w} {r} '
          f'l {w} {h-r} b {w} {h-r+k*r} {w-r+k*r} {h} {w-r} {h} '
          f'l {r} {h} b {r-k*r} {h} 0 {h-r+k*r} 0 {h-r} '
          f'l 0 {r} b 0 {r-k*r} {r-k*r} 0 {r} 0')
    tags=f'\\an7\\pos({x-w/2:.2f},{bottom-text_h-pad:.2f})\\bord0\\shad0\\1c{color}&\\1a&H{color[2:4]}&\\p1'
    return [f'Dialogue: {layer},{stamp(start,True)},{stamp(end,True)},Default,,0,0,0,,{{{tags}}}{path}']


def character_events(text, start, end, x, y, size, mode='char-bounce',
                     stagger_ms=90, animation_ms=300, color='#FFE082', layer=1,
                     font='Noto Sans CJK SC', bold=True, jump_ratio=.32, peak_scale=1.45):
    """Animate whole visible text in sequence, at natural measured font advances."""
    import unicodedata
    from common import stamp
    from finish_steps import ass_color, ass_text
    if mode not in {'char-bounce','char-grow'}:raise ValueError('未知逐字动画')
    if not 20<=stagger_ms<=400 or not 120<=animation_ms<=800:
        raise ValueError('逐字间隔需20–400ms，动画时长需120–800ms')
    if not .05<=jump_ratio<=.8 or not 1.1<=peak_scale<=1.8:raise ValueError('跳高需0.05–0.8字号，峰值缩放需1.1–1.8')
    if end-start<.25:raise ValueError('逐字动画至少需要250ms')
    if '\n' in text:raise ValueError('重点逐字花字仅支持单行')
    glyphs=[]
    for ch in text:
        if unicodedata.combining(ch) and glyphs:glyphs[-1]+=ch
        else:glyphs.append(ch)
    if not glyphs:return []
    # Prefix advances preserve the font's natural spacing instead of padded fixed cells.
    prefix=[text_width(''.join(glyphs[:i]),size,font,bold) for i in range(len(glyphs)+1)]
    left=x-prefix[-1]/2
    step=min(stagger_ms/1000,(end-start)*.55/max(1,len(glyphs)-1))
    lines=[]
    def emit(a,b,glyph,cx,tags=''):
        if b-a<.009:return
        base=f'\\an5\\fs{size}\\c{ass_color(color)}&'
        if '\\move' not in tags:base+=f'\\pos({cx:.2f},{y:.2f})'
        lines.append(f'Dialogue: {layer},{stamp(a,True)},{stamp(b,True)},Default,,0,0,0,,{{{base}{tags}}}{ass_text(glyph)}')
    for i,glyph in enumerate(glyphs):
        cx=left+(prefix[i]+prefix[i+1])/2
        when=start+i*step;duration=min(animation_ms/1000,(end-when)*.8)
        emit(start,when,glyph,cx)
        if mode=='char-bounce':
            apex=when+duration*.45;finish=when+duration
            high=y-size*jump_ratio
            emit(when,apex,glyph,cx,f'\\move({cx:.2f},{y:.2f},{cx:.2f},{high:.2f},0,{round((apex-when)*1000)})')
            emit(apex,finish,glyph,cx,f'\\move({cx:.2f},{high:.2f},{cx:.2f},{y:.2f},0,{round((finish-apex)*1000)})')
            emit(finish,end,glyph,cx)
        else:
            peak=round(duration*350);hold=round(duration*600);finish=round(duration*1000)
            tags=(f'\\fscx100\\fscy100\\t(0,{peak},\\fscx{peak_scale*100}\\fscy{peak_scale*100})'
                  f'\\t({hold},{finish},\\fscx100\\fscy100)')
            emit(when,end,glyph,cx,tags)
    return lines
