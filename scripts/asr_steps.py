"""DashScope real transcription with scripts/.env and resumable task polling."""
from pathlib import Path
import shutil
import time
from urllib.parse import urlparse

from service_errors import checked_json, service_failure

from common import digest, ff, project, read, require_aliyun, require_script, srt, unlocked, verify_source, write


ENV_PATH = Path(__file__).resolve().parent / ".env"

def load_api_config():
    from dotenv import dotenv_values
    if not ENV_PATH.is_file():
        raise ValueError("缺少转写配置：请在Skill的scripts/.env中填写DASHSCOPE_API_KEY")
    config=dotenv_values(ENV_PATH,interpolate=False)
    if not config.get("DASHSCOPE_API_KEY"):
        raise ValueError("scripts/.env缺少DASHSCOPE_API_KEY")
    return config


def extract_words(raw):
    words = []
    for transcript in raw.get('transcripts', []):
        for sentence in transcript.get('sentences', []):
            if sentence.get('text', '').strip() and not sentence.get('words'):
                raise ValueError('模型缺少字词时间戳；保留原始JSON，不推算时间')
            for word in sentence.get('words', []):
                if word.get('begin_time') is None or word.get('end_time') is None:
                    raise ValueError('模型字词时间戳不完整')
                words.append({**word, 'sentence_id': sentence.get('sentence_id'),
                    'channel_id': transcript.get('channel_id')})
    if not words:
        raise ValueError('没有识别到语音')
    # Channel zero is submitted; mixed speaker timelines require a different editing workflow.
    last = -1
    for word in words:
        if not last <= word['begin_time'] < word['end_time']:
            raise ValueError('时间戳乱序或长度为零，不能直接用于剪辑')
        last = word['begin_time']
    return words


