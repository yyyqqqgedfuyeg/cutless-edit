# 可直接调用的分阶段命令

当前字幕规则：默认使用随包提供的得意黑（Smiley Sans）粗体，字号竖屏0.144、横屏0.066，只显示单行白色圆角矩形底黑字（圆角18px）。原稿 `【】` 标记的对应口播文字以1.35倍字号、黄字黑色描边显示，括号不显示。旧任务的颜色、阴影、动画和手动强调配置会被清除，独立花字轨不再输出文字。长字幕先按语义及真实字词边界拆短，日期、数字单位及保护词不拆散；允许单条字号在82%–100%内适配并记录font_scale，仍溢出则报错要求重拆，不换行。旧字幕用 --resplit 重建，随后重新逐条校对。SRT只保存文字与时间，视觉样式由ASS/视频保留。

以下均为实际脚本。`cli.py 阶段名` 与同名独立脚本等价。所有脚本支持 `--help`。在 Windows 将 Python 和路径替换成本机实际值；Python 内部使用参数列表调用 FFmpeg，不拼接用户输入为 shell 命令。

## 0. 环境

完整依赖、安装、自检、API额度不足反馈和续查规则见 [dependencies-and-errors.md](dependencies-and-errors.md)。

项目已有虚拟环境可以直接使用：

```bash
CUTLESS_PY=/root/projects/cutless/.venv/bin/python
CUTLESS_SKILL=/root/projects/cutless/skills/cutless-edit
CUTLESS_JOB=/root/projects/cutless/data/jobs/demo-v1

"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/doctor.py"
```

Python 3.11+，安装依赖：

```bash
"$CUTLESS_PY" -m pip install -r "$CUTLESS_SKILL/scripts/requirements.txt"
```

系统需要 FFmpeg、FFprobe、libass、libx264 和中文字体。检查安装：

```bash
ffmpeg -version
ffprobe -version
fc-match 'Noto Sans CJK SC'
```

Windows 可在字幕命令中指定 `--font 'Microsoft YaHei'`。可用 `FFMPEG_BINARY`、`FFPROBE_BINARY` 指定程序路径。服务配置固定放在 `scripts/.env`，由 `asr_steps.py` 直接读取；无需传 `--env`；其他脚本无需联网。

## 1. 入库：ingest.py

输入：视频、UTF-8原稿、任务目录、主题。输出：`project.json`、规范命名的原片副本、`audio/original.wav`、`qc/probe.json`、方向截图。

原稿必填且必须含有效文字。用户已在对话给出原稿时原样保存再传入；没有原稿先向用户索取，不能用ASR转写或agent编写的稿件替代。

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/ingest.py" \
  --job "$CUTLESS_JOB" --video /path/input.mp4 \
  --script /path/script.txt --title 主题 --layout auto
```

实际横倒的竖拍素材在检查后使用 `--rotate clock` 或 `--rotate cclock` 重新入库。默认先尊重旋转元数据，再施加额外旋转。非标准画幅用 `--layout portrait` 或 `--layout landscape` 指定目标，等比缩放加边。输出尺寸取不超过原生尺寸的精确9:16或16:9偶数画布，上限1080×1920/1920×1080。同一目录不可覆盖入库。

## 2. 音频质检：audio_qc.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/audio_qc.py" --job "$CUTLESS_JOB"
```

输入：当前音轨。输出：`qc/audio.json`、3个定位试听片段。指标包含峰值、疑似削波样本比例、低能量窗口及50ms窗口能量。低能量窗口不等于真实噪声底；不输出伪造SNR。噪声、混响、爆音等需结合试听辨别。无需VAD即可用FFmpeg音量检测区间，ASR用于对照；不把音量检测冒充VAD。

## 3. 降噪：denoise.py

```bash
# 轻度：6dB；中度用 --mode moderate，12dB
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/denoise.py" --job "$CUTLESS_JOB" \
  --mode light --noise-floor -50 --sample-start 8 --reason '持续底噪，先试轻度处理'
```

实际滤镜链如下；脚本先获取原始精确时长再填入 DURATION：

```text
apad=pad_dur=0.2,afftdn=nr=6:nf=-50:tn=1,atrim=duration=DURATION,asetpts=PTS-STARTPTS
```

