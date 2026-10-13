# 先提剪辑方案，再独立审片

沿用本skill已有口误、停顿、速度、字幕和效果规则；新增的是锁轨前的独立检查，不重新设计整个剪辑器。短视频口播的跳切很正常：手势已结束后换姿态、下一句抬另一只手、段落切换、少量姿态位移都可以接受。重点处理明显未完成手势被截断、突兀来回抽动，以及可能截字、截起音或截尾音的切点。不设置固定的动作幅度/像素跳跃否决门槛，也不为接顺手势恢复口误或重说。

## 两个agent的职责

1. **Agent A，剪辑者**：完整读原稿与真实转写，按现有规则提出删段，准备实际时间轴、切口预览及证据。此时是方案，不能把自己的判断当作最终审片结论。
2. **Agent B，独立审核者**：读取A的时间轴和删段理由，实际看切口前后有序画面，回查原片动作及音频依据。B只写审片意见，不同时修改A的时间轴。交给B原稿和证据，不提供“这些切口应该通过”的预设结论。
3. A落实B的必要修改，只重新核对改动影响的切口/节奏；B复核修改。通过后锁轨，A完成字幕、效果、渲染，B再看最终成片的切口画面。通常一轮，确有问题再迭代，不强求无缝转场。

使用collaboration工具实际创建两个独立agent；不要仅在一个agent的报告里写两个角色名称冒充协作。若父agent承担A，则委派另一个agent做B；一般测试可由两个子agent分别承担A/B。文件由A修改，B只提交意见，避免同时写时间轴。用户指定其他协作方式时从其要求。

## 连续画面如何提取

不必默认提取前后0.5～1秒的每一帧。`prepare_cut_review.py` 默认窗口前后各0.6秒，较远处按约0.15秒间隔抽取，切点前后3帧逐帧保留，按真实帧编号顺序组成用户示例那样的接触表：原片切出、原片切入、拟拼接序列。总览覆盖所有切口的有序拟拼接序列。B先看总览，遇到动作看不清、快速指点或声音疑点，再打开对应完整接触表；需要时加 `--dense` 提取窗口内每一帧，按行排列，不把几十帧挤成极小缩略图。可以放大局部或查看匹配的切口视频，不能把两张静态图片说成检查了完整轨迹。

采样是为节省审片负担，不是动作检测阈值。总览没有明显问题的常规切口可简短记录通过；有问题或不确定的项才详细回查。不自动根据相似度、像素变化或ASR触边替B写“动作已完成”。

## 声音优先保完整

工具提供原声峰值/RMS、声音曲线、真实ASR边界及被删区间理由。低于-35dBFS仍是原剪辑的候选依据，但不等于可以删掉实际说出的词或尾音；ASR时间戳也可能包住静音，触边不自动判定截声。B结合前后词语、声学包络、原片与拼接预览判断。若疑似截声，优先恢复少量正确原声，或让A重新修改删段，而不是用字幕掩盖。视觉不顺与声音完整冲突时，优先正确讲话和原意。

有实际听音能力时试听切口、起尾音和爆点。不能实际听音时，`listening_status=unavailable`，如实写出基于测量与转写的判断；`audio_status=uncertain`可在具体理由下保守保留原方案，不自动阻断全部工作，也不能声称听过。明确的 `potential_truncation` 不能直接批准，须调整或回查解决。A/B不调用新的ASR或视觉API；已有匹配真实缓存可以离线复用，ASS/SRT不用于伪造字词时间。

多个原片先组装再精剪时，B也检查组装内部接点，不能仅检查外层气口边界。内部接点需要调整则创建新任务。`assemble_cached_sources.py` 按原片真实整帧范围组装，以原声PCM避免多层AAC接点偏移，并从每个匹配原片缓存确定性映射字词时间。派生记录明确为 `provider=derived`，没有新云端任务ID；工程保存真实父缓存、原片、映射、边界截词标记。不能给修改后的组装源套旧组装源云端证明。该工具暂支持同帧率、原速组装与原声缓存；仍须独立审核语义、声音和画面，不把映射当作识别或通过意见。

