"""Offline checks for expanded explanations, sound levels and approval gates."""
import array
import math
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
import wave
from common import write
from point_effects import validate_points, zoom_expression, safe_sound_gain
from emphasis_review import emphasis_review, approved_emphasis


class PointEffectTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.job=Path(self.temp.name)
        (self.job/'script.txt').write_text('第一，讲解观点和完整例子。第二，继续解释原因和行动。')
        self.timeline={'fps':25,'segments':[{'source_start_frame':0,'source_end_frame':200,'output_start_frame':0}]}
        self.words=[{'begin_time':0,'end_time':300},{'begin_time':4000,'end_time':4300}]
        self.events=[{'start':start,'end':start+3,'template':'expanded-point','sequence_group':'overview',
            'point_index':i,'expanded_explanation':True,'reason':'每项包含独立观点和解释',
            'script_quote':quote,'sound':'ding-ding-ding'}
            for start,i,quote in [(0,1,'第一，讲解观点和完整例子。'),(4,2,'第二，继续解释原因和行动。')]]

    def tearDown(self):self.temp.cleanup()

    def test_expanded_points_have_exact_zoom_and_timing(self):
        validate_points(self.events,self.job,self.timeline,self.words)
        for event in self.events:self.assertEqual(event['zoom'],1.2)
        expression=zoom_expression(self.events,25)
        self.assertIn('lt(on/25,3)',expression)
        self.assertNotIn('sin(',expression)
        # Independent arithmetic evaluation of the generated ffmpeg expression.
        code=expression.replace('if(', 'choose(')
        funcs={'choose':lambda condition,a,b:a if condition else b,'gte':lambda a,b:a>=b,
            'lt':lambda a,b:a<b,'min':min,'max':max}
        for frame,expected in [(0,1),(3,1.0940032),(7,1.2),(74,1.2),(75,1),(100,1),(107,1.2),(175,1)]:
            self.assertAlmostEqual(eval(code,{'__builtins__':{}},{**funcs,'on':frame}),expected,places=5)

    def test_simple_list_missing_context_and_offbeat_trigger_rejected(self):
        from copy import deepcopy
        cases=[]
        cases.append(self.events[:1])
        case=deepcopy(self.events);case[0]['expanded_explanation']=False;cases.append(case)
        case=deepcopy(self.events);case[0]['start']=.3;cases.append(case)
        case=deepcopy(self.events);case[0]['script_quote']='不存在的原稿';cases.append(case)
        case=deepcopy(self.events);case[0]['sound_gain_db']=-6;cases.append(case)
        for events in cases:
            with self.subTest(events=events),self.assertRaises(ValueError):
                validate_points(events,self.job,self.timeline,self.words)

    def tone(self,path,amplitude):
        samples=array.array('h',[round(amplitude*32767*math.sin(2*math.pi*440*i/48000)) for i in range(48000)])
        with wave.open(str(path),'wb') as target:
            target.setparams((1,2,48000,0,'NONE','not compressed'));target.writeframes(samples.tobytes())

    def test_loud_effect_is_bounded_below_quiet_voice(self):
        sound=self.job/'sound.wav';voice=self.job/'voice.wav'
        self.tone(sound,.9);self.tone(voice,.02)
        gain=safe_sound_gain(sound,voice,0,1,-24)
        self.assertLessEqual(.9*10**(gain/20),.02*.25+.0001)

    def test_unmarked_proposal_requires_explicit_user_and_reapproval_after_changes(self):
        write(self.job/'subtitles/captions.json',{'cues':[{'id':'s1','text':'讲解观点','start':0,'end':1}]})
        write(self.job/'proposal.json',{'reason':'突出核心观点','items':[{'id':'s1','text':'讲解观点','spans':[{'text':'观点'}]}]})
        args=SimpleNamespace(job=str(self.job),proposal=str(self.job/'proposal.json'))
        emphasis_review(args)
        self.assertIsNone(approved_emphasis(self.job))
        with self.assertRaises(ValueError):approved_emphasis(self.job,required=True)
        args.proposal=None;args.decision='approve';args.user_response='同意强调观点'
        emphasis_review(args)
        approved=approved_emphasis(self.job,required=True)
        self.assertEqual(approved['s1'][0]['style']['color'],'#FFFF00')
        (self.job/'script.txt').write_text('修改后的原稿')
        with self.assertRaises(ValueError):approved_emphasis(self.job,required=True)


    def test_rendered_subtitle_size_and_position_stay_fixed_during_zoom(self):
        import subprocess
        from PIL import Image, ImageChops
        from common import exe
        from finish_steps import ass_header, video_filters
        from inline_emphasis import emphasized_text, single_line_layout
        from visual_assets import rounded_background
        layout=single_line_layout({'font':'Noto Sans CJK SC','background_padding':8})
        text,box=emphasized_text('固定字幕',[],24,layout)
        lines=rounded_background('固定字幕',0,2,160,170,24,layout,layout['font'],False,box_size=box)
        lines.append('Dialogue: 1,0:00:00.00,0:00:02.00,Default,,0,0,0,,'+text)
        header=ass_header(320,200,layout['font'],24,.85,color='#000000',outline_width=0,shadow_depth=0,bold=False)
        (self.job/'captions.ass').write_text(header+'\n'.join(lines))
        (self.job/'overlay.ass').write_text(header)
        event={'start':0,'end':1.2,'template':'expanded-point','zoom':1.2,'zoom_transition_seconds':.25}
        filters=video_filters({'events':[event]},320,200,25)
        self.assertLess(next(i for i,f in enumerate(filters) if f.startswith('zoompan=')),filters.index('ass=captions.ass'))
        subprocess.run([exe('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
            'color=c=navy:s=320x200:r=25:d=1.5,drawbox=x=140:y=70:w=40:h=40:color=red:t=fill',
            '-vf',','.join(filters+["select='eq(n,0)+eq(n,10)+eq(n,30)'"]),'-vsync','0',
            str(self.job/'frame-%02d.png')],cwd=self.job,check=True)
        frames=[Image.open(self.job/f'frame-{i:02}.png').convert('RGB') for i in (1,2,3)]
        for frame in frames[1:]:
            self.assertIsNone(ImageChops.difference(frames[0].crop((0,125,320,200)),frame.crop((0,125,320,200))).getbbox())
        def red_box(frame):
            mask=Image.new('L',frame.size)
            mask.putdata([255 if r>180 and g<70 and b<70 else 0 for r,g,b in frame.getdata()])
            return mask.getbbox()
        boxes=[red_box(frame) for frame in frames]
        self.assertGreater(boxes[1][2]-boxes[1][0],boxes[0][2]-boxes[0][0])
        self.assertEqual(boxes[2],boxes[0])
