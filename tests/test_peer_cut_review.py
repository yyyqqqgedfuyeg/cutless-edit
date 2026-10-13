import json
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from PIL import Image,ImageDraw

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import digest,ff,write
from peer_cut_review import (POLICY,apply_peer_adjustments,prepare_cut_review,require_peer_review,
                             review_cut_plan,sample_indices,source_for_output)
from finish_steps import render_segment


class PeerReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.job=Path(self.temp.name)
        frames=self.job/'frames';frames.mkdir()
        for n in range(20):
            image=Image.new('RGB',(180,320),(n*10,n*10,n*10))
            ImageDraw.Draw(image).rectangle((20+n,150,40+n,220),fill='white')
            image.save(frames/f'{n:03}.png')
        source=self.job/'source.mov'
        ff(['-framerate','25','-i',frames/'%03d.png','-f','lavfi','-i',
            'sine=frequency=440:sample_rate=48000:duration=0.8','-c:v','libx264','-crf','0',
            '-pix_fmt','yuv420p','-c:a','pcm_s16le',source])
        ff(['-i',source,'-vn','-c:a','pcm_s24le',self.job/'audio.wav'])
        write(self.job/'project.json',{'source':'source.mov','source_sha256':digest(source),
              'active_audio':'audio.wav','layout':'portrait','rotate':'auto','fps':25,'duration':.8})
        write(self.job/'asr/words.json',{'words':[{'text':'ending','begin_time':40,'end_time':240},
              {'text':'discarded retake','begin_time':240,'end_time':360},
              {'text':'next','begin_time':400,'end_time':720}]})
        (self.job/'script.txt').write_text('ending next')
        write(self.job/'decisions.json',{'candidates':[{'id':'m1','start':.2,'end':.4,'action':'drop','kind':'manual','reason':'synthetic fixture deletion'}]})
        write(self.job/'timeline.json',{'fps':25,'speed':1.3,'duration':.48,'total_frames':12,
              'decisions_sha256':digest(self.job/'decisions.json'),'peer_cut_review_policy':POLICY,
              'segments':[{'source_start_frame':0,'source_end_frame':5,'source_frames':5,'frames':4,'output_start_frame':0},
                          {'source_start_frame':10,'source_end_frame':20,'source_frames':10,'frames':8,'output_start_frame':4}]})
        # These are explicitly synthetic time annotations, not fake production
        # cloud provenance. Only the real-provider guard is mocked for this fixture.
        self.guard=patch('peer_cut_review.require_aliyun');self.guard.start()

    def tearDown(self):
        self.guard.stop();self.temp.cleanup()

    def prepare(self,phase='proposal',dense=False):
        return prepare_cut_review(Namespace(job=str(self.job),planner='/agent-a',phase=phase,context=.6,dense=dense))

    def report(self,proposal,decision='approve',audio_status='uncertain'):
        item=proposal['items'][0]
        proof=item['evidence'][1] if proposal['phase']=='export' else item['evidence'][0]
        return {'reviewer':'/agent-b','items':[{'id':item['id'],'decision':decision,
                'visual_note':'Synthetic moving square is visible in ordered context; a normal jump may be accepted.',
                'audio_note':'Synthetic word boundary crosses cut; measurements available, no claim of listening.',
                'audio_status':audio_status,'listening_status':'unavailable','evidence':[proof]}]}

    def submit(self,proposal,decision='approve',audio_status='uncertain'):
        report=self.report(proposal,decision,audio_status)
        write(self.job/'reviews.json',report)
        review_cut_plan(Namespace(job=str(self.job),reviews=str(self.job/'reviews.json'),phase=proposal['phase']))

    def test_boundary_dense_frames_audio_and_normal_jump_acceptance(self):
        self.assertTrue(set(range(2,9)).issubset(sample_indices(5,15,30)))
        proposal=self.prepare();self.assertTrue(proposal['items'][0]['word_edges'])
        self.assertTrue(proposal['items'][0]['audio_edges']['out'][0]['available'])
        with self.assertRaises(ValueError):require_peer_review(self.job)
        self.submit(proposal)
        self.assertTrue(require_peer_review(self.job)['passed'])
        # Acceptance is a reviewer judgement, not a pixel-distance ceiling.
        self.assertNotIn('jump_threshold',proposal)

    def test_same_agent_and_wrong_cut_evidence_rejected(self):
        p=self.prepare();r=self.report(p);r['reviewer']='/agent-a'
        write(self.job/'reviews.json',r)
        args=Namespace(job=str(self.job),reviews=str(self.job/'reviews.json'),phase='proposal')
        with self.assertRaises(ValueError):review_cut_plan(args)
        r=self.report(p);r['items'][0]['evidence'][0]['sha256']='changed'
        write(self.job/'reviews.json',r)
        with self.assertRaises(ValueError):review_cut_plan(args)

    def test_audio_risk_cannot_be_approved_and_retake_cannot_be_restored(self):
        p=self.prepare()
        with self.assertRaises(ValueError):self.submit(p,'approve','potential_truncation')
        self.submit(p,'adjust','potential_truncation')
        write(self.job/'changes.json',[{'id':'cut-001','restore_out_frames':2,'reason':'Would restore the deliberately discarded word'}])
        with self.assertRaises(ValueError):apply_peer_adjustments(Namespace(job=str(self.job),changes=str(self.job/'changes.json')))

    def test_restore_surviving_word_tail_and_require_new_review(self):
        p=self.prepare();self.submit(p,'adjust','potential_truncation')
        original=digest(self.job/'source.mov')
        write(self.job/'changes.json',[{'id':'cut-001','restore_out_frames':1,'reason':'Preserve remaining tail of already retained word'}])
        apply_peer_adjustments(Namespace(job=str(self.job),changes=str(self.job/'changes.json')))
        self.assertEqual(digest(self.job/'source.mov'),original)
        t=json.loads((self.job/'timeline.json').read_text());self.assertEqual(t['segments'][0]['source_end_frame'],6)
        self.assertEqual(t['total_frames'],sum(s['frames'] for s in t['segments']))
        with self.assertRaises(ValueError):require_peer_review(self.job)
        self.submit(self.prepare());require_peer_review(self.job)

    def test_export_review_requires_actual_export_and_fresh_video(self):
        (self.job/'output').mkdir();shutil.copy2(self.job/'source.mov',self.job/'output/final.mp4')
        p=self.prepare('export');r=self.report(p)
        r['items'][0]['evidence']=[p['items'][0]['evidence'][0]]
        write(self.job/'reviews.json',r)
        with self.assertRaises(ValueError):review_cut_plan(Namespace(job=str(self.job),reviews=str(self.job/'reviews.json'),phase='export'))
        self.submit(p);require_peer_review(self.job,'export')
        (self.job/'output/final.mp4').write_bytes(b'changed')
        with self.assertRaises(ValueError):require_peer_review(self.job,'export')

    def test_sampling_matches_real_non_integer_speed_render(self):
        segment={'source_start_frame':0,'source_end_frame':13,'frames':10,'output_start_frame':0}
        t={'segments':[segment]}
        render_segment({'rotate':'auto'},segment,25,180,320,self.job/'source.mov',self.job/'audio.wav',self.job/'rendered.mov',self.job)
        from peer_cut_review import capture_frames
        images=capture_frames(self.job/'rendered.mov',[8,9],self.job/'actual',self.job)
        actual=[]
        for n in (8,9):
            with Image.open(images[n]) as img:actual.append(round(img.getpixel((5,5))[0]/10))
        self.assertEqual(actual,[source_for_output(t,n) for n in (8,9)])

    def test_dense_mode_provides_every_frame_in_window(self):
        p=self.prepare(dense=True)
        self.assertEqual(p['items'][0]['source_out_indices'],list(range(20)))


if __name__=='__main__':unittest.main()
