# 风格素材库

当前字幕规则：默认使用随包提供的得意黑（Smiley Sans）粗体，字号竖屏0.144、横屏0.066，只显示单行白色圆角矩形底黑字（圆角18px）。原稿 `【】` 标记的对应口播文字以1.35倍字号、黄字黑色描边显示，括号不显示。旧任务的颜色、阴影、动画和手动强调配置会被清除，独立花字轨不再输出文字。长字幕先按语义及真实字词边界拆短，日期和数字单位保持完整；单条字号可在基础字号82%–100%内适配并记录font_scale，仍溢出时须重新断句，不换行。SRT只保存文字与时间，视觉样式由ASS/视频保留。

入口为 `assets/manifest.json`，横竖屏参数在 `assets/profiles/portrait.json`、`landscape.json`。素材可逐项扩展，不需改剪辑规则。

## 已创建素材

| 类型 | ID | 使用建议 |
| --- | --- | --- |
| 调色 | natural | 轻微对比度和饱和度 |
| 调色 | portrait-fresh | 竖屏轻提亮，默认竖屏预设 |
| 调色 | landscape-neutral | 横屏克制，默认横屏预设 |
| 调色 | warm-soft | 轻暖色，使用前检查肤色 |
| BGM | calm-focus | 72 BPM，横屏专业口播 |
| BGM | warm-explain | 80 BPM，温和讲解 |
| BGM | bright-pulse | 96 BPM，竖屏轻快口播 |
| 音效 | soft-tick | 重点提示 |
| 音效 | gentle-chime | 章节或结论 |
| 音效 | soft-whoosh | 少量转场 |
| 花字 | keyword-pop / key-sentence / number-highlight | 关键词、重点句、数字 |
| 花字 | chapter-tag / soft-emphasis | 分类标题、轻强调 |
| 画面 | gentle-push | 不加文字的轻推进 |
| 新花字 | subtitle-bounce / corner-label / focus-bracket | 回弹、角标、括号强调 |

内置BGM和合成音效使用标准库确定性生成，无第三方采样。它们是可循环的简单配乐起步素材；实际任务应试听循环接缝及人声遮蔽情况。音频指标保存在 `assets/audio-metadata.json`。调色是参数预设，不是LUT或自动曝光修复。

## 字幕弹跳与固定位置

```bash
python scripts/subtitles.py --job /path/job \
  --animation bounce --bounce-strength 0.08 --bounce-ms 320 \
  --animated-cues s0001,s0005 --bottom 0.82
```

弹跳为围绕固定底部锚点的缩放回弹，最终恢复100%字号。两行文字一起回弹，不改变字幕基准位置。横屏建议强度0.04、240ms，竖屏0.08、320ms；启用时检查弹跳最大尺寸下是否越界。`--animated-cues ''` 应用于全部条目；`--animation none` 关闭。默认关闭，agent根据内容选择重点条目。字体、颜色、描边等仍通过原有参数配置；位置可随时整体调整，不改变剪辑轨道。

## BGM、花字、音效组合

agent根据内容创建events.json，使用剪辑后的时间：

```json
[
  {"start":3.2,"end":4.8,"template":"subtitle-bounce","text":"关键结论","zoom":1.04,"sound":"soft-tick","sound_gain_db":-18},
  {"start":12.0,"end":14.0,"template":"corner-label","text":"第二部分","zoom":1.02}
]
```

示例时间必须替换成实际成片时间，不能直接用于短于14秒的任务。

```bash
python scripts/style.py --job /path/job --events /path/events.json \
  --grade auto --bgm-preset auto --bgm-gain -14
python scripts/render.py --job /path/job
python scripts/verify_export.py --job /path/job
```

`auto` 按画幅匹配：竖屏portrait-fresh/bright-pulse，横屏landscape-neutral/calm-focus。不传 `--bgm-preset` 或 `--bgm` 则不加音乐；`--grade none` 关闭调色。BGM在人声期间避让；音效显式选择才播放。所有素材复制进任务并记录哈希，后续修改素材库不会悄悄改变既有任务。