前置补尾让降噪滤镜处理完整末尾窗，再裁回原音轨长度，避免截掉尾音。保存 PCM 24-bit，不改语速。输出 `audio/denoised.wav`、`qc/denoise.json` 和 `qc/ab-original.wav`、`qc/ab-denoised.wav`。试听后选择：

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/denoise.py" --job "$CUTLESS_JOB" \
  --mode accept --reason '对比通过，底噪改善且人声未明显损伤'
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/audio_qc.py" --job "$CUTLESS_JOB"
```

无需降噪或候选音质更差：

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/denoise.py" --job "$CUTLESS_JOB" \
  --mode bypass --reason '原始音轨可用，保留自然音色'
```

降噪发生在转写之前；有转写或锁定结果后禁止换音轨。若噪声严重且处理中度仍无法满足可懂度，记录问题，不反复叠加重度滤镜。

## 4. 真实转写：transcribe.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/transcribe.py" --job "$CUTLESS_JOB" \
  --timeout 900
```

读取 `DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`、`ASR_MODEL`。当前实现为项目现有的 DashScope 异步文件转写接口，不能任意填入另一个提供商地址而期待协议兼容。由当前48kHz音轨生成单声道16kHz MP3并上传；保存：

- `asr/submission.json`：任务ID、模型、音轨指纹，提交后立即写入。
- `asr/task.json`：云端查询结果。
- `asr/transcription.json`：原始模型结果。
- `asr/words.json`：真实 begin_time/end_time 毫秒时间戳，保留模型字词粒度。
- `asr/provenance.json`：来源及素材、音轨、转写指纹。

超时后重跑同一命令会续查已有任务；完成后命中缓存。服务明确失败时停止，检查原因后在新任务版本重试。缺少时间戳时保留原始结果并报错，不以平均字长补齐。

复用已确认属于同一原片的历史真实转写：

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/transcribe.py" --job "$CUTLESS_JOB" \
  --reuse /path/transcription.json --reuse-source /path/input.mp4 \
  --reuse-provenance /path/asr/provenance.json
```

`--reuse-source` 必须是该转写实际对应的素材，脚本检查与入库原片指纹一致。来源记录必须包含 `provider=aliyun`、模型、真实云端任务ID，且原始JSON及音轨指纹匹配；没有来源记录的历史JSON不能直接复用，更不能手填来源记录冒充真实调用，应重新调用阿里模型。降噪后的任务不复用未降噪原片结果。云端提交只支持阿里DashScope HTTPS接口；原稿不能代替ASR。

## 5. 只读预览字幕：preview_subtitles.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/preview_subtitles.py" --job "$CUTLESS_JOB"
```

输出 `asr/preview.srt`，直接来自模型句子文字和时间，只用于定位。不改错字、不润色、不根据原稿替换内容。

## 6. 剪辑候选：analyze_edits.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/analyze_edits.py" --job "$CUTLESS_JOB"
```

输出 `decisions.json` 和 `script_comparison.json`。同时输出 qc/pauses.json；扫描首尾准备区、原片超过0.35秒的字词间空白、实际音频持续至少0.12秒、低于-35dBFS的区间（-30/-35/-40dB多阈值，包括ASR词内空白）、独立填充词和完全相同的长句。语义口误和不完整重说由agent结合原稿补充；脚本不伪称能自动理解所有语义。低音量acoustic_pause按用户授权自动action=drop，不保留起尾音；其他语义候选初始action=undecided，不自动确认。

## 7. 写入决策：review_edits.py

根据 schemas.md 准备 `reviews.json`：

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/review_edits.py" --job "$CUTLESS_JOB" \
  --reviews /path/reviews.json
```

输入为agent判断后的JSON列表。可以分批复核；可以新增 `kind=manual` 的口误删除范围。未复核项、没有理由的动作不允许进入下一阶段。

## 8. 建立时间轴：build_timeline.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/build_timeline.py" --job "$CUTLESS_JOB" --speed 1.3
```

输出 `timeline.json`：原片帧区间、输出帧起点、段长、总帧数和决策指纹。切口向待删除范围内部对齐帧；拒绝切入字词的区间。精剪后统一1.3倍速；--speed可按用户要求覆盖。每段分别记录source_frames和输出frames，输出长度按帧量化；音频用atempo保持音高，字幕和音效共用各段量化后时间映射。

