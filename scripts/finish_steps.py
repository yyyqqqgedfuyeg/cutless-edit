import math
import re
from pathlib import Path
import shutil
import tempfile
import wave

from inline_emphasis import emphasized_text, emphasis_map, script_emphasis, single_line_layout

from common import SKILL, check_ranges, digest, dimensions, ff, fingerprint, locked, probe, project, read, srt, stamp, write
from point_effects import validate_points, zoom_expression, safe_sound_gain
from emphasis_review import approved_emphasis
from caption_review import cue_hash, promote, require_final, review_binding
from visual_assets import asset_entry, bounce_tags, subtitle_animation, character_events, rounded_background


def ass_text(text):
    return text.replace('\\','／').replace('{','（').replace('}','）').replace('\n','\\N')


def ass_color(value):
    if not isinstance(value,str) or not re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?',value):
        raise ValueError('颜色需为 #RRGGBB 或 #RRGGBBAA；AA为不透明度，FF完全不透明')
    rgb=value[1:7].upper()
    alpha=255-int(value[7:9],16) if len(value)==9 else 0
    return f'&H{alpha:02X}{rgb[4:6]}{rgb[2:4]}{rgb[0:2]}'


def ass_header(width,height,font,size,bottom,color='#FFFFFF',outline_color='#101010',
               outline_width=2,shadow_color='#00000080',shadow_depth=1,bold=True):
    color,outline_color,shadow_color=map(ass_color,(color,outline_color,shadow_color))
    for value in (outline_width,shadow_depth):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=12:
            raise ValueError('描边粗细和阴影偏移需为0到12之间的像素值')
    if not isinstance(bold,bool):raise ValueError('bold必须为true或false')
    return f'''[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{size},{color},&H000000FF,{outline_color},{shadow_color},{-1 if bold else 0},0,0,0,100,100,0,0,1,{outline_width},{shadow_depth},2,20,20,{round(height*(1-bottom))},1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''


def wrap(text,limit,terms):
    if '\n' in text:
        lines=text.split('\n')
        if len(lines)>2 or any(len(line)>limit for line in lines):raise ValueError('字幕超过两行或行长限制')
        return text
    if len(text)<=limit:return text
    if len(text)>limit*2:raise ValueError(f'字幕过长，请先拆句：{text}')
    forbidden=set()
    for term in terms:
        pos=text.find(term)
        while pos>=0:
            forbidden.update(range(pos+1,pos+len(term)))
            pos=text.find(term,pos+1)
    options=[i for i in range(max(1,len(text)-limit),min(limit,len(text)-1)+1) if i not in forbidden]
    if not options:raise ValueError('术语无法完整排入两行，请缩短字幕或调整行长')
    cut=min(options,key=lambda i:abs(len(text)/2-i)-(3 if text[i-1] in '，、； ' else 0))
    return text[:cut]+'\n'+text[cut:]


def subtitles(a):
    job=Path(a.job).resolve();timeline,lock_hash=locked(job);p=project(job)
    from common import require_aliyun, require_script
    require_script(job/'script.txt');require_aliyun(job)
    profile=read(SKILL/f'assets/profiles/{p["layout"]}.json');fps=timeline['fps']
    layout={**profile['subtitle'],**(read(job/'subtitles/layout.json') if (job/'subtitles/layout.json').exists() else {})}
    if layout.get('subtitle_policy')!='manuscript-single-line-v1':
        layout['font_size_ratio']=profile['subtitle']['font_size_ratio']
        layout['line_chars']=profile['subtitle']['line_chars']
    # Upgrade previous defaults while retaining deliberate font/size overrides.
    if layout.get('subtitle_style')!='short-video-v2':
        if layout.get('font')=='Noto Sans CJK SC':layout['font']=profile['subtitle']['font']
        if layout.get('font_size_ratio') in (.156,.072):
            layout['font_size_ratio']=profile['subtitle']['font_size_ratio']
        if layout.get('background_radius',0)==0:
            layout['background_radius']=profile['subtitle']['background_radius']
    layout['subtitle_style']='short-video-v2'
    layout['subtitle_policy']='manuscript-single-line-v1'
    for key,arg in [('bottom_y_ratio','bottom'),('font_size_ratio','font_size'),('font','font'),('line_chars','line_chars'),
                    ('text_color','text_color'),('outline_color','outline_color'),('outline_width','outline_width'),
                    ('shadow_color','shadow_color'),('shadow_depth','shadow_depth'),('bold','bold'),
                    ('animation','animation'),('bounce_strength','bounce_strength'),('bounce_ms','bounce_ms'),
                    ('background','background'),('background_color','background_color'),
                    ('background_radius','background_radius'),('background_padding','background_padding')]:
        if getattr(a,arg,None) is not None:layout[key]=getattr(a,arg)
    if getattr(a,'animated_cues',None) is not None:
        layout['animated_cues']=[x.strip() for x in a.animated_cues.split(',') if x.strip()]
    layout=single_line_layout(layout)
    if not .5<=layout['bottom_y_ratio']<=.96 or not .015<=layout['font_size_ratio']<=.27 or not 5<=layout['line_chars']<=40:
        raise ValueError('字幕位置/字号/行长超出允许范围')
    if a.terms:layout['protected_phrases']=read(a.terms)
    captions_path=job/'subtitles/captions.json'
    if captions_path.exists():
        saved=read(captions_path)
        if saved['lock_sha256']!=lock_hash:raise ValueError('字幕属于另一时间轴')
        cues=saved['cues']
    else:
        mapped=[]
        for word in read(job/'asr/words.json')['words']:
            start,end=word['begin_time']/1000,word['end_time']/1000
            for segment in timeline['segments']:
                left,right=segment['source_start_frame']/fps,segment['source_end_frame']/fps
                if left-.0001<=start and end<=right+.0001:
                    offset=segment['output_start_frame']/fps-left
                    mapped.append({'start':start+offset,'end':end+offset,
                        'text':word['text'],'punctuation':word.get('punctuation','')})
                    break
        if not mapped:raise ValueError('锁定轨道中没有完整字词')
        width,height=dimensions(p);size=round(width*layout['font_size_ratio'])
        cues=[];chunk=[]
        original=(job/'script.txt').read_text(encoding='utf-8')
        def fits(words):
            candidate={'id':'candidate','text':''.join(w['text']+w['punctuation'] for w in words).rstrip('，。；')}
            spans=script_emphasis(original,[candidate])['candidate']
            box=emphasized_text(candidate['text'],spans,size,layout)[1]
            return box[0]+2*layout.get('background_padding',12)<=width*.9
        def flush():
            if chunk:
                cues.append({'id':f's{len(cues)+1:04}','start':chunk[0]['start'],
                    'end':chunk[-1]['end'],'text':''.join(w['text']+w['punctuation'] for w in chunk).rstrip('，。；')})
                chunk.clear()
        for w in mapped:
            length=sum(len(x['text'])+len(x['punctuation']) for x in chunk)
            if chunk and (not fits(chunk+[w]) or length+len(w['text'])+len(w['punctuation'])>layout['line_chars'] or w['end']-chunk[0]['start']>5
                    or w['start']-chunk[-1]['end']>.7):flush()
            chunk.append(w)
            if w['punctuation'] in {'。','！','？','；'}:flush()
        flush()
    if a.corrections:
        lookup={c['id']:c for c in cues}
        for correction in read(a.corrections):
            if correction['id'] not in lookup or not correction.get('reason','').strip():
                raise ValueError('校对项需要有效id和reason，不能修改只读预览字幕')
            c=lookup[correction['id']]
            c.setdefault('baseline_start',c['start'])
            c.setdefault('baseline_end',c['end'])
            for key in ['start','end']:
                if key in correction:
                    if abs(correction[key]-c['baseline_'+key])>.25:raise ValueError('字幕校时超过250ms，请核查对齐；不允许移动剪辑轨道')
                    c[key]=correction[key]
            c.update(text=correction['text'],correction_reason=correction['reason'])
    check_ranges(cues,timeline['duration'])
    if len({c['id'] for c in cues})!=len(cues):raise ValueError('字幕id重复')
    if set(layout.get('animated_cues',[]))-{c['id'] for c in cues}:raise ValueError('弹跳字幕列表中存在未知cue id')
    for left,right in zip(cues,cues[1:]):
        if left['end']>right['start']+.0001:raise ValueError('字幕时间重叠或顺序错误')
    width,height=dimensions(p);size=round(width*layout['font_size_ratio'])
    emphasis=approved_emphasis(job)
    if emphasis is None:emphasis=script_emphasis((job/'script.txt').read_text(encoding='utf-8'),cues)
    layout['approved_emphasis']=emphasis
    lines=[];final=[]
    for cue in cues:
        spans=emphasis.get(cue['id'],[])
        text=cue['text'].replace('\r','').replace('\n','')
        decorated,box=emphasized_text(text,spans,size,layout)
        if box[0]+2*layout.get('background_padding',12)>width*.9:
            raise ValueError(f'{cue["id"]} 单行字幕超出安全宽度，请按真实字词边界重新拆句')
        animation=subtitle_animation(layout,cue)
        lines.extend(rounded_background(text,cue['start'],cue['end'],width/2,
            round(height*layout['bottom_y_ratio']),size,layout,layout['font'],layout.get('bold',True),box_size=box))
        lines.append(f'Dialogue: 1,{stamp(cue["start"],True)},{stamp(cue["end"],True)},Default,,0,0,0,,{animation}{decorated}')
        final.append({**cue,'text':text})
    header=ass_header(width,height,layout['font'],size,layout['bottom_y_ratio'],
        color=layout.get('text_color','#FFFFFF'),outline_color=layout.get('outline_color','#101010'),
        outline_width=layout.get('outline_width',2),shadow_color=layout.get('shadow_color','#00000080'),
        shadow_depth=layout.get('shadow_depth',1),bold=layout.get('bold',True))
    (job/'subtitles/draft.ass').write_text(header+'\n'.join(lines)+'\n',encoding='utf-8')
    srt(job/'subtitles/draft.srt',final)
    write(captions_path,{'lock_sha256':lock_hash,'cues':cues})
    write(job/'subtitles/layout.json',layout)
    write(job/'subtitles/generated.json',{'cue_sha256':cue_hash(job),
        'layout_sha256':digest(job/'subtitles/layout.json'),
        'ass_sha256':digest(job/'subtitles/draft.ass'),'srt_sha256':digest(job/'subtitles/draft.srt')})
    locked(job)
    review_path=job/'subtitles/review.json'
    if review_path.exists() and read(review_path).get('binding')==review_binding(job):
        promote(job)
        print('已保留逐条校对结果并更新最终字幕样式')
    else:
        for ext in ('ass','srt'):(job/f'subtitles/final.{ext}').unlink(missing_ok=True)
        print(f'生成 {len(cues)} 条待校对字幕 draft.ass/srt；逐条对照原稿后运行 review_subtitles.py')


def style(a):
    job=Path(a.job).resolve();timeline,lock_hash=locked(job);p=project(job)
    events=read(a.events) if a.events else []
    profile=read(SKILL/f'assets/profiles/{p["layout"]}.json')
    grade_name=profile['style_defaults']['grade'] if a.grade=='auto' else a.grade
    grade_parameters={} if grade_name=='none' else read(SKILL/'assets'/asset_entry('grades',grade_name)['path'])['parameters']
    manifest=read(SKILL/'assets/manifest.json')
    presets={x['id']:read(SKILL/'assets'/x['path']) for x in manifest['templates']}
    check_ranges(events,timeline['duration'])
    events=sorted(events,key=lambda e:e['start'])
    validate_points(events,job,timeline,read(job/'asr/words.json')['words'])
    for i,event in enumerate(events):
        template=presets.get(event['template'])
        if not template:raise ValueError('未知特效模板')
        if i and events[i-1]['end']>event['start']:raise ValueError('重点效果不能重叠，避免同屏竞争')
        zoom=event.get('zoom',profile['emphasis_zoom'])
        if event['template']!='expanded-point' and not 1<=zoom<=1.08:
            raise ValueError('普通缩放必须在1.00至1.08之间；逐项展开讲解固定1.20')
        max_chars=min(18,math.floor(.82/profile['emphasis_font_ratio']))
        if len(event.get('text',''))>max_chars:raise ValueError(f'当前画幅重点文案最多{max_chars}字，避免越界')
        event.update(zoom=zoom,template_parameters=template)
        if event.get('sound'):
            entry=asset_entry('audio',event['sound'])
            target=job/'style'/('sfx-'+entry['id']+Path(entry['path']).suffix)
            shutil.copy2(SKILL/'assets'/entry['path'],target)
            gain=event.get('sound_gain_db',-18)
            if not -40<=gain<=-6:raise ValueError('音效增益需在-40至-6dB')
            event['sound_asset']={'file':str(target.relative_to(job)),'sha256':digest(target),'gain_db':gain}
    bgm=None
    preset_name=getattr(a,'bgm_preset',None)
    if preset_name and a.bgm:raise ValueError('--bgm和--bgm-preset只能指定一个')
    bgm_input,bgm_source=a.bgm,a.bgm_source
    if preset_name:
        preset_name=profile['style_defaults']['bgm'] if preset_name=='auto' else preset_name
        entry=asset_entry('bgm',preset_name)
        bgm_input=SKILL/'assets'/entry['path'];bgm_source=entry['origin']+' / '+entry['id']
    if bgm_input:
        if not bgm_source:raise ValueError('BGM需要 --bgm-source 记录来源/授权说明')
        target=job/'style'/('bgm'+Path(bgm_input).suffix.lower())
        shutil.copy2(bgm_input,target)
        bgm={'file':str(target.relative_to(job)),'sha256':digest(target),'source':bgm_source,
            'gain_db':a.bgm_gain}
    if not -40<=a.bgm_gain<=-6:raise ValueError('BGM增益范围 -40 到 -6 dB')
    width,height=dimensions(p);lines=[]
    font=read(job/'subtitles/layout.json')['font']
    (job/'style/overlay.ass').write_text(ass_header(width,height,font,round(width*profile['emphasis_font_ratio']),.3,'#FFDF66')+'\n'.join(lines)+'\n',encoding='utf-8')
    write(job/'style/style.json',{'lock_sha256':lock_hash,'events':events,'bgm':bgm,
        'grade':grade_name,'grade_parameters':grade_parameters})
    locked(job)
    print(f'已生成 {len(events)} 个画面/声音事件；文字仅使用原稿标记的字幕强调')


def video_filters(style_data,width,height,fps):
    """Scale the picture first; burn subtitles at their unchanged output size."""
    vf=[]
    grade=style_data.get('grade_parameters',{})
    if grade:
        vf.append(f'eq=contrast={grade.get("contrast",1)}:saturation={grade.get("saturation",1)}:brightness={grade.get("brightness",0)}')
        if 'red' in grade or 'blue' in grade:
            vf.append(f'colorbalance=rm={grade.get("red",0)}:bm={grade.get("blue",0)}')
    zoom=zoom_expression(style_data['events'],fps)
    if style_data['events']:
        vf.append(f"zoompan=z='{zoom}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps={fps}")
    font_dir=str(SKILL/'assets/fonts').replace('\\','/').replace(':',r'\:').replace("'",r"\'")
    vf+=[f"ass=captions.ass:fontsdir='{font_dir}'",f"ass=overlay.ass:fontsdir='{font_dir}'"]
    return vf


def render(a):
    job=Path(a.job).resolve();timeline,lock_hash=locked(job);p=project(job)
    require_final(job)
    style_data=read(job/'style/style.json')
    if style_data['lock_sha256']!=lock_hash:raise ValueError('风格轨属于另一时间轴')
    if read(job/'subtitles/captions.json')['lock_sha256']!=lock_hash:raise ValueError('字幕属于另一时间轴')
    width,height=dimensions(p);fps=timeline['fps']
    bgm=style_data['bgm']
    if any(e['template']=='expanded-point' and not e.get('sound_asset') for e in style_data['events']):
        raise ValueError('逐项讲解缩放缺少音效素材，请重新运行style.py；音效与缩放必须共用事件')
    sounds=[e for e in style_data['events'] if e.get('sound_asset')]
    for e in sounds:
        if digest(job/e['sound_asset']['file'])!=e['sound_asset']['sha256']:raise ValueError('音效素材已改动')
    if bgm and digest(job/bgm['file'])!=bgm['sha256']:raise ValueError('BGM素材已改动')
    with tempfile.TemporaryDirectory(prefix='cutless-render-',dir=job) as temporary:
        temp=Path(temporary);entries=[]
        for i,s in enumerate(timeline['segments']):
            start=s['source_start_frame']/fps;duration=s['frames']/fps
            name=f'p{i:04}.mov'
            # Lossless intermediates; only the final delivery is lossy-encoded.
            vf=([f'transpose={p["rotate"]}'] if p['rotate']!='auto' else [])
            vf += [f'scale={width}:{height}:force_original_aspect_ratio=decrease',
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2','setsar=1']
            fade=min(.006,duration/4)
            ff(['-ss',start,'-i',job/p['source'],'-ss',start,'-i',job/p['active_audio'],
                '-t',duration,'-map','0:v:0','-map','1:a:0','-vf',','.join(vf),
                '-af',f'asetpts=PTS-STARTPTS,apad,atrim=duration={duration},afade=t=in:d={fade},afade=t=out:st={duration-fade}:d={fade}',
                '-r',fps,'-c:v','libx264','-crf','0','-preset','ultrafast','-threads','2',
                '-c:a','pcm_s16le','-ar','48000','-pix_fmt','yuv420p','-map_metadata','-1',temp/name],job)
            entries += [f"file '{name}'",f'duration {duration:.9f}']
            print(f'片段 {i+1}/{len(timeline["segments"])}',flush=True)
        (temp/'concat.txt').write_text('\n'.join(entries)+'\n',encoding='utf-8')
        shutil.copy2(job/'subtitles/final.ass',temp/'captions.ass')
        shutil.copy2(job/'style/overlay.ass',temp/'overlay.ass')
        vf=video_filters(style_data,width,height,fps)
        args=['-f','concat','-safe','0','-i','concat.txt']
        if bgm:
            # Build a finite PCM bed before sidechain mixing. Older FFmpeg versions can
            # truncate or stall when short infinite-loop inputs share a framesync graph.
            ff(['-i',job/bgm['file'],'-t',timeline['duration'],'-ac','2','-ar','48000',
                '-c:a','pcm_s16le',temp/'music-source.wav'],job)
            with wave.open(str(temp/'music-source.wav'),'rb') as source:
                if not source.getnframes():raise ValueError('BGM没有可解码音频')
                with wave.open(str(temp/'music-bed.wav'),'wb') as target:
                    target.setparams((2,2,48000,0,'NONE','not compressed'))
                    remaining=round(timeline['duration']*48000)
                    while remaining:
                        block=source.readframes(min(65536,remaining))
                        if not block:
                            source.rewind()
                            continue
                        target.writeframesraw(block)
                        remaining-=len(block)//4
            ff(['-f','concat','-safe','0','-i','concat.txt','-vn','-t',timeline['duration'],
                '-ac','2','-ar','48000','-c:a','pcm_s16le',temp/'voice-bed.wav'],job,cwd=temp)
            # Two finite passes avoid old FFmpeg format-negotiation deadlocks when one
            # speech branch drives both the sidechain and the final mix.
            duck=('[0:a][1:a]sidechaincompress=threshold=0.025:ratio=8:attack=20:release=250,'
                f'volume={bgm["gain_db"]}dB[duck]')
            ff(['-i',temp/'music-bed.wav','-i',temp/'voice-bed.wav','-filter_complex',duck,
                '-map','[duck]','-t',timeline['duration'],'-c:a','pcm_s16le',temp/'ducked.wav'],job)
            af=('[0:a][1:a]amix=inputs=2:duration=first:weights=1 1,volume=2,'
                'alimiter=limit=0.95:level=false[a]')
            ff(['-i',temp/'voice-bed.wav','-i',temp/'ducked.wav','-filter_complex',af,
                '-map','[a]','-t',timeline['duration'],'-c:a','pcm_s16le',temp/'mixed.wav'],job)
            if abs(float(probe(temp/'mixed.wav')['format']['duration'])-timeline['duration'])>.03:
                raise ValueError('混音时长异常，不能导出')
            args+=['-i',temp/'mixed.wav','-map','0:v:0','-map','1:a:0']
        elif not sounds:args+=['-map','0:v:0','-map','0:a:0','-af','alimiter=limit=0.95:level=false']
        if sounds:
            if not (temp/'voice-bed.wav').exists():
                ff(['-f','concat','-safe','0','-i','concat.txt','-vn','-t',timeline['duration'],
                    '-ac','2','-ar','48000','-c:a','pcm_s16le',temp/'voice-bed.wav'],job,cwd=temp)
            if not bgm:
                ff(['-f','concat','-safe','0','-i','concat.txt','-vn','-t',timeline['duration'],
                    '-ac','2','-ar','48000','-c:a','pcm_s16le',temp/'mixed.wav'],job,cwd=temp)
            mixargs=['-i',temp/'mixed.wav'];filters=[];inputs='[0:a]'
            for i,event in enumerate(sounds,1):
                sound=event['sound_asset'];source=job/sound['file']
                delay=round(event['start']*1000);gain=sound['gain_db']
                if event['template']=='expanded-point':
                    decoded=temp/f'sound-{i}.wav'
                    ff(['-i',source,'-ac','2','-ar','48000','-c:a','pcm_s16le',decoded],job)
                    gain=safe_sound_gain(decoded,temp/'voice-bed.wav',event['start'],event['end'],gain)
                    aligned=temp/f'effect-bed-{i}.wav'
                    ff(['-i',decoded,'-af',f'atrim=duration={event["end"]-event["start"]},asetpts=PTS-STARTPTS,volume={gain}dB,adelay={delay}|{delay},apad,atrim=duration={timeline["duration"]}',
                        '-t',timeline['duration'],'-c:a','pcm_s16le',aligned],job)
                    source=temp/f'effect-ducked-{i}.wav'
                    ff(['-i',aligned,'-i',temp/'voice-bed.wav','-filter_complex',
                        '[0:a][1:a]sidechaincompress=threshold=0.005:ratio=20:attack=5:release=80[a]',
                        '-map','[a]','-t',timeline['duration'],'-c:a','pcm_s16le',source],job)
                    # Timing and attenuation are already applied in the finite effect bed.
                    gain=0;delay=0
                mixargs+=['-i',source]
                filters.append(f'[{i}:a]aresample=48000,aformat=channel_layouts=stereo,volume={gain}dB,'
                    f'adelay={delay}|{delay},apad,atrim=duration={timeline["duration"]}[fx{i}]')
                inputs+=f'[fx{i}]'
            filters.append(f'{inputs}amix=inputs={len(sounds)+1}:duration=first:dropout_transition=0,'
                f'volume={len(sounds)+1},alimiter=limit=0.95:level=false[mixed]')
            ff(mixargs+['-filter_complex',';'.join(filters),'-map','[mixed]','-t',timeline['duration'],
                '-c:a','pcm_s16le',temp/'with-sfx.wav'],job)
            args=['-f','concat','-safe','0','-i','concat.txt','-i',temp/'with-sfx.wav',
                '-map','0:v:0','-map','1:a:0']
        args+=['-vf',','.join(vf),'-t',timeline['duration'],'-r',fps,'-c:v','libx264',
            '-crf','18','-preset','fast','-threads','2','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',
            '-movflags','+faststart','-map_metadata','-1',job/'output/final.pending.mp4']
        ff(args,job,cwd=temp)
    locked(job)
    info=probe(job/'output/final.pending.mp4')
    duration=float(info['format']['duration'])
    if abs(duration-timeline['duration'])>max(.15,2/fps):raise ValueError('导出时长偏离锁定时间轴')
    (job/'output/final.pending.mp4').replace(job/'output/final.mp4')
    write(job/'output/render.json',{'lock_sha256':lock_hash,'video_sha256':digest(job/'output/final.mp4'),
        'subtitle_sha256':digest(job/'subtitles/final.ass'),'style_sha256':digest(job/'style/style.json'),
        'overlay_sha256':digest(job/'style/overlay.ass'),'duration':duration})
    print('已导出 output/final.mp4；继续 verify_export.py')


def verify_export(a):
    job=Path(a.job).resolve();timeline,lock_hash=locked(job);p=project(job)
    require_final(job)
    record=read(job/'output/render.json')
    checks={'lock':record['lock_sha256']==lock_hash,'video_hash':record['video_sha256']==digest(job/'output/final.mp4'),
        'subtitles':record['subtitle_sha256']==digest(job/'subtitles/final.ass'),
        'style':record['style_sha256']==digest(job/'style/style.json'),
        'overlay':record['overlay_sha256']==digest(job/'style/overlay.ass')}
    info=probe(job/'output/final.mp4');v=next(s for s in info['streams'] if s['codec_type']=='video')
    audio=next(s for s in info['streams'] if s['codec_type']=='audio')
    checks['frames']=int(v['nb_frames'])==timeline['total_frames']
    checks['av_duration']=abs(float(v['duration'])-float(audio['duration']))<.08
    checks['aspect']=abs(v['width']/v['height']-(9/16 if p['layout']=='portrait' else 16/9))<.002
    ff(['-v','error','-i',job/'output/final.mp4','-f','null','-'],job)
    checks['full_decode']=True
    for i,when in enumerate([min(1,timeline['duration']/4),timeline['duration']/2,max(0,timeline['duration']-1)]):
        ff(['-ss',when,'-i',job/'output/final.mp4','-frames:v','1',job/f'output/check-{i+1}.jpg'],job)
    write(job/'output/verification.json',{'checks':checks,'passed':all(checks.values()),
        'visual_listening_review':'需agent检查截图、试听切口；技术校验不代表已完成听感审片'})
    print(checks)
    if not all(checks.values()):raise ValueError('导出校验未全部通过')
