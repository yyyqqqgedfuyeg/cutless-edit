#!/usr/bin/env python3
"""Rebuild original synthesized BGM/SFX; standard library only, no downloaded samples."""
import argparse
import array
import json
import math
from pathlib import Path
import random
import sys
import wave

RATE=24000


def save(path,samples,target_rms=.09):
    rms=math.sqrt(sum(x*x for x in samples)/max(1,len(samples)))
    peak=max((abs(x) for x in samples),default=1)
    gain=min(target_rms/max(rms,1e-9),.7/max(peak,1e-9))
    pcm=array.array('h')
    for x in samples:
        value=round(x*gain*32767)
        pcm.extend((value,value))
    if sys.byteorder!='little':pcm.byteswap()
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as stream:
        stream.setparams((2,2,RATE,0,'NONE','not compressed'))
        stream.writeframes(pcm.tobytes())
    return {'duration':len(samples)/RATE,'sample_rate':RATE,'channels':2,
        'rms_dbfs':round(20*math.log10(max(rms*gain,1e-9)),2),'peak_dbfs':round(20*math.log10(max(peak*gain,1e-9)),2)}


def tone(buffer,start,length,midi,level,kind='pad'):
    frequency=440*2**((midi-69)/12)
    first=round(start*RATE);count=min(round(length*RATE),len(buffer)-first)
    for n in range(max(0,count)):
        t=n/RATE;end=(count-n)/RATE
        envelope=min(1,t/.04,end/.12) if kind=='pad' else (1-math.exp(-t*180))*math.exp(-t*5)*min(1,end/.08)
        signal=math.sin(2*math.pi*frequency*t)+.18*math.sin(4*math.pi*frequency*t)
        buffer[first+n]+=level*envelope*signal


def music(bpm,variant):
    beat=60/bpm;duration=16*beat
    out=array.array('f',[0])*round(duration*RATE)
    progression=[[48,55,60,64],[45,52,57,60],[41,48,53,57],[43,50,55,59]]
    for bar,chord in enumerate(progression):
        for note in chord:tone(out,bar*4*beat,4*beat,note,.035)
        for k in range(4):
            tone(out,(bar*4+k)*beat,beat*1.2,chord[(k+bar)%4]+12,.035 if variant=='calm' else .055,'pluck')
        if variant=='pulse':
            for k in (0,2):tone(out,(bar*4+k)*beat,.16,36,.055,'pluck')
    # Match zero-valued loop endpoints, with a short fade instead of a hard seam.
    fade=round(.08*RATE)
    for n in range(fade):out[n]*=n/fade;out[-n-1]*=n/fade
    return out


def build(output):
    output=Path(output);metadata={}
    for name,bpm,variant in [('calm-focus',72,'calm'),('warm-explain',80,'warm'),('bright-pulse',96,'pulse')]:
        metadata[name]=save(output/'bgm'/f'{name}.wav',music(bpm,variant))
    for name,notes in [('soft-tick',[(0,.16,84,.15)]),('gentle-chime',[(0,.6,76,.12),(.08,.52,83,.08)])]:
        out=array.array('f',[0])*round(.7*RATE)
        for start,duration,note,level in notes:tone(out,start,duration,note,level,'pluck')
        metadata[name]=save(output/'audio'/f'{name}.wav',out,.055)
    rng=random.Random(1407);out=array.array('f');previous=0
    for i in range(round(.45*RATE)):
        previous=.88*previous+.12*rng.uniform(-1,1)
        out.append(previous*math.sin(math.pi*i/(.45*RATE))**2)
    metadata['soft-whoosh']=save(output/'audio/soft-whoosh.wav',out,.04)
    (output/'audio-metadata.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('已生成3段原创合成BGM、3段提示音，以及音频参数记录')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path(__file__).resolve().parents[1]/'assets')
    build(parser.parse_args().output)