## 9. 拼接预览：preview_cuts.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/preview_cuts.py" --job "$CUTLESS_JOB" --context 1.5
```

输出每个切口前后真实拼接的 `previews/cut-*.mp4` 和复核清单 `previews/review.json`。检查截音、爆点、跳帧、语义和保留停顿。填写每项 `reviewed`、结论及 `whole_track_reviewed`。这个记录由实际执行检查的agent填写，不代表必需用户逐项审批。

## 9b. 变速后纯人声节奏验收：check_rhythm.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/check_rhythm.py" --job "$CUTLESS_JOB"
```

在锁轨前按timeline实际分段速度拼接人声，输出previews/rhythm-voice.wav及rhythm.json；检查全片及持续至少0.12秒的剩余低音量区间。agent逐项填写reviewed=true、classification=section/expressive/false_positive以及具体reason，最后填写whole_track_reviewed及note。普通卡顿应重剪，不允许用上述分类掩盖；低音量默认整段剪掉，不强制保留气口或起尾音。改时间轴后须重跑此步骤；无试听能力不得伪造检查完成。

## 10. 锁定：lock_timeline.py

新增A/B串行审片：A运行 `prepare_cut_review.py --job ... --planner 实际A身份` 提交剪辑方案与接触表，B实际看有序画面和声音证据，运行 `review_cut_plan.py --job ... --reviews b-reviews.json`。需要少量回帧时由A执行 `apply_peer_adjustments.py --job ... --changes ...`，重新提方案并让B复核。默认窗口0.6秒，切点附近每帧保留；疑难项 `--dense` 提取全部帧。正常短视频跳切允许，不使用固定动作否决阈值。详见 [two-agent-review.md](two-agent-review.md)。

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/lock_timeline.py" --job "$CUTLESS_JOB"
```

输出 `timeline.lock.json`。缺少复核、预览过期、素材不一致会失败。锁定绑定素材、音轨、原稿、原始字幕及时间轴；后续脚本每次校验。需要改剪辑时新建任务版本，不能删除锁文件后覆盖旧任务冒充原版本。

## 11. 字幕专项：subtitles.py

```bash
# 首次从阿里字词时间戳生成待校对草稿，尚不能导出最终成片
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/subtitles.py" --job "$CUTLESS_JOB"

# 依据实际人声校对，并设置全片一致的位置、基础字号、不拆分术语
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/subtitles.py" --job "$CUTLESS_JOB" \
  --corrections /path/corrections.json --terms /path/terms.json \
  --bottom 0.82 --font-size 0.052 --line-chars 14 --font 'Noto Sans CJK SC'

# 只移动整条视频字幕，已校对内容保留
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/subtitles.py" --job "$CUTLESS_JOB" --bottom 0.85

# 黄色文字、黑色3像素描边、半透明阴影；双引号保护颜色中的#
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/subtitles.py" --job "$CUTLESS_JOB" \
  --text-color "#FFD54F" --outline-color "#000000" --outline-width 3 \
  --shadow-color "#00000080" --shadow-depth 1.5 --bold
```

文字、描边、阴影支持 `#RRGGBB` 或 `#RRGGBBAA`，AA为不透明度（FF不透明，00透明）；脚本自动转换成ASS的颜色顺序及透明度，不需手写ASS颜色。`outline_width` 和 `shadow_depth` 为最终画面像素值，支持小数、范围0–12；分别设置为0可关闭描边或阴影。使用 `--no-bold` 取消加粗。

默认外观保存在横竖屏profile的 `subtitle` 对象中：`text_color`、`outline_color`、`outline_width`、`shadow_color`、`shadow_depth`、`bold`。每个任务的最终设置保存在 `subtitles/layout.json`，再次调用未指定的参数保持原值。更改profile影响新任务及旧任务缺失的字段；已有任务需通过命令行或其layout.json调整。改样式后重跑 `render.py`、`verify_export.py` 更新成片。此配置作用于基础字幕；重点花字仍使用独立样式轨。

`bottom` 是字幕块下边缘相对画面高度的位置，数值增大向下移动；`font-size` 是字号/画面宽度。横屏默认0.91和0.066，竖屏默认0.82和0.144。默认单行上限；line-chars是拆句目标长度，允许小幅浮动以保护完整短语。基础字号不够时单条适配到82%，仍不够放就修改正式字幕断句。校时只允许每次在原字幕边界±250ms内调整并检查重叠，不改视频切点。跨越大量时间的纠错应重新对齐，不得用小步累加逃避复核。

