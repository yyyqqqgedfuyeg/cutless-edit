# 同一句字幕中的强调词

当前字幕规则：默认使用随包提供的得意黑（Smiley Sans）粗体，字号竖屏0.144、横屏0.066，只显示单行白色圆角矩形底黑字（圆角18px）。原稿 `【】` 标记的对应口播文字以1.35倍字号、黄字黑色描边显示，括号不显示。旧任务的颜色、阴影、动画和手动强调配置会被清除，独立花字轨不再输出文字。长字幕先按语义及真实字词边界拆短，日期和数字单位保持完整；单条字号可在基础字号82%–100%内适配并记录font_scale，仍溢出时须重新断句，不换行。SRT只保存文字与时间，视觉样式由ASS/视频保留。

`emphasize_subtitles.py` 是独立调用入口，与 `subtitles.py --emphasis` 使用同一渲染逻辑。它只应用agent指定的样式，不替换文字、不改时间戳、不承担语义校对。原有阿里转写、用户原稿、锁轨与agent校对门禁保持生效。

## 调用

先查看任务 `subtitles/captions.json`，选取字幕id及完整文字，再创建强调JSON：

```json
[
  {
    "id":"s0001",
    "text":"留在北京，也能稳稳上岸",
    "spans":[
      {"text":"北京","style":{"size_scale":1.45,"color":"#FFD54F","bold":true,"outline_color":"#5B3900","outline_width":2.5}},
      {"text":"上岸","style":{"size_scale":1.2,"color":"#80DEEA","underline":true}}
    ]
  }
]
```

```bash
python /path/cutless-edit/scripts/emphasize_subtitles.py \
  --job /path/job --emphasis /path/emphasis.json
python /path/cutless-edit/scripts/render.py --job /path/job
python /path/cutless-edit/scripts/verify_export.py --job /path/job
```

示例id、全文必须换成当前任务的真实字幕。配置保存在任务 `subtitles/layout.json`，以后调整其他样式时继续生效。传入内容为 `[]` 的JSON可清除全部句内强调。

## 可配置样式

| 字段 | 含义 |
| --- | --- |
| size_scale | 相对基础字号倍率，0.5–2.0；推荐1.15–1.45 |
| color | 文字颜色，`#RRGGBB` 或含不透明度的 `#RRGGBBAA` |
| bold / italic / underline | 加粗、斜体、下划线，true/false |
| outline_color / outline_width | 描边颜色及粗细，0–12像素 |
| shadow_color / shadow_depth | 阴影颜色及偏移，0–12像素 |

未指定的外观继承基础字幕样式；强调片段结束后恢复基础样式，不污染后面的字。一句可指定多个词或单个字。相同词出现多次时，在该span中加 `"occurrence":2` 可只强调第二次，默认1，从左到右按非重叠匹配计数。

## 排版与校验

- 使用同一句ASS文本的原生排版，保留常规字距，放大词会自然占用更多宽度。
- 字幕仅单行，不自动换行。强调后超出画面90%安全宽度会报错，应缩小倍率、调整字号或重新断句。
- 兼容整句缩放回弹和可选圆角背景，背景按强调后的字宽及各行最大字号调整；斜体宽度保守估算，实际成片仍需检查。
- 同一范围不允许重叠强调；词不存在、出现次数无效、配置全文与字幕不一致时明确报错。
- 修改强调样式不改变校对记录；字幕文字或时间变化后仍需agent重新校对，并检查强调配置。
- SRT保存纯文字。ASS及烧录视频才包含句内样式。不会添加演示标题、效果编号或额外文字。

独立效果预览使用合成文案即可，不需调用ASR；真实口播项目仍按完整流程处理。


当前扩展规则：原稿没有【】时，由agent先选重点词并提交用户明确审批，使用 `review_emphasis.py` 保存方案及实际用户决定；批准后重跑subtitles再生成最终字幕。逐项展开讲解使用 `expanded-point`，同步 `ding-ding-ding`/`swish` 和画面居中120%平滑推进，事件结束立即恢复100%；一句话列举不触发。执行阶段和精确数据格式见 commands.md、schemas.md。音效实测压低并侧链避让人声，须试听复核。
