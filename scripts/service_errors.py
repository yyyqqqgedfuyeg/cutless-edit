"""Safe, actionable DashScope errors; never persist raw error bodies or credentials."""
import re
from pathlib import Path
from common import write


def token(value):
    value=str(value or '')
    return value if re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value) and not value.startswith('sk-') else ''


def error_fields(body):
    if not isinstance(body,dict):return '', '', ''
    nested=body.get('output') or body.get('error')
    candidates=[body]+([nested] if isinstance(nested,dict) else [])
    if isinstance(nested,dict):
        candidates += [r for r in nested.get('results',[]) if isinstance(r,dict) and r.get('subtask_status')!='SUCCEEDED']
    for item in reversed(candidates):
        if item.get('code') or item.get('message'):
            return token(item.get('code')), str(item.get('message','')).lower(), token(body.get('request_id'))
    return '', '', token(body.get('request_id'))


def service_failure(job,stage,status=None,body=None,task_id=None,kind=None):
    code,message,request_id=error_fields(body)
    if kind=='network':
        category,summary,action='network','网络连接失败或超时','检查网络后重试；已有任务ID时只续查，不重复提交。提交响应丢失时先核查云端任务。'
    elif code=='AllocationQuota.FreeTierOnly' or 'free allocated quota exceeded' in message:
        category,summary,action='free_quota','模型免费额度耗尽或已过期','到百炼控制台核对模型额度及“免费额度用完即停”设置；是否启用付费由用户决定，脚本不会切换付费或模型。'
    elif code in {'Arrearage','Account.Arrearage','isv.OUT_OF_SERVICE'} or 'insufficient balance' in message:
        category,summary,action='billing','账户欠费或余额不足，API服务被拒绝','核对API Key所属账号的费用与余额；充值或恢复服务后重试。'
    elif code=='Throttling.AllocationQuota' or code=='insufficient_quota':
        category,summary,action='allocation_limit','调用配额受限（不直接等同余额不足）','检查模型TPS/TPM及分配配额，降低并发或稍后重试；需要时在控制台申请提升限额。'
    elif code.startswith('Throttling') or code in {'LimitRequests','limit_requests'} or status==429:
        category,summary,action='rate_limit','API请求限流或服务繁忙','降低请求频率，稍后手动重试；脚本不循环重新提交。'
    elif code=='InvalidApiKey' or status==401:
        category,summary,action='authentication','API Key无效或鉴权失败','检查.env密钥及所属地域；不要在聊天或日志中粘贴密钥。'
    elif status==403 or code in {'AccessDenied','CommodityNotPurchased'}:
        category,summary,action='access','模型或工作空间无访问权限','检查模型开通状态、账号权限及接口地域。'
    elif status and status>=500:
        category,summary,action='server','阿里服务暂时异常','稍后重试；若已有任务ID，保留原任务续查。'
    else:
        category,summary,action='service','转写服务返回错误','按错误码及request_id在百炼控制台排查模型、参数、文件与任务状态。'
    record={'stage':stage,'category':category,'http_status':status,'code':code or 'Unknown',
        'request_id':request_id,'task_id':token(task_id),'summary':summary,'action':action,
        'automatic_retry':False}
    write(Path(job)/'asr/error.json',record)
    detail=f'；HTTP {status}' if status else ''
    raise ValueError(f'{summary}（{stage}{detail}；{record["code"]}）。{action} 详情：asr/error.json')


def checked_json(response,job,stage,task_id=None):
    try:body=response.json()
    except ValueError:body={}
    if not response.ok or (isinstance(body,dict) and body.get('code')):
        service_failure(job,stage,response.status_code,body,task_id)
    if not isinstance(body,dict) or not body:
        service_failure(job,stage,response.status_code,{'code':'InvalidResponse'},task_id)
    return body