输出 `subtitles/captions.json`、`layout.json`、`draft.srt`、`draft.ass` 和生成指纹。草稿不能当作最终字幕交付。SRT不包含字体与位置，排版以ASS/烧录成片为准。

## 11b. 必须逐条参考原稿确认：review_subtitles.py

agent阅读原稿和全部字幕，核对实际口播、专有名词、数字、否定词、断句及时间对齐。先使用上面的corrections修正ASR错误，再按schemas.md编写全量校对记录（每条都要确认，无需修改的条目也包括）：

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/review_subtitles.py" --job "$CUTLESS_JOB" \
  --reviews /path/subtitle-reviews.json
```

记录必须逐条覆盖当前cue，引用用户原稿实际存在的对应句或上下文，提交当前文字/时间、对齐确认和理由。通过后写入 `subtitles/review.json` 并生成 `final.ass`、`final.srt`。`render.py` 和 `verify_export.py` 都检查此门槛。

之后修改文字或时间会使校对记录失效，必须重新校对；只改变位置、颜色、字号等样式时，重跑subtitles会保留有效文字校对并更新最终样式。直接修改ASS/SRT会被指纹检查拒绝。代码验证记录的完整性与版本一致性，不能替agent完成语义判断，也不能证明虚构的“已校对”声明是真的。

旧任务缺少阿里来源、字幕校对或新增锁定字段时，不允许沿用旧成片路径绕过门槛；在新任务版本完成完整流程，保留旧结果用于追溯。

## 12a. 必做：逐项讲解语义审阅

agent审阅全文后，先准备 [point-review.md](point-review.md) 的判定文件（有独立展开时列出每项；没有时明确判定并说明理由）：

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/review_points.py" --job "$CUTLESS_JOB" \
  --review /path/point-review.json
```

缺少这项记录，或实际效果漏项，style/render/verify_export 均拒绝执行。每项独立展开必须有120%画面推进和一个assets音效；字幕阶段不能代替此步骤。用户指定既有剪辑视频补效果时，用 [finish_existing.py 模式](point-review.md)；该独立入口不重建原视频剪辑。

## 12. 风格：style.py

```bash
# 无效果也执行一次，创建明确的空风格轨道（关闭默认轻调色）
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/style.py" --job "$CUTLESS_JOB" --grade none

# 加少量重点词和同步画面推进
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/style.py" --job "$CUTLESS_JOB" \
  --events /path/events.json --grade natural

# 提供已有授权BGM；默认-24dB并在人声期间自动避让
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/style.py" --job "$CUTLESS_JOB" \
  --events /path/events.json --bgm /path/music.wav \
  --bgm-source '自有/授权素材，记录来源及授权范围' --bgm-gain -24
```

输出 `style/style.json`、`overlay.ass`。重点事件不能重叠；普通事件缩放不超过1.08倍，逐项展开讲解使用 `expanded-point` 固定1.20倍。`natural` 仅轻调对比度和饱和度，不是自动白平衡修复；默认 `auto` 按画幅选择 `portrait-fresh` 或 `landscape-neutral`，不合适时明确使用 `--grade none`。素材索引见 `assets/manifest.json`。不提供BGM时不联网搜索或自行下载音乐。

## 原片首尾缓冲（锁轨前）

`build_timeline.py --speed 1.3 --source-buffer 0.35` 默认在原片首个、末个保留字词前后各留0.35秒，向上按原片整帧对齐；`--source-buffer 0` 可按用户要求关闭。缓冲保留真实连续画面及原声，以原速播放，正文速度不变。低音量删除决策只用于正文，构建时恢复受保护的首尾区间，必要时也恢复被低音量剪掉的首尾字词尾部。原片可用时长不足会记录实际保留量，不补静帧或静音。

输出时间轴的 `source_buffers` 记录源区间、输出区间、实际可用时长及增加帧数；缓冲片段包含 `buffer_kind=head/tail`。字幕和特效按新时间轴映射。`check_rhythm.py` 用 `source_buffer_boundary` 标记完全落在缓冲区内的低音量候选；agent复核其保留作用，不能把静音检测结果直接再删掉。既有成片改缓冲须另建任务，从原片重新生成预览并校对首尾字幕时间，不修改原锁定记录。

