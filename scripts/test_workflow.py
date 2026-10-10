"""Offline invariant tests. Fixture timestamps are synthetic, never cloud results."""
import array
from copy import deepcopy
from pathlib import Path
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
import wave

from common import digest, exe, locked, read, require_script, write
from caption_review import review_subtitles, require_final
from edit_steps import build_timeline, lock_timeline
from finish_steps import ass_color, ass_header, subtitles, wrap, style, render, verify_export
from media_steps import denoise
from visual_assets import bounce_tags, subtitle_animation, character_events, rounded_background, text_width


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.job=Path(self.temp.name)
        for folder in ['media','audio','qc','asr','previews','subtitles']:(self.job/folder).mkdir()
        (self.job/'media/source.mp4').write_bytes(b'synthetic fixture; not a real video')
        (self.job/'audio/original.wav').write_bytes(b'synthetic fixture audio')
        (self.job/'script.txt').write_text('【前句】。后句。',encoding='utf-8')
        (self.job/'asr/preview.srt').write_text('fixture',encoding='utf-8')
        write(self.job/'asr/transcription.json',{'fixture':True})
        words={'unit':'milliseconds','words':[
            {'begin_time':200,'end_time':800,'text':'前句','punctuation':'。'},
            {'begin_time':3200,'end_time':4000,'text':'后句','punctuation':'。'}]}
        write(self.job/'asr/words.json',words)
        p={'source':'media/source.mp4','source_sha256':digest(self.job/'media/source.mp4'),
            'active_audio':'audio/original.wav','fps':25,'duration':5,'audio_video_start_offset':0,
            'layout':'portrait','rotate':'auto'}
        write(self.job/'project.json',p)
        write(self.job/'asr/provenance.json',{'provider':'aliyun','model':'unit-test-fixture',
            'task_id':'synthetic-test-not-cloud','source_sha256':p['source_sha256'],
            'audio_sha256':digest(self.job/'audio/original.wav'),
            'raw_sha256':digest(self.job/'asr/transcription.json'),
            'words_sha256':digest(self.job/'asr/words.json')})
        data={'source_sha256':p['source_sha256'],'words_sha256':digest(self.job/'asr/words.json'),
            'candidates':[{'id':'c1','kind':'pause','start':.8,'end':3.2,'action':'shorten',
                'keep_seconds':.24,'reason':'synthetic gap test'}]}
        write(self.job/'decisions.json',data)
        self.args=SimpleNamespace(job=str(self.job))

    def tearDown(self):self.temp.cleanup()

    def create_lock(self):
        build_timeline(self.args)
        write(self.job/'previews/review.json',{'timeline_sha256':digest(self.job/'timeline.json'),
            'decisions_sha256':digest(self.job/'decisions.json'),'cuts':[{'id':'cut-001','reviewed':True}],
            'retained_pauses':[],'whole_track_reviewed':True,'note':'synthetic unit-test review'})
        lock_timeline(self.args)

    def test_gap_cut_preserves_word_edges_and_frame_count(self):
        build_timeline(self.args)
        t=read(self.job/'timeline.json');left,right=t['segments']
        self.assertGreaterEqual(left['source_end_frame']/25,.8)
        self.assertLessEqual(right['source_start_frame']/25,3.2)
        self.assertEqual(sum(s['frames'] for s in t['segments']),t['total_frames'])

    def test_partial_word_delete_is_rejected(self):
        d=read(self.job/'decisions.json');d['candidates'][0].update(action='drop',start=.5,end=1.2)
        write(self.job/'decisions.json',d)
        with self.assertRaisesRegex(ValueError,'切入字词'):build_timeline(self.args)

    def test_unreviewed_candidates_block_build(self):
        d=read(self.job/'decisions.json');d['candidates'][0]['action']='undecided'
        write(self.job/'decisions.json',d)
        with self.assertRaisesRegex(ValueError,'未复核'):build_timeline(self.args)

    def test_lock_rejects_timeline_mutation(self):
        self.create_lock();locked(self.job)
        t=read(self.job/'timeline.json');t['total_frames']+=1;write(self.job/'timeline.json',t)
        with self.assertRaisesRegex(ValueError,'锁定校验失败'):locked(self.job)

    def test_lock_rejects_raw_transcript_mutation(self):
        self.create_lock();write(self.job/'asr/transcription.json',{'changed':True})
        with self.assertRaisesRegex(ValueError,'锁定校验失败'):locked(self.job)

    def test_caption_position_does_not_change_lock(self):
        self.create_lock();before=locked(self.job)[1]
        args=SimpleNamespace(job=str(self.job),bottom=.85,font_size=None,font=None,line_chars=None,
            terms=None,corrections=None)
        subtitles(args)
        self.assertEqual(locked(self.job)[1],before)
        self.assertEqual(read(self.job/'subtitles/layout.json')['bottom_y_ratio'],.85)

    def test_protected_phrase_does_not_split(self):
        result=wrap('包括中国人寿还有中国人保',8,['中国人寿','中国人保'])
        self.assertIn('中国人寿',result);self.assertIn('中国人保',result)
        self.assertLessEqual(len(result.splitlines()),2)

    def test_bounce_is_bounded_and_respects_cue_selection(self):
        self.assertEqual(bounce_tags(.15),'')
        self.assertNotIn('\\pos',bounce_tags(2))
        self.assertIn('\\fscx100\\fscy100',bounce_tags(2))
        layout={'animation':'bounce','animated_cues':['s0001']}
        self.assertEqual(subtitle_animation(layout,{'id':'s0002','start':0,'end':2}),'')
        self.assertIn('\\t(',subtitle_animation(layout,{'id':'s0001','start':0,'end':2}))
        with self.assertRaises(ValueError):bounce_tags(2,.5)

    def test_character_render_has_real_vertical_motion_and_enlargement(self):
        from PIL import Image
        for mode in ('char-bounce','char-grow'):
            events=character_events('字',0,1,160,100,60,mode,color='#FFFFFF')
            ass=self.job/'animation.ass'
            ass.write_text(ass_header(320,200,'Noto Sans CJK SC',60,.8,
                outline_width=0,shadow_depth=0)+'\n'.join(events)+'\n')
            boxes=[]
            for when in (0,.12,.8):
                target=self.job/f'{mode}-{when}.png'
                subprocess.run([exe('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
                    'color=c=black:s=320x200:r=100:d=1','-vf',f'ass={ass}',
                    '-ss',str(when),'-frames:v','1',str(target)],check=True)
                mask=Image.open(target).convert('L').point(lambda v:255 if v>180 else 0)
                boxes.append(mask.getbbox())
            if mode=='char-bounce':
                self.assertLess(boxes[1][1],boxes[0][1]-8)
                self.assertLessEqual(abs(boxes[2][1]-boxes[0][1]),1)
            else:
                self.assertGreater(boxes[1][3]-boxes[1][1],(boxes[0][3]-boxes[0][1])*1.3)
                self.assertLessEqual(abs((boxes[2][3]-boxes[2][1])-(boxes[0][3]-boxes[0][1])),1)
        with self.assertRaises(ValueError):character_events('字',0,.1,360,900,50)

    def test_background_is_optional_and_validated(self):
        self.assertEqual(rounded_background('字幕',0,1,160,150,30,{}),[])
        lines=rounded_background('字幕\n两行',0,1,160,150,30,
            {'background':True,'background_color':'#12345680'})
        self.assertIn('\\1a&H7F&',lines[0])
        self.assertIn('\\p1',lines[0])
        self.assertLess(text_width('字幕',30),60)
        with self.assertRaises(ValueError):
            rounded_background('字',0,1,160,150,30,{'background':True,'background_radius':-1})

    def test_rgb_opacity_converts_to_ass_bgr_transparency(self):
        self.assertEqual(ass_color('#123456'),'&H00563412')
        self.assertEqual(ass_color('#12345680'),'&H7F563412')
        self.assertEqual(ass_color('#12345600'),'&HFF563412')
        with self.assertRaises(ValueError):ass_color('red,invalid')
        with self.assertRaises(ValueError):ass_header(540,960,'Noto Sans CJK SC',28,.82,outline_width=float('nan'))

    def test_appearance_persists_without_changing_timeline(self):
        self.create_lock();before=locked(self.job)[1]
        args=SimpleNamespace(job=str(self.job),bottom=None,font_size=None,font=None,line_chars=None,
            terms=None,corrections=None,text_color='#FFD54F',outline_color='#123456',outline_width=3.5,
            shadow_color='#00000080',shadow_depth=0,bold=False)
        subtitles(args)
        header=(self.job/'subtitles/draft.ass').read_text().split('Style: Default,',1)[1].splitlines()[0].split(',')
        self.assertEqual(header[2],'&H00000000')
        self.assertEqual(header[4],'&H00000000')
        self.assertEqual(header[6],'0')
        self.assertEqual(header[15:17],['0','0'])
        args.text_color=args.outline_width=None
        subtitles(args)
        self.assertEqual(read(self.job/'subtitles/layout.json')['text_color'],'#000000')
        self.assertEqual(read(self.job/'subtitles/layout.json')['outline_width'],0)
        self.assertEqual(locked(self.job)[1],before)

    def caption_args(self):
        return SimpleNamespace(job=str(self.job),bottom=None,font_size=None,font=None,line_chars=None,
            terms=None,corrections=None)

    def test_render_grades_bounce_and_sound_with_and_without_music(self):
        # Fully synthetic media/provenance, never presented as a real ASR job.
        for folder in ('style','output','logs'):(self.job/folder).mkdir(exist_ok=True)
        subprocess.run([exe('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
            'color=c=navy:s=180x320:r=25:d=5','-c:v','libx264',
            str(self.job/'media/source.mp4')],check=True)
        subprocess.run([exe('ffmpeg'),'-v','error','-y','-f','lavfi','-i',
            'sine=frequency=440:duration=5:sample_rate=48000',
            str(self.job/'audio/original.wav')],check=True)
        for name in ('project.json','decisions.json','asr/provenance.json'):
            value=read(self.job/name);value['source_sha256']=digest(self.job/'media/source.mp4')
            if name=='project.json':value.update(output_width=180,output_height=320)
            if name=='asr/provenance.json':value['audio_sha256']=digest(self.job/'audio/original.wav')
            write(self.job/name,value)
        words=read(self.job/'asr/words.json');words['words'][0]['begin_time']=0
        write(self.job/'asr/words.json',words)
        for name in ('decisions.json','asr/provenance.json'):
            value=read(self.job/name);value['words_sha256']=digest(self.job/'asr/words.json');write(self.job/name,value)
        (self.job/'script.txt').write_text('第一，【前句】，详细讲解原因。第二，后句，继续展开说明。')
        self.create_lock();args=self.caption_args();args.animation='bounce';args.background=True;args.background_color='#183C60CC'
        subtitles(args);review_subtitles(self.review_report())
        write(self.job/'events.json',[{'start':.3,'end':1.5,'template':'subtitle-bounce',
            'text':'测试','background':True,'background_color':'#503060DD'}])
        for preset,grade in ((None,'warm-soft'),('calm-focus','auto'),(None,'none')):
            with self.subTest(bgm=preset):
                events=read(self.job/'events.json')
                events[0]['template']='char-bounce' if preset is None else 'char-grow'
                if grade=='none':
                    cues=read(self.job/'subtitles/captions.json')['cues']
                    events=[{'start':cue['start'],'end':cue['start']+1,'template':'expanded-point',
                        'sequence_group':'test','point_index':i,'expanded_explanation':True,
                        'reason':'合成测试中的逐项独立解释','script_quote':quote,'sound':sound}
                        for i,(cue,quote,sound) in enumerate(zip(cues,
                            ['第一，【前句】，详细讲解原因。','第二，后句，继续展开说明。'],
                            ['ding-ding-ding','swish']),1)]
                write(self.job/'events.json',events)
                style(SimpleNamespace(job=str(self.job),events=str(self.job/'events.json'),
                    grade=grade,bgm=None,bgm_preset=preset,bgm_source=None,bgm_gain=-14))
                render(self.args);verify_export(self.args)
                self.assertTrue(read(self.job/'output/verification.json')['passed'])

    def review_report(self):
        cues=read(self.job/'subtitles/captions.json')['cues']
        report={'reviewer':'unit-test fixture','items':[
            {**{k:c[k] for k in ('id','text','start','end')},'script_quote':(self.job/'script.txt').read_text(),
                'decision':'matches_script','alignment_checked':True,'reason':'synthetic test'} for c in cues]}
        write(self.job/'reviews.json',report)
        return SimpleNamespace(job=str(self.job),reviews=str(self.job/'reviews.json'))

    def test_marked_script_defaults_and_overflow(self):
        (self.job/'script.txt').write_text('【前句】。后句。',encoding='utf-8')
        self.create_lock();before=locked(self.job)[1]
        subtitles(self.caption_args())
        layout=read(self.job/'subtitles/layout.json')
        ass=(self.job/'subtitles/draft.ass').read_text()
        self.assertEqual(layout['font_size_ratio'],.052*3)
        self.assertEqual(layout['max_lines'],1)
        self.assertIn('\\1c&H0000FFFF&',ass)
        self.assertIn('\\fs226.80',ass)
        self.assertIn('\\bord3',ass)
        self.assertIn('\\1c&H00FFFFFF&',ass)
        self.assertNotIn('\\N',ass)
        self.assertNotIn('\\t(',ass)
        self.assertEqual(locked(self.job)[1],before)
        path=self.job/'subtitles/captions.json'
        captions=read(path);captions['cues'][0]['text']='过长的字幕需要按真实字词边界拆成多个短片段'
        write(path,captions)
        with self.assertRaisesRegex(ValueError,'单行字幕超出'):subtitles(self.caption_args())

    def test_unmarked_manuscript_blocks_final_until_user_approves(self):
        from emphasis_review import emphasis_review
        (self.job/'script.txt').write_text('前句。后句。')
        self.create_lock();subtitles(self.caption_args())
        with self.assertRaisesRegex(ValueError,'用户明确审批'):
            review_subtitles(self.review_report())
        cues=read(self.job/'subtitles/captions.json')['cues']
        write(self.job/'proposal.json',{'reason':'突出第一句的核心信息','items':[
            {'id':cues[0]['id'],'text':cues[0]['text'],'spans':[{'text':'前句'}]}]})
        args=SimpleNamespace(job=str(self.job),proposal=str(self.job/'proposal.json'))
        emphasis_review(args)
        with self.assertRaises(ValueError):require_final(self.job)
        args.proposal=None;args.decision='approve';args.user_response='同意突出前句'
        emphasis_review(args)
        with self.assertRaisesRegex(ValueError,'重新运行subtitles'):require_final(self.job)
        subtitles(self.caption_args());require_final(self.job)
        self.assertIn('\\1c&H0000FFFF&',(self.job/'subtitles/final.ass').read_text())
        args.decision='reject';args.user_response='取消刚才的重点方案'
        emphasis_review(args)
        with self.assertRaises(ValueError):require_final(self.job)

    def test_empty_original_is_rejected(self):
        (self.job/'script.txt').write_text(' \n。！？',encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'原稿不能为空'):require_script(self.job/'script.txt')

    def test_unreviewed_subtitles_cannot_be_final(self):
        self.create_lock();subtitles(self.caption_args())
        self.assertFalse((self.job/'subtitles/final.ass').exists())
        with self.assertRaisesRegex(ValueError,'未完成'):require_final(self.job)
        args=self.review_report();review_subtitles(args);require_final(self.job)
        args2=self.caption_args();args2.bottom=.86
        args2.animation='bounce';args2.animated_cues='s0001'
        before=digest(self.job/'subtitles/captions.json')
        cue=read(self.job/'subtitles/captions.json')['cues'][0]
        write(self.job/'emphasis.json',[{'id':cue['id'],'text':cue['text'],
            'spans':[{'text':cue['text'],'style':{'size_scale':1.3,'color':'#FFD54F'}}]}])
        args2.emphasis=str(self.job/'emphasis.json')
        subtitles(args2);require_final(self.job)
        self.assertNotIn('\\fscx',(self.job/'subtitles/final.ass').read_text())
        self.assertEqual(digest(self.job/'subtitles/captions.json'),before)
        c=read(self.job/'subtitles/captions.json');c['cues'][0]['text']='改变内容'
        write(self.job/'subtitles/captions.json',c)
        with self.assertRaisesRegex(ValueError,'过期'):require_final(self.job)

    def test_review_must_cover_all_cues_and_quote_original(self):
        self.create_lock();subtitles(self.caption_args());args=self.review_report()
        r=read(args.reviews);r['items'].pop();write(args.reviews,r)
        with self.assertRaisesRegex(ValueError,'全部字幕'):review_subtitles(args)
        args=self.review_report();r=read(args.reviews);r['items'][0]['script_quote']='原稿不存在的内容'
        write(args.reviews,r)
        with self.assertRaisesRegex(ValueError,'实际存在'):review_subtitles(args)

    def test_non_aliyun_transcription_blocks_subtitles(self):
        p=read(self.job/'asr/provenance.json');p['provider']='other';write(self.job/'asr/provenance.json',p)
        self.create_lock()
        with self.assertRaisesRegex(ValueError,'阿里模型'):subtitles(self.caption_args())

    def test_denoise_preserves_tail_and_does_not_delay_impulses(self):
        (self.job/'asr/provenance.json').unlink()
        with wave.open(str(self.job/'audio/original.wav'),'wb') as out:
            out.setparams((1,2,48000,0,'NONE','not compressed'))
            out.writeframes(b''.join(struct.pack('<h',24000 if i in {9600,47900} else 0) for i in range(48000)))
        write(self.job/'qc/audio.json',{'fixture':True})
        args=SimpleNamespace(job=str(self.job),mode='light',noise_floor=-50,sample_start=0,reason='test')
        denoise(args)
        result=subprocess.run([exe('ffmpeg'),'-v','error','-i',str(self.job/'audio/denoised.wav'),
            '-ac','1','-ar','48000','-f','f32le','-'],capture_output=True,check=True)
        values=array.array('f',result.stdout)
        self.assertEqual(len(values),48000)
        self.assertGreater(abs(values[47900]),.1)
        self.assertEqual(max(range(8000,12000),key=lambda i:abs(values[i])),9600)


if __name__=='__main__':unittest.main()
