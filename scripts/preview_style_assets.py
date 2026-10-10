#!/usr/bin/env python3
"""Render subtitle-only style previews, not project subtitles or editorial approvals."""
import argparse
from pathlib import Path

from common import SKILL, ff, read, stamp, write
from finish_steps import ass_header, ass_text
from visual_assets import asset_entry, bounce_tags


def preview(output,layout):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    p=read(SKILL/f'assets/profiles/{layout}.json')
    width,height=(360,640) if layout=='portrait' else (640,360)
    size=round(width*p['subtitle']['font_size_ratio']);lines=[]
    def add(start,end,text,tags=''):
        lines.append(f'Dialogue: 0,{stamp(start,True)},{stamp(end,True)},Default,,0,0,0,,{tags}{ass_text(text)}')
    add(0,2,'固定位置 · 清晰字幕')
    add(2,4,'关键词轻弹跳', '{'+bounce_tags(2,p['subtitle']['bounce_strength'],p['subtitle']['bounce_ms'])+'}')
    add(4,6,'一句连贯的话\n可以分成两行', '{'+bounce_tags(2,.05,320)+'}')
    add(6,8,'「重点结论」','{\\c&H66DFFF&\\fad(120,120)}')
    add(8,10,'文字校对由 Agent 完成')
    name=layout+'-styles'
    (output/(name+'.ass')).write_text(ass_header(width,height,p['subtitle']['font'],size,
        p['subtitle']['bottom_y_ratio'])+'\n'.join(lines)+'\n',encoding='utf-8')
    bgm=asset_entry('bgm',p['style_defaults']['bgm'])
    ff(['-f','lavfi','-i',f'color=c=0x142236:s={width}x{height}:r=24:d=10',
        '-i',SKILL/'assets'/bgm['path'],'-vf',f'ass={name}.ass','-af','volume=-3dB',
        '-t','10','-c:v','libx264','-preset','fast','-threads','2','-pix_fmt','yuv420p',
        '-c:a','aac','-movflags','+faststart',output/(name+'.mp4')],cwd=output)
    write(output/(name+'.json'),{'type':'style-preview-only','layout':layout,'bgm':bgm['id'],
        'text':'模板示例，不是实际口播或已校对项目字幕'})
    print(output/(name+'.mp4'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=SKILL/'assets/previews')
    parser.add_argument('--layout',choices=['portrait','landscape','both'],default='both')
    args=parser.parse_args()
    for layout in (['portrait','landscape'] if args.layout=='both' else [args.layout]):preview(args.output,layout)
