# 必做的逐项讲解审阅与补效果

每个正常任务在字幕校对后、style 前运行。agent通读全文后明确判定，脚本只检查记录一致性，不按“第一、第二”关键词推断语义。

输入格式：

```json
{
  "assessment": "expanded_points",
  "reason": "两项都有独立观点、例子和说明，属于逐项展开",
  "points": [
    {"start": 3.2, "end": 12.4, "sequence_group": "观点", "point_index": 1,
     "expanded_explanation": true, "script_quote": "原稿中的第一项及展开说明",
     "reason": "第一项有独立论点和例子", "sound": "ding-ding-ding"},
    {"start": 13.1, "end": 22.0, "sequence_group": "观点", "point_index": 2,
     "expanded_explanation": true, "script_quote": "原稿中的第二项及展开说明",
     "reason": "第二项有独立论点和例子", "sound": "ding-ding-ding"}
  ]
}
```

没有逐项结构时使用 `assessment=no_expanded_points`、`points=[]` 和具体理由。时间全部是变速后的成片秒数；使用真实字词起点，引用必须来自当前用户原稿。同组至少两项。缩放固定 1.2；音效缺省叮叮叮、-24dB，范围 -40 至 -18dB，自动测量限制与侧链避让。侧链保留-3dB干声下限；渲染时逐项测量实际播放窗口的前后RMS、人声RMS及衰减，超过3.2dB容差即拒绝导出。正常流程记录 `qc/point-sound-levels.json`，既有视频补效果记录 `completion/verification.json` 的 `events[].applied_sound.levels`。这些数值不等于已完成实际试听。

```bash
python scripts/review_points.py --job /path/job --review /path/point-review.json
python scripts/style.py --job /path/job --events /path/expanded-points.json --grade none
python scripts/render.py --job /path/job
python scripts/verify_export.py --job /path/job
```

`style/point-review.json` 绑定原稿、时间轴、真实字词指纹。events 逐项与 points 的分组、序号、起止及原稿引文一致。任一变更需要重新语义审阅。没有 points 时 events 可以包含其他已授权普通事件。

## 已剪视频补齐效果

只用于用户明确指定既有剪辑/字幕视频并要求继续补效果。先验证无字幕基底与用户指定字幕版属于同一任务；不能缩放已烧录的字幕。字幕文字记录的每项 id/text/start/end 必须与当前 captions 一致；仍有未完成的实际听感核验时据实交付说明，不虚构校对结论。

```bash
python scripts/review_points.py --job /path/job --review /path/point-review.json
python scripts/finish_existing.py --job /path/job \
  --base /path/matching-unsubtitled.mp4 --reference-video /path/user-selected-subtitled.mp4 \
  --subtitles /path/preview.ass \
  --captions /path/preview-captions.json --text-review /path/reference-text-review.json \
  --output /path/completed.mp4
```

脚本绑定基底、时间轴、ASS、captions、文字记录及观点审阅指纹；不修改既有入出点或速度，不伪造原流程锁定记录。按真实观点时间居中推进，字幕在缩放后合成；音效使用 assets 清单并验证指纹，先限制增益，再侧链避让，最终混音。全部帧数、音画时长、输入指纹和全片解码通过才交付，并在 `completion/verification.json` 保存每个音效的实际增益、偏移及混音轨指纹。实际听感是否完成单独记录。

导出后逐项抽取推进前、推进完成、结束后帧检查人物和字幕。核对每个效声音轨非空并确实混入成片，同时提供触发点试听片段；能实际听音时完成试听，不能时如实注明。
