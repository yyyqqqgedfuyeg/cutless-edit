# Agent填写的数据格式

时间单位：决策及字幕/效果JSON为秒；原始ASR为毫秒；锁定片段为帧。不要混用。

## 复核剪辑

`review_edits.py --reviews reviews.json` 输入列表，以下id仅为格式示例，实际读取当前任务候选：

```json
[
  {"id":"c0001","action":"keep","reason":"转折前的强调停顿，保留完整语气"},
  {"id":"c0002","action":"shorten","keep_seconds":0.24,"reason":"普通句间吸气过长，保留前后发音余量"},
  {"id":"m0001","kind":"manual","start":12.1,"end":14.9,"action":"drop","reason":"不完整的错误起句，后面有完整正确重录","context":"实际原文及对应后续重录位置"}
]
```

动作只有 `keep` / `drop` / `shorten`。`keep_seconds` 至少0.12秒；shorten用于纯空白，不能包含有效人声。已有候选的时间范围不通过review命令修改：需要改变范围时保留旧候选并新增正确的manual范围，清楚说明替代关系。

## 切口复核

`preview_cuts.py` 自动生成 `previews/review.json`。agent检查每个 `cut-*.mp4` 以及所有保留停顿后设置对应 `reviewed: true` 并记录note；整体片段顺序、首尾内容和语义检查完后设置 `whole_track_reviewed: true` 和非空note。不要改其中的时间轴/决策指纹。

## 正式字幕校对

先执行 `subtitles.py` 得到稳定cue id，随后传入：

```json
[
  {"id":"s0001","text":"校对后的短字幕","reason":"按真实发音修正识别错字，保持单行"},
  {"id":"s0002","text":"截止日期为10月11日","start":8.12,"end":10.36,"reason":"试听确认边界，微调字幕与人声对齐"}
]
```

校时限相对生成时边界±250ms，防止无意改写时间关系；真正的时间漂移需进一步对齐。文本不能增加没说过的信息。禁止手动换行；过长字幕按真实字词边界拆成多个单行片段。术语输入为字符串列表，如 `["中国农业发展银行","10月11日"]`，只属于当前任务，不进入通用预设。

需要合并/拆分字幕块时，agent可在 `subtitles/captions.json` 内修改cue列表：id唯一、顺序单调、不重叠、全部在锁定片长内，依据相应字词边界，不改变 `lock_sha256`；重新运行 `subtitles.py` 校验并生成ASS/SRT。此文件属于锁定后字幕层，不能改原始ASR或时间轴。

## 重点词与关键帧

正式字幕进入此阶段前，必须完成下面的逐条原稿校对。排版、修改文字本身不等于已校对。

## 最终字幕的全量校对记录

`review_subtitles.py --reviews subtitle-reviews.json`：

```json
{
  "reviewer": "agent",
  "items": [
    {
      "id": "s0001",
      "text": "截止日期为10月11日",
      "start": 8.12,
      "end": 10.36,
      "script_quote": "网申截止10月11号",
      "decision": "asr_corrected",
      "alignment_checked": true,
      "reason": "对照用户原稿及实际口播纠正日期误识别，并核对字词边界"
    }
  ]
}
```

引文必须在当前用户原稿中真实存在，以上内容仅示例。每个cue都要出现一次；text/start/end必须与当前captions.json完全一致。`decision` 支持 `matches_script`（与原稿一致）、`asr_corrected`（已修ASR识别错误）、`spoken_variant`（保留合理口语改写）、`spoken_addition`（原稿外有效补充，引用对应上下文并说明）。仍有疑点的条目不能通过。不要用自动循环把建议或所有cue批量标成已确认。

## 重点事件格式

```json
[
  {"start":3.2,"end":4.6,"template":"keyword-pop","text":"关键结论","zoom":1.04},
  {"start":16.1,"end":18.0,"template":"number-highlight","text":"10月11日截止","zoom":1.03},
  {"start":25.0,"end":27.0,"template":"gentle-push","text":"","zoom":1.025}
]
```

