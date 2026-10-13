import sys,tempfile,unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from common import digest,ff,read,write,require_aliyun
from cached_assembly import assemble_cached_sources,mapped_words
from media_steps import denoise

class CachedAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.parent=self.root/'parent'
        self.parent.mkdir();source=self.parent/'source.mov'
        ff(['-f','lavfi','-i','color=red:size=180x320:rate=25:duration=1',
            '-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1',
            '-c:v','libx264','-pix_fmt','yuv420p','-c:a','pcm_s24le',source])
        (self.parent/'audio').mkdir()
        ff(['-i',source,'-vn','-c:a','pcm_s24le',self.parent/'audio/original.wav'])
        write(self.parent/'project.json',{'source':'source.mov','source_sha256':digest(source),
            'active_audio':'audio/original.wav','rotate':'auto','fps':25,'duration':1})
        write(self.parent/'asr/words.json',{'words':[
            {'begin_time':100,'end_time':300,'text':'one','sentence_id':1},
            {'begin_time':600,'end_time':800,'text':'two','sentence_id':2}]})
        # Explicit synthetic annotations. No simulated cloud task IDs are issued.
        write(self.parent/'asr/transcription.json',{'synthetic':True})
        write(self.parent/'asr/provenance.json',{'synthetic':True})
        self.script=self.root/'script.txt';self.script.write_text('one two')
        self.parts=[{'cache':str(self.parent),'start_frame':0,'end_frame':10},
                    {'cache':str(self.parent),'start_frame':15,'end_frame':25}]
        self.plan=self.root/'plan.json';write(self.plan,{'fps':25,'parts':self.parts})
    def tearDown(self):self.temp.cleanup()
    def test_actual_frame_assembly_and_deterministic_cache_mapping(self):
        job=self.root/'job'
        # Mock only real-cloud guard for the synthetic parent; all media,
        # mapping, hash validation and derived-word validation are real.
        with patch('cached_assembly.require_aliyun'):
            assemble_cached_sources(Namespace(job=str(job),plan=str(self.plan),script=str(self.script),title='test'))
            proof=require_aliyun(job)
            self.assertEqual(proof['provider'],'derived');self.assertNotIn('task_id',proof)
            self.assertEqual(read(job/'asr/assembly.json')['total_frames'],20)
            words=read(job/'asr/words.json')['words']
            self.assertEqual([(w['begin_time'],w['end_time']) for w in words],[(100,300),(400,600)])
            self.assertAlmostEqual(read(job/'project.json')['duration'],.8,places=3)
            write(job/'qc/audio.json',{'audio_sha256':digest(job/'audio/original.wav')})
            audio_hash=digest(job/'audio/original.wav')
            denoise(Namespace(job=str(job),mode='bypass',reason='Synthetic QC: retain same audio',noise_floor=-50,sample_start=0))
            self.assertEqual(digest(job/'audio/original.wav'),audio_hash)
            with self.assertRaises(ValueError):
                denoise(Namespace(job=str(job),mode='light',reason='Would invalidate cached timing',noise_floor=-50,sample_start=0))
            # Updating file hash cannot legitimize manually invented timestamps.
            words[0]['begin_time']=101;write(job/'asr/words.json',{'words':words})
            proof['words_sha256']=digest(job/'asr/words.json');write(job/'asr/provenance.json',proof)
            with self.assertRaises(ValueError):require_aliyun(job)
    def test_partial_word_clipping_is_explicit_and_dropped_word_not_recreated(self):
        words=mapped_words([{'cache':str(self.parent),'start_frame':5,'end_frame':10}],25)
        self.assertEqual(len(words),1);self.assertEqual(words[0]['begin_time'],0)
        self.assertTrue(words[0]['origin']['edge_clipped'])
        self.assertEqual(words[0]['origin']['begin_time'],100)
    def test_invalid_frame_range_rejected_before_assembly(self):
        self.parts[0]['end_frame']=26;write(self.plan,{'fps':25,'parts':self.parts})
        with patch('cached_assembly.require_aliyun'):
            with self.assertRaises(ValueError):assemble_cached_sources(Namespace(job=str(self.root/'job'),plan=str(self.plan),script=str(self.script),title='test'))

if __name__=='__main__':unittest.main()