def transcribe(a):
    job = Path(a.job).resolve()
    unlocked(job)
    p = verify_source(job)
    require_script(job/'script.txt')
    if p['audio_review'] != 'accepted':
        raise ValueError('先质检并通过 denoise.py --mode accept/bypass 确定音轨')
    audio_hash = digest(job/p['active_audio'])
    provenance = job/'asr/provenance.json'
    if provenance.exists():
        require_aliyun(job)
        old = read(provenance)
        if (old['audio_sha256'] == audio_hash and old['words_sha256'] == digest(job/'asr/words.json')
                and old['raw_sha256'] == digest(job/'asr/transcription.json')
                and old['source_sha256'] == p['source_sha256']):
            print('命中同一音轨的真实转写缓存，无新增API调用')
            return
        raise ValueError('已有转写与音轨不一致，请创建新任务版本')
    if a.reuse:
        if not getattr(a,'reuse_provenance',None):
            raise ValueError('复用转写必须提供 --reuse-provenance，确认阿里模型及真实云端任务来源')
        proof=read(a.reuse_provenance)
        if (proof.get('provider')!='aliyun' or not proof.get('task_id') or not proof.get('model')
                or proof.get('raw_sha256')!=digest(a.reuse) or proof.get('audio_sha256')!=audio_hash):
            raise ValueError('复用结果缺少可匹配的阿里真实转写来源记录')
        if not a.reuse_source or digest(a.reuse_source) != p['source_sha256']:
            raise ValueError('--reuse 需要 --reuse-source，且必须与入库原片指纹一致')
        if p['active_audio'] != 'audio/original.wav':
            raise ValueError('已降噪的任务不能使用原片旧转写缓存')
        raw = read(a.reuse)
        words = extract_words(raw)
        write(job/'asr/transcription.json', raw)
        mode, task_id, model = 'reused-real-transcription', proof['task_id'], proof['model']
    else:
        import requests
        from requests_toolbelt.multipart.encoder import MultipartEncoder
        config = load_api_config()
        key = config['DASHSCOPE_API_KEY']
        base = config.get('DASHSCOPE_BASE_URL', 'https://dashscope.aliyuncs.com/api/v1').rstrip('/')
        address=urlparse(base)
        if address.scheme!='https' or not (address.hostname or '').endswith('.aliyuncs.com'):
            raise ValueError('本Skill要求使用阿里DashScope HTTPS模型接口')
        model = config.get('ASR_MODEL', 'qwen-audio-3.0-asr-flash-filetrans')
        headers = {'Authorization':'Bearer '+key}
        task_id=None
        def api(method, path, stage, **kw):
            try:r = requests.request(method, base+path, timeout=(15,120), **kw)
            except requests.RequestException:
                service_failure(job,stage,task_id=task_id,kind='network')
            return checked_json(r,job,stage,task_id)
        submitted = job/'asr/submission.json'
        pending=job/'asr/submission.pending.json'
        if pending.exists() and not submitted.exists():
            raise ValueError('上次提交结果不确定；先在云端核查任务，避免重复计费。确认未创建任务后再移除asr/submission.pending.json重试；如已创建，恢复submission.json中的真实task_id续查。')
        if submitted.exists():
            submission = read(submitted)
            if submission['audio_sha256'] != audio_hash:
                raise ValueError('待查询任务属于另一音轨')
            task_id = submission['task_id']
            model = submission['model']
            print('恢复查询已提交任务，不重新上传计费',flush=True)
        else:
            mp3 = job/'asr/audio.mp3'
            ff(['-i',job/p['active_audio'],'-ac','1','-ar','16000','-c:a','libmp3lame','-b:a','64k',mp3],job)
            policy = api('GET','/uploads','获取上传凭证',headers=headers,params={'action':'getPolicy','model':model})['data']
            if mp3.stat().st_size > float(policy.get('max_file_size_mb',2048))*1024*1024:
                raise ValueError('音频超过服务上传限制')
            object_key = policy['upload_dir'].rstrip('/')+'/'+mp3.name
            with mp3.open('rb') as stream:
                body = MultipartEncoder(fields={'OSSAccessKeyId':policy['oss_access_key_id'],
                    'Signature':policy['signature'],'policy':policy['policy'],
                    'x-oss-object-acl':policy['x_oss_object_acl'],
                    'x-oss-forbid-overwrite':str(policy['x_oss_forbid_overwrite']),
                    'key':object_key,'success_action_status':'200','file':(mp3.name,stream,'audio/mpeg')})
                try:
                    response = requests.post(policy['upload_host'],data=body,
                        headers={'Content-Type':body.content_type},timeout=(15,600))
                except requests.RequestException:service_failure(job,'音频上传',kind='network')
                if not response.ok:
                    service_failure(job,'音频上传',response.status_code)
            write(pending,{'audio_sha256':audio_hash,'model':model,'state':'submission_unconfirmed'})
            try:
                result = api('POST','/services/audio/asr/transcription','提交转写',
                headers={**headers,'X-DashScope-Async':'enable','X-DashScope-OssResourceResolve':'enable'},
                json={'model':model,'input':{'file_urls':['oss://'+object_key]},'parameters':{'channel_id':[0]}})
            except ValueError:
                error=read(job/'asr/error.json') if (job/'asr/error.json').exists() else {}
                if error.get('http_status') and 400<=error['http_status']<500:pending.unlink(missing_ok=True)
                raise
            task_id = result.get('output',{}).get('task_id')
            if not task_id:service_failure(job,'提交转写',body={'code':'MissingTaskId'})
            write(submitted,{'task_id':task_id,'audio_sha256':audio_hash,'model':model})
            pending.unlink(missing_ok=True)
        deadline = time.monotonic()+a.timeout
        while time.monotonic()<deadline:
            task = api('GET',f'/tasks/{task_id}','查询任务',headers=headers)
            state = task['output']['task_status']
            if state == 'SUCCEEDED':
                break
            if state in {'FAILED','CANCELED','UNKNOWN'}:
                service_failure(job,'云端任务 '+state,body=task,task_id=task_id)
            write(job/'asr/task.json', task)
            time.sleep(3)
        else:
            raise ValueError('查询超时；重跑本命令可续查，不重新提交')
        results=task['output'].get('results',[])
        if len(results)!=1 or results[0].get('subtask_status')!='SUCCEEDED':
            service_failure(job,'文件识别',body=task,task_id=task_id)
        write(job/'asr/task.json', task)
        try:response=requests.get(results[0]['transcription_url'],timeout=(15,120))
        except requests.RequestException:service_failure(job,'转写下载',task_id=task_id,kind='network')
        raw=checked_json(response,job,'转写下载',task_id)
        write(job/'asr/transcription.json',raw)
        words=extract_words(raw)
        mode='cloud'
    if max(w['end_time'] for w in words)/1000 > p['duration']+.1:
        raise ValueError('转写时间超过原片时长，可能复用了错误结果')
    write(job/'asr/words.json',{'unit':'milliseconds','words':words})
    write(provenance,{'provider':'aliyun','model':model,'mode':mode,'task_id':task_id,'source_sha256':p['source_sha256'],
        'audio_sha256':audio_hash,'words_sha256':digest(job/'asr/words.json'),
        'raw_sha256':digest(job/'asr/transcription.json')})
    (job/'asr/error.json').unlink(missing_ok=True)
    print(f'真实转写完成：{len(words)}个字词；时间戳未经推算')


def preview_subtitles(a):
    job=Path(a.job).resolve()
    unlocked(job)
    require_aliyun(job)
    provenance=read(job/'asr/provenance.json')
    if digest(job/'asr/transcription.json')!=provenance['raw_sha256']:
        raise ValueError('原始转写被改动')
    cues=[]
    for transcript in read(job/'asr/transcription.json').get('transcripts',[]):
        for sentence in transcript.get('sentences',[]):
            if sentence.get('text'):
                cues.append({'start':sentence['begin_time']/1000,'end':sentence['end_time']/1000,
                    'text':sentence['text']})
    srt(job/'asr/preview.srt',cues)
    print('已生成只读预览字幕 asr/preview.srt；此阶段不校改文字')