使用剪辑后时间，不使用原片时间。模板名从素材索引选；事件不重叠，重点文案须符合画幅长度限制。关键帧在事件内平滑推进再恢复，基础字幕在缩放后合成。`gentle-push` 的text留空，只改变画面。

## 素材扩展

模板目前支持 `animation: pop / underline / fade / none / bounce / label / bracket / char-bounce / char-grow` 和 `y_ratio`。增加模板JSON后登记 `assets/manifest.json` 中的id/path/origin。新的动画机制需要先扩展渲染器并测试，不能只在JSON写一个不存在的效果名称。

BGM由 `style.py --bgm ... --bgm-source ...` 导入任务并记录指纹，不在索引里保存密钥或外部账户信息。内置BGM也可通过 `--bgm-preset auto|素材id` 选择。重点事件可增加 `"sound":"soft-tick"`（也支持gentle-chime、soft-whoosh）与 `"sound_gain_db":-18`，音效在事件start播放，尾音允许延续到事件之后但会在成片末尾裁断。增益范围-40至-6dB；普通事件未指定sound不加音效；通过逐项展开讲解校验的 expanded-point 事件未指定sound时自动添加 ding-ding-ding，音效与缩放共用事件起点，音效不越过事件end。所有时间均为剪辑后时间。音效文件复制到任务并保存指纹。


## 逐项展开讲解事件

在文字校对完成、重点词来源确认后，由agent对照原稿与实际口播创建。下面时间和引文仅为格式示例，实际须对应当前素材：

```json
[
  {"start":3.2,"end":12.4,"template":"expanded-point","sequence_group":"岗位分类","point_index":1,
   "expanded_explanation":true,"script_quote":"第一类，对应观点及随后完整讲解原文",
   "reason":"第一项包含独立观点、说明与例子，并非一句话枚举",
   "sound":"ding-ding-ding","sound_gain_db":-24,"zoom":1.2,"zoom_transition_seconds":0.25},
  {"start":13.1,"end":22.0,"template":"expanded-point","sequence_group":"岗位分类","point_index":2,
   "expanded_explanation":true,"script_quote":"第二类，对应观点及随后完整讲解原文",
   "reason":"第二项也有独立展开说明，与前项组成逐项讲解",
   "sound":"swish","sound_gain_db":-24,"zoom":1.2,"zoom_transition_seconds":0.25}
]
```

同组至少两项，point_index按出现顺序递增、不重复；每项至少1秒只是技术下限，不能代替agent语义判断。agent必须确认每项独立展开，简单“第一、第二、第三”一句话列举不触发。引文需真实存在于当前原稿。事件start对齐锁定后真实字词起点（容差一帧或40ms），end为该项讲解结束，事件不能重叠。固定居中放大1.20倍，平滑推进默认0.25秒，允许0.08–1秒；保持至end，end起立即恢复1.00倍。音效优先ding-ding-ding，轻转接可选swish，每项仅一个。默认-24dB、允许-40至-18dB，实测限制在该段人声RMS的25%及峰值的40%以内，再通过侧链避让；不能宣称这些数值代替试听。独立文字效果不输出。

## 无标记原稿的重点词审批

`review_emphasis.py --proposal proposal.json` 输入对象：

```json
{"reason":"突出本段核心信息，避免大面积强调","items":[
  {"id":"s0001","text":"当前字幕完整文字","spans":[{"text":"核心信息","occurrence":1}]}
]}
```

items只包含要强调的cue，text必须和当前cue一致；spans只含text及可选occurrence，不接受自定义颜色或动画。agent负责语义选择，并向用户展示具体重点与上下文；脚本不会替agent挑重点。保存后处于待审批状态。明确批准才记录 `--decision approve --user-response 用户实际回复`；拒绝用reject并重提方案。审批记录由脚本保存方案指纹，原稿和cue绑定变化即失效。禁止自动批准、伪造用户回复、修改原稿添加【】绕过审批。已有【】时不运行此流程，遵循原标记。