## 校对职责

用户原稿必须提供，阿里模型必须先给出真实转写和时间戳。agent对照原稿、实际口播及剪辑后片段逐条校对，决定保留口语表达、纠正识别错误或退回剪辑处理真实口误。禁止将这一判断写成硬编码词典、正则替换或自动全部确认脚本。

`subtitles.py --corrections` 仅执行agent已作出的修改；`review_subtitles.py` 仅检查记录覆盖、引文存在、时间与版本一致，不能证明语义正确。改变文字或时间需重新校对；只调整弹跳、颜色、位置等样式无需重做文字校对。

## 逐字弹跳 / 逐字放大

新增模板 `char-bounce`（逐字上跳、落回原位）和 `char-grow`（逐字100%→145%→100%）。字距按所选字体的真实字宽计算，使用独立字位，避免放大时挤动整句；为短中文重点花字设计，单行使用。间隔与动画时长可按事件配置：

```json
[
  {"start":0,"end":1.5,"template":"char-bounce","text":"每个字轻轻弹起","zoom":1.02,"stagger_ms":105,"animation_ms":360},
  {"start":1.5,"end":3,"template":"char-grow","text":"让重点逐字放大","zoom":1.02,"stagger_ms":105,"animation_ms":360}
]
```

`stagger_ms` 范围20–400毫秒，`animation_ms` 范围120–800毫秒。事件至少250毫秒；整句从事件起点显示，每个字依次执行动画并恢复原位/字号。短事件会自动收紧间隔以留出完整展示时间。此间隔仅为视觉节奏，不代表真实逐字口播时间戳；需要逐字跟读时必须另行根据真实时间戳对齐。动画不修改字幕文案，不执行语义校对。


`jump_ratio` 控制上跳高度/字号，默认0.32，范围0.05–0.8；`peak_scale` 控制放大峰值，默认1.45，范围1.1–1.8。二者可在重点事件中设置。中文常规字距不额外扩大；放大时邻字可能短暂靠近，建议短句、少量使用。字体度量使用Pillow与fontconfig（Linux安装fontconfig；字体文件路径可供测量检查，正式渲染使用已安装字体族名），字体须与渲染使用的字体一致。

## 可选圆角字幕背景

默认关闭。基础字幕可通过命令行启用，设置保存到任务layout.json：

```bash
python scripts/subtitles.py --job /path/job --background \
  --background-color '#193C5EE6' --background-radius 16 --background-padding 18
# 关闭：--no-background
```

颜色支持 `#RRGGBB` 或 `#RRGGBBAA`；AA是不透明度，00全透明、FF不透明。圆角半径、内边距单位为输出画面像素，范围0–100。背景按当前字幕文字宽度与行数调整，最多两行，保持基准位置固定；改变背景样式无需重做文字校对，需重新渲染视频。动画幅度较大时可增加内边距。

重点花字也可单独配置：

```json
{"start":0,"end":1.5,"template":"char-bounce","text":"每个字轻轻弹起","jump_ratio":0.32,"background":true,"background_color":"#193C5EE6","background_radius":16,"background_padding":18}
```

背景是独立ASS圆角矢量层，不随单个字跳动或放大。SRT不承载背景或动画。


## 句内强调词

同一句可对多个词/字分别设置字号、颜色、加粗、斜体、下划线、描边和阴影，常规文字自动恢复原样式。调用 `emphasize_subtitles.py --job /path/job --emphasis /path/emphasis.json`；配置格式与示例见 [inline-emphasis.md](inline-emphasis.md)。


当前扩展规则：原稿没有【】时，由agent先选重点词并提交用户明确审批，使用 `review_emphasis.py` 保存方案及实际用户决定；批准后重跑subtitles再生成最终字幕。逐项展开讲解使用 `expanded-point`，同步 `ding-ding-ding`/`swish` 和画面居中120%平滑推进，事件结束立即恢复100%；一句话列举不触发。执行阶段和精确数据格式见 commands.md、schemas.md。音效实测压低并侧链避让人声，须试听复核。