## 13. 导出：render.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/render.py" --job "$CUTLESS_JOB"
```

按锁定帧区间逐段处理，采用无损中间视频和PCM音频；成片阶段一次有损编码。字幕在画面缩放之后合成，位置不跟随缩放。BGM循环长度限制为锁定片长，并压低人声期间的音乐。输出 `output/final.mp4` 和 `render.json`，编码H.264/AAC、yuv420p、faststart；临时片段完成后清除。输出生成失败时不会把临时文件当成成片。

## 14. 导出校验：verify_export.py

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/verify_export.py" --job "$CUTLESS_JOB"
```

核查锁定绑定、文件指纹、精确帧数、音画时长、画幅、全片解码；抽取首中尾截图。输出 `output/verification.json`。技术检查通过不代表已经听过全片，agent还需检查截图、重点效果及切口音质。测试摘要必须区分真实API、缓存复用、合成测试和未完成的听感审片。


## 素材使用与弹跳

素材清单、BGM/音效使用示例见 [style-library.md](style-library.md)。内置素材已随技能提供，无需生成。

```bash
# 固定字幕底部锚点，缩放回弹；横屏建议0.04 / 240ms
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/subtitles.py" --job "$CUTLESS_JOB" \
  --animation bounce --bounce-strength 0.08 --bounce-ms 320 --animated-cues s0001,s0005
# --animated-cues '' 表示全部；--animation none 关闭
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/style.py" --job "$CUTLESS_JOB" \
  --events /path/events.json --grade auto --bgm-preset auto --bgm-gain -14
```

字幕动画写入ASS和烧录视频，SRT本身不支持动画。幅度范围0–0.15，时长120–800ms；小于250ms的短字幕不做弹跳，避免闪烁。样式修改后重新导出即可；字幕语义校对始终由agent执行，脚本不会判定原稿与口播是否一致。


圆角背景支持 `subtitles.py --background --background-color '#193C5EE6' --background-radius 16 --background-padding 18`；`--no-background` 关闭。颜色含可选不透明度通道；背景和动画详情见 [style-library.md](style-library.md)。新字体度量依赖Pillow（已列入requirements）；使用字体名需系统fontconfig，或提供字体文件路径。


句内强调另有独立入口 `emphasize_subtitles.py --job /path/job --emphasis /path/emphasis.json`（等价于subtitles.py的同名参数），见 [句内强调文档](inline-emphasis.md)。


## 字幕校对后：重点词审批与逐项讲解效果

原稿无 `【】` 时，agent先生成重点词方案并展示给用户。方案JSON为 `{"reason":"选择重点的理由","items":[{"id":"s0001","text":"与当前字幕完全相同","spans":[{"text":"重点词","occurrence":1}]}]}`；只选择文字，不自定义样式。

```bash
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/review_emphasis.py" --job "$CUTLESS_JOB" --proposal /path/proposal.json
# 此处等待用户明确审批；仅在实际收到用户回复后记录，不能自动执行下面的批准示例。
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/review_emphasis.py" --job "$CUTLESS_JOB" --decision approve --user-response '用户实际明确批准的原话'
# 拒绝使用 --decision reject；按反馈重拟方案。
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/subtitles.py" --job "$CUTLESS_JOB"
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/review_subtitles.py" --job "$CUTLESS_JOB" --reviews /path/subtitle-reviews.json
"$CUTLESS_PY" "$CUTLESS_SKILL/scripts/style.py" --job "$CUTLESS_JOB" --events /path/expanded-points.json --grade none
```

若已有逐条文字校对记录，批准后重跑subtitles可保留有效记录；没有有效重点词审批时，最终字幕和成片生成被拒绝。审批绑定原稿、当前字幕文字/时间及方案指纹。用户原稿不可改写成带标记的版本来绕过审批。

逐项事件使用剪辑后真实字词起点，JSON格式见 [schemas.md](schemas.md)。`ding-ding-ding` 位于 `assets/audio/叮叮叮.mp3`，`swish` 位于 `assets/audio/唰.mp3`；均为用户提供并要求移动的本地文件。每项选择一个，缺省自动添加叮叮叮。音效和120%缩放共用同一逐项展开事件及真实字词起点，音效只播放一次，不循环，尾音在事件end截断；画面120%居中推进仅覆盖“序号＋简短标题”的引出语，到zoom_end立即恢复100%；end仍表示该项完整讲解结束，音效尾音不因缩放复位而提前截断。每一项均需记录zoom_end和zoom_intro_quote，下一项再次执行相同动画。背景、人声、字幕时序均不改动。不得把普通一句话列举标为expanded-point来绕过触发条件。
