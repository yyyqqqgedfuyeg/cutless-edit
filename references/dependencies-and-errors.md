# 依赖、自检与 API 报错

## 必需依赖

| 依赖 | 用途 / 要求 |
| --- | --- |
| Python 3.11+ | 脚本与文件哈希；当前已在Python 3.12.11测试 |
| requests >=2.32,<3 | 阿里HTTP API、任务查询、结果下载 |
| requests-toolbelt >=1.0,<2 | 流式上传音频 |
| python-dotenv >=1.0,<2 | 读取脚本同目录scripts/.env |
| Pillow >=10,<13 | 字体实际字宽、TTC字体子集与圆角背景尺寸 |
| FFmpeg + FFprobe | 编解码、探测、降噪、剪辑、混音、字幕；不能只装imageio的简版二进制 |
| libass / libx264 / libmp3lame / AAC | ASS字幕、H.264成片、MP3上传音频与AAC音轨 |
| fontconfig + 中文字体 | 按字体名称找到实际字体；默认Noto Sans CJK SC，需能解析到正确字体族 |

FFmpeg还需PCM、MJPEG、PNG编码器及 `afftdn`、`astats`、`silencedetect`、`loudnorm`、`ass`、`sidechaincompress`、`zoompan`、`eq`、`colorbalance`、`alimiter`、`amix`、`apad`、`adelay`、`aresample` 等滤镜。doctor逐项报告缺失，不只检查命令是否存在。当前环境FFmpeg 4.2.7已完成渲染测试；其他构建以doctor和实际导出验证为准。

Linux（Debian/Ubuntu）示例：

```bash
sudo apt-get install ffmpeg fontconfig fonts-noto-cjk
python3 -m venv .venv
.venv/bin/python -m pip install -r /path/cutless-edit/scripts/requirements.txt
.venv/bin/python /path/cutless-edit/scripts/doctor.py
```

没有pip时先运行 `python -m ensurepip`。Windows需安装包含上述能力的FFmpeg、FFprobe、fontconfig及中文字体，加入PATH；用 `doctor.py --font 'Microsoft YaHei'` 验证，再以相同字体名生成字幕。只装字体而没有fontconfig会影响逐字效果与背景测量。`FFMPEG_BINARY`、`FFPROBE_BINARY` 可指定路径。字体文件路径仅用于字体测量检查；正式字幕应使用已安装且可解析的字体族名。

`doctor.py` 是离线检查，不读取密钥、不发起计费调用，也不能判断账号余额或模型权限。临时渲染使用无损视频和PCM，磁盘空间需足以容纳原片、工作副本与中间片段，不能仅按最终MP4大小估计。

## .env 与原稿

原视频、用户提供的UTF-8原稿是必需输入。配置固定放在 `scripts/.env`，由 `asr_steps.py` 按自身路径读取，不接受 `--env` 参数；仅转写阶段读取，文件必须存在；已有同音轨的有效真实转写缓存可以离线复用。

```dotenv
DASHSCOPE_API_KEY=填写真实密钥
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/api/v1
ASR_MODEL=qwen-audio-3.0-asr-flash-filetrans
```

API配置只来自 `scripts/.env`，系统环境变量不会覆盖；不展开 `${变量}`，请填入实际配置值。接口须为阿里原生DashScope HTTPS端点，不能填 `/compatible-mode/v1`。模型需支持异步文件转写及真实字词时间戳；模型或地域不可用时由agent核对当前阿里文档，不能默默替换为假时间戳。模型访问权限、额度、网络连通性只能在实际请求中确认。仅为测试错误处理不需要真实消耗额度。

## 错误反馈

命令失败退出码为1，终端给出中文原因、服务错误码与处理建议，并保存 `任务/asr/error.json`。记录含阶段、HTTP状态、分类、错误码、request_id、已有task_id；不写入密钥、原始响应正文或签名URL。HTTP拒绝、HTTP 200中的错误、异步任务FAILED、单文件识别失败均可反馈。

| 服务错误 | 用户反馈 | 下一步 |
| --- | --- | --- |
| AllocationQuota.FreeTierOnly / Free allocated quota exceeded | 免费额度耗尽或过期 | 核查模型额度及“免费额度用完即停”设置；用户决定是否付费 |
| Arrearage / isv.OUT_OF_SERVICE | 欠费或余额不足 | 核查密钥所属账号账单、余额与服务状态 |
| Throttling.AllocationQuota | 调用配额受限，不直接等同欠费 | 核查TPS/TPM及分配配额，减少并发或申请提升 |
| Throttling.RateQuota / 其他429 | 请求限流或服务繁忙 | 降频，稍后重试 |
| InvalidApiKey / 401 | 鉴权失败 | 核对密钥与地域，不输出密钥 |
| 403 / AccessDenied | 模型或工作空间权限不足 | 核查开通状态、权限与地域 |
| 5xx | 服务暂时异常 | 稍后续查 |
| 网络超时/连接失败 | 网络异常 | 按任务是否已提交决定续查，避免重复计费 |

上述分类参考[阿里官方错误码](https://www.alibabacloud.com/help/zh/model-studio/error-code)（2026-10-10核对）。同为429，免费额度耗尽、吞吐配额和普通限流不能统一说成余额不足。

示例反馈：

```text
失败：模型免费额度耗尽或已过期（提交转写；HTTP 403；AllocationQuota.FreeTierOnly）。
到百炼控制台核对模型额度及“免费额度用完即停”设置；是否启用付费由用户决定，
脚本不会切换付费或模型。详情：asr/error.json
```

agent应直接说明失败原因、停在哪一步、保留了什么、恢复条件。额度不足时停止后续字幕/导出流程，不伪造成片，不反复重试，不擅自切换模型或打开付费。

## 恢复与计费保护

- 有 `submission.json`：重跑同一转写命令只查询原task_id。真实结果缓存命中不调用API。
- 提交明确被4xx拒绝：保留错误报告，修复额度/配置后可重跑；脚本不自行重试。
- 提交超时、响应不明或5xx：保留 `submission.pending.json`，再次执行先停止，防止云端已接受却重复计费。先核对云端任务；存在则将真实task_id及原audio_sha256/model恢复至submission.json继续查询；确认未创建才移除pending标记重新提交。不得猜造task_id。
- 异步任务已FAILED：保留任务ID和错误报告；排除失败原因后创建新任务版本再运行，不循环查询已失败任务。
- 成功后清除旧error.json。一次服务成功不代表后续永远有额度。

## 验证命令

```bash
python scripts/doctor.py
python -m unittest discover -s scripts -p 'test_*.py' -q
```

测试使用临时合成素材和模拟API响应，覆盖实际FFmpeg导出、字幕动画位移/放大、时间轴锁定、校对记录门禁、额度/欠费/限流反馈、任务续查、提交超时防重复。不会加载真实.env或发送真实云端请求。人工/agent仍需对实际视频进行听感检查和逐条字幕语义校对。