```bash
# plan.json: {"fps":25,"parts":[{"cache":"/path/clip-2","start_frame":167,"end_frame":1297,"reason":"保留完整一遍"}, ...]}
python scripts/assemble_cached_sources.py --job /path/new-job --plan plan.json --script original.txt --title 口播
# 接着audio_qc、确认原声、preview_subtitles及正常剪辑流程；无需transcribe。
```

## 命令与记录

```bash
python scripts/build_timeline.py --job /path/job --speed 1.3 --source-buffer .35
python scripts/preview_cuts.py --job /path/job
python scripts/check_rhythm.py --job /path/job
python scripts/prepare_cut_review.py --job /path/job --planner /root/agent_a --context .6
```

新时间轴包含 `peer_cut_review_policy=two-agent-cut-review-v1`。`qc/cut-review-proposal.json` 保存A身份、所有边界、删段理由、帧证据、音频曲线和字词触边标注。B必须实际用view_image查看总览和需要进一步核查的原片序列，提交全量独立意见，包括正常通过的切口：

```json
{"reviewer":"/root/agent_b","items":[
  {"id":"cut-001","decision":"approve",
   "visual_note":"前一手势已落回腹前，下一句换姿态是正常口播跳切，未截断有效指点。",
   "audio_note":"词语与前后语义完整；ASR尾部包住低音量停顿，声学曲线未显示词中突断。实际听感未验证。",
   "audio_status":"uncertain","listening_status":"unavailable",
   "evidence":[{"file":"qc/cut-review-evidence/overview-01.jpg","sha256":"实际文件指纹"}]}
]}
```

这是格式示例，不是可自动套用的结论。每项根据B真实查看的画面和音频依据填写。`decision`为 `approve`、`adjust`、`keep_pause` 或 `defer`。B身份必须与A不同，证据必须属于当前切口。总体通过靠实际独立复核，不靠固定图像变化阈值。

```bash
python scripts/review_cut_plan.py --job /path/job --reviews b-reviews.json
```

如B建议小幅回帧，A提交具体修改：

```json
[{"id":"cut-001","restore_out_frames":2,"restore_in_frames":1,"reason":"按B审阅保留已说出的尾音，恢复3帧原声与动作；不恢复已删除的重说"}]
```

```bash
python scripts/apply_peer_adjustments.py --job /path/job --changes changes.json
```

只允许恢复被删区间里的真实帧，不覆盖相邻保留片段，不补静帧或静音。可恢复已保留字词的起尾音，不要求这些正确讲话低于-35dBFS。若会恢复计划已整词删除的讲话或重录，先由A对照原稿修改对应decisions、重建时间轴，再交B复核。`timeline.json/peer_adjustments`记录回帧与理由，之后重新生成受影响预览、节奏及方案，B再审。恢复的停顿可在节奏报告记为 `peer_preserved` 并说明声音/动作作用；不是用它保留普通卡顿。

方案、证据和B记录版本匹配且无未完成调整后锁轨。只强制这个协作流程的真实性和声音/语义完整，不强制“零跳切”“零不确定项”或固定回帧数。

## 成片验证

新版本 `verify_export.py` 自动生成 `output/cut-review-proposal.json` 和成片接触表；首次没有B导出审片记录会提示待复核。B实际查看导出总览及可疑切口后提交本次export意见，不能复制锁轨前的通过状态：

```bash
python scripts/review_cut_plan.py --job /path/job --phase export --reviews b-export-reviews.json
python scripts/verify_export.py --job /path/job
```

已锁定后如需改变切点，创建新版本，不改旧锁。旧任务无新协作标记可按原规则验证。交付视频、SRT/ASS、A方案、B审片意见、修改记录、总览/详细证据及未实际试听的限制；不强求测试版必须与旧版不同，也不以重新导出就宣称质感提升。
