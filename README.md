# cutless-edit

面向16:9、9:16口播视频的剪辑Skill：真实阿里ASR、音频质检与降噪、口误及停顿精剪、锁定时间轴、agent对照原稿校对、字幕样式与成片导出。

## 输入与职责

必须提供原视频和用户原稿。脚本获取真实转写及字词时间戳，agent根据原稿、音画及剪辑规则判断切口和逐条校对字幕；不使用硬编码脚本代替语义校对。样式预览不需要调用ASR。

- [Skill入口](SKILL.md)
- [分阶段命令](references/commands.md)
- [依赖与API报错](references/dependencies-and-errors.md)
- [剪辑规则](references/editing-policy.md)
- [字幕和风格素材库](references/style-library.md)
- [句内强调词配置](references/inline-emphasis.md)

## 安装与配置

将此仓库克隆到全局Skills目录下尚不存在的 `cutless-edit` 目录，或将完整仓库目录链接到该位置。已有安装时先检查原目录，不要覆盖本地配置与修改。

```bash
git clone https://github.com/yyyqqqgedfuyeg/cutless-edit.git ~/.codex/skills/cutless-edit
cd ~/.codex/skills/cutless-edit
python3 -m venv .venv
.venv/bin/python -m pip install -r scripts/requirements.txt
cp scripts/.env.example scripts/.env
```

编辑本机 `scripts/.env`，填写自己的阿里DashScope密钥。转写脚本固定读取同目录配置，不需要 `--env`，也不会用系统环境变量覆盖API配置。真实 `.env` 不在仓库中；示例只包含占位符。

系统需Python 3.11+、FFmpeg/FFprobe（包含libass、libx264、libmp3lame等）、fontconfig及中文字体。详见[完整依赖](references/dependencies-and-errors.md)。

```bash
.venv/bin/python scripts/doctor.py
.venv/bin/python -m unittest discover -s scripts -p 'test_*.py' -q
```

测试使用合成素材及模拟API，不读取真实密钥、不消耗云端额度。`doctor`为离线依赖检查，不能判断账号余额；实际调用失败时区分免费额度耗尽、欠费、限流、权限和网络问题，输出中文建议及任务 `asr/error.json`。

## 字幕与风格

支持固定字幕位置、双行、字体颜色/描边/阴影、可选圆角背景、整句回弹、逐字上下弹跳、逐字放大和句内强调词。逐字视觉节奏不伪装成真实逐字语音对齐。不自动添加说明标题。

素材包含11种花字/缩放模板、4套轻调色预设、3段程序合成BGM与3个轻音效，无第三方录音采样。

- [竖屏风格预览](assets/previews/portrait-styles.mp4)
- [横屏风格预览](assets/previews/landscape-styles.mp4)

预览是合成效果演示，不代表实际口播已完成校对或听感验收。
