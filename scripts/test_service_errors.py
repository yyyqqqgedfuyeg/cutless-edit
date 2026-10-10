"""Offline fake API responses; never uses .env or contacts a provider."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch, Mock
import requests

from common import digest, read, write
from asr_steps import transcribe, load_api_config
from service_errors import checked_json, service_failure


def response(status,body):
    return SimpleNamespace(ok=status<400,status_code=status,json=lambda:body)


class ServiceErrorTests(TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory();self.job=Path(self.temp.name)
        for folder in ('media','audio','asr'):(self.job/folder).mkdir()
        (self.job/'media/source.mp4').write_bytes(b'fake-test-video')
        (self.job/'audio/original.wav').write_bytes(b'fake-test-audio')
        (self.job/'script.txt').write_text('测试原稿')
        (self.job/'test.env').write_text('DASHSCOPE_API_KEY=sk-offline-test-not-real\n')
        write(self.job/'project.json',{'source':'media/source.mp4','source_sha256':digest(self.job/'media/source.mp4'),
            'active_audio':'audio/original.wav','audio_review':'accepted','duration':5})
        self.config_patch=patch('asr_steps.ENV_PATH',self.job/'test.env')
        self.config_patch.start();self.addCleanup(self.config_patch.stop)
        self.args=SimpleNamespace(job=str(self.job),reuse=None,timeout=5)

    def tearDown(self):self.temp.cleanup()

    def test_local_config_ignores_process_environment(self):
        with patch.dict('os.environ',{'DASHSCOPE_API_KEY':'unexpected-process-key'}):
            self.assertEqual(load_api_config()['DASHSCOPE_API_KEY'],'sk-offline-test-not-real')
        (self.job/'test.env').unlink()
        with self.assertRaisesRegex(ValueError,'scripts/.env'):load_api_config()

    def resumed(self):
        write(self.job/'asr/submission.json',{'task_id':'fake-task','model':'fake-model',
            'audio_sha256':digest(self.job/'audio/original.wav')})

    def test_categories_and_no_raw_secrets(self):
        cases=[(403,'AllocationQuota.FreeTierOnly','free_quota'),(400,'Arrearage','billing'),
            (429,'Throttling.AllocationQuota','allocation_limit'),(429,'Throttling.RateQuota','rate_limit'),
            (401,'InvalidApiKey','authentication'),(403,'AccessDenied','access'),(503,'InternalError','server')]
        for status,code,category in cases:
            with self.subTest(code=code),self.assertRaises(ValueError):
                checked_json(response(status,{'code':code,'message':'secret sk-not-safe https://signed.example/token',
                    'request_id':'fake-request'}),self.job,'测试')
            report=read(self.job/'asr/error.json')
            self.assertEqual(report['category'],category)
            self.assertEqual(report['request_id'],'fake-request')
            self.assertNotIn('sk-not-safe',str(report));self.assertNotIn('signed.example',str(report))
            self.assertFalse(report['automatic_retry'])

    def test_free_quota_message_is_distinct_from_rate_quota(self):
        with self.assertRaisesRegex(ValueError,'免费额度'):
            service_failure(self.job,'测试',429,{'code':'Throttling.AllocationQuota','message':'Free allocated quota exceeded.'})

    def test_non_json_http_error_is_actionable(self):
        r=Mock(ok=False,status_code=503);r.json.side_effect=ValueError('bad response')
        with self.assertRaisesRegex(ValueError,'服务暂时异常'):checked_json(r,self.job,'查询')

    def test_async_and_per_file_failures_surface_billing(self):
        for output in ({'task_status':'FAILED','code':'Arrearage','message':'untrusted'},
            {'task_status':'SUCCEEDED','results':[{'subtask_status':'FAILED','code':'AllocationQuota.FreeTierOnly'}]}):
            self.resumed()
            with patch.dict('os.environ',{},clear=True),patch('requests.request',return_value=response(200,{'output':output})) as req:
                with self.assertRaises(ValueError):transcribe(self.args)
                self.assertEqual(req.call_count,1);self.assertEqual(req.call_args.args[0],'GET')
                self.assertFalse((self.job/'asr/provenance.json').exists())
            self.assertIn(read(self.job/'asr/error.json')['category'],{'billing','free_quota'})

    def test_poll_network_failure_preserves_task(self):
        self.resumed()
        with patch.dict('os.environ',{},clear=True),patch('requests.request',side_effect=requests.Timeout):
            with self.assertRaisesRegex(ValueError,'网络'):transcribe(self.args)
        self.assertTrue((self.job/'asr/submission.json').exists())
        self.assertEqual(read(self.job/'asr/error.json')['task_id'],'fake-task')

    def test_submission_timeout_blocks_duplicate_post(self):
        policy={'data':{'upload_dir':'fake','oss_access_key_id':'fake','signature':'fake','policy':'fake',
            'x_oss_object_acl':'private','x_oss_forbid_overwrite':True,'upload_host':'https://offline.invalid'}}
        with patch.dict('os.environ',{},clear=True),patch('asr_steps.ff',side_effect=lambda *a,**k:(self.job/'asr/audio.mp3').write_bytes(b'fake')),\
             patch('requests.post',return_value=response(200,{})),\
             patch('requests.request',side_effect=[response(200,policy),requests.Timeout]) as req:
            with self.assertRaisesRegex(ValueError,'网络'):transcribe(self.args)
            self.assertTrue((self.job/'asr/submission.pending.json').exists())
            before=req.call_count
            with self.assertRaisesRegex(ValueError,'提交结果不确定'):transcribe(self.args)
            self.assertEqual(req.call_count,before)

    def test_explicit_submission_quota_rejection_is_not_uncertain(self):
        policy={'data':{'upload_dir':'fake','oss_access_key_id':'fake','signature':'fake','policy':'fake',
            'x_oss_object_acl':'private','x_oss_forbid_overwrite':True,'upload_host':'https://offline.invalid'}}
        with patch.dict('os.environ',{},clear=True),patch('asr_steps.ff',side_effect=lambda *a,**k:(self.job/'asr/audio.mp3').write_bytes(b'fake')),\
             patch('requests.post',return_value=response(200,{})),\
             patch('requests.request',side_effect=[response(200,policy),response(403,{'code':'AllocationQuota.FreeTierOnly'})]) as req:
            with self.assertRaisesRegex(ValueError,'免费额度'):transcribe(self.args)
            self.assertEqual(req.call_count,2)
            self.assertFalse((self.job/'asr/submission.pending.json').exists())
            self.assertFalse((self.job/'asr/provenance.json').exists())

    def test_successful_resume_and_cache_do_not_resubmit(self):
        self.resumed()
        raw={'transcripts':[{'sentences':[{'text':'测试','words':[{'text':'测试','begin_time':0,'end_time':1000}]}]}]}
        with patch.dict('os.environ',{},clear=True),patch('requests.request',return_value=response(200,{'output':
            {'task_status':'SUCCEEDED','results':[{'subtask_status':'SUCCEEDED','transcription_url':'https://offline.invalid'}]}})) as req,\
             patch('requests.get',return_value=response(200,raw)):
            transcribe(self.args);transcribe(self.args)
            self.assertEqual(req.call_count,1)
            self.assertTrue((self.job/'asr/provenance.json').exists())
