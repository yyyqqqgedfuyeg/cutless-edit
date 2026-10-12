"""CLI definitions shared by the individually callable stage scripts."""
import argparse
import sys

import asr_steps
import edit_steps
import pause_analysis
import point_review
import finish_steps
import media_steps
import caption_review
import emphasis_review


FUNCTIONS = {
    'doctor':media_steps.doctor,'ingest':media_steps.ingest,'audio_qc':media_steps.audio_qc,
    'denoise':media_steps.denoise,'transcribe':asr_steps.transcribe,
    'preview_subtitles':asr_steps.preview_subtitles,'analyze_edits':edit_steps.analyze_edits,
    'review_edits':edit_steps.review_edits,'build_timeline':edit_steps.build_timeline,
    'check_rhythm':pause_analysis.check_rhythm,
    'review_points':point_review.review_points,
    'preview_cuts':edit_steps.preview_cuts,'lock_timeline':edit_steps.lock_timeline,
    'subtitles':finish_steps.subtitles,'style':finish_steps.style,
    'render':finish_steps.render,'verify_export':finish_steps.verify_export,
    'review_subtitles':caption_review.review_subtitles,
    'review_emphasis':emphasis_review.emphasis_review}


def main(stage=None):
    if stage is None:
        if len(sys.argv)<2 or sys.argv[1] not in FUNCTIONS:
            print('可用阶段：'+', '.join(FUNCTIONS))
            return
        stage=sys.argv.pop(1)
    parser=argparse.ArgumentParser(description=f'cutless-edit / {stage}；详见 references/commands.md')
    if stage=='doctor':parser.add_argument('--font',default='Smiley Sans Oblique',help='检查实际字体名或字体文件路径')
    if stage!='doctor':parser.add_argument('--job',required=True,help='单次任务目录')
    if stage=='ingest':
        parser.add_argument('--video',required=True)
        parser.add_argument('--script',required=True,help='UTF-8原稿文件')
        parser.add_argument('--title',required=True)
        parser.add_argument('--layout',choices=['auto','portrait','landscape'],default='auto')
        parser.add_argument('--rotate',choices=['auto','clock','cclock'],default='auto',
            help='默认尊重旋转元数据；clock/cclock为额外90度纠正')
    if stage=='denoise':
        parser.add_argument('--mode',choices=['light','moderate','accept','bypass'],required=True)
        parser.add_argument('--reason',required=True)
        parser.add_argument('--noise-floor',type=float,default=-50)
        parser.add_argument('--sample-start',type=float,default=0)
    if stage=='transcribe':
        parser.add_argument('--reuse',help='同一原片已有的真实transcription.json')
        parser.add_argument('--reuse-source',help='旧转写实际对应的原视频，校验指纹')
        parser.add_argument('--reuse-provenance',help='阿里真实转写的provenance.json，复用时必填')
        parser.add_argument('--timeout',type=int,default=900)
    if stage=='review_emphasis':
        parser.add_argument('--proposal',help='agent重点词方案JSON；保存待审批，不自动批准')
        parser.add_argument('--decision',choices=['approve','reject'])
        parser.add_argument('--user-response',help='用户实际明确审批回复，不能由agent编造')
    if stage=='review_edits':parser.add_argument('--reviews',required=True,help='agent填写的决策JSON列表')
    if stage=='review_subtitles':parser.add_argument('--reviews',required=True,help='逐条引用原稿、确认文字及对齐的校对JSON')
    if stage=='review_points':parser.add_argument('--review',required=True,help='agent全文语义审阅的逐项讲解判定及完整清单')
    if stage=='build_timeline':
        parser.add_argument('--speed',type=float,default=1.3,help='锁轨前设置正文速度，默认1.3倍；保留音高')
        parser.add_argument('--source-buffer',type=float,default=.35,help='首尾从原片保留的缓冲秒数，默认0.35；原速，0关闭')
    if stage=='preview_cuts':parser.add_argument('--context',type=float,default=1.5)
    if stage=='subtitles':
        parser.add_argument('--resplit',action='store_true',help='按真实字词重新断句；使旧文字校对失效')
        parser.add_argument('--emphasis',help='句内强调JSON；字号/颜色/样式；空列表清除')
        parser.add_argument('--corrections',help='[{id,text,reason,start?,end?}]')
        parser.add_argument('--terms',help='不拆行术语JSON列表')
        parser.add_argument('--bottom',type=float,help='字幕块底部纵坐标/画面高度')
        parser.add_argument('--font-size',type=float,help='字号/画面宽度')
        parser.add_argument('--font',help='已安装字体名称')
        parser.add_argument('--line-chars',type=int,help='单行目标字符数；保护完整词组时允许小幅浮动')
        parser.add_argument('--text-color',help='文字颜色 #RRGGBB 或 #RRGGBBAA（AA为不透明度）')
        parser.add_argument('--outline-color',help='描边颜色 #RRGGBB 或 #RRGGBBAA')
        parser.add_argument('--outline-width',type=float,help='描边粗细0–12像素；0关闭')
        parser.add_argument('--shadow-color',help='阴影颜色 #RRGGBB 或 #RRGGBBAA')
        parser.add_argument('--shadow-depth',type=float,help='阴影偏移0–12像素；0关闭')
        parser.add_argument('--bold',action=argparse.BooleanOptionalAction,default=None,help='加粗或 --no-bold 取消加粗')
        parser.add_argument('--background',action=argparse.BooleanOptionalAction,default=None,help='启用圆角背景；--no-background关闭')
        parser.add_argument('--background-color',help='背景颜色 #RRGGBB 或 #RRGGBBAA')
        parser.add_argument('--background-radius',type=float,help='圆角半径0–100像素')
        parser.add_argument('--background-padding',type=float,help='背景内边距0–100像素')
        parser.add_argument('--animation',choices=['none','bounce'],help='字幕入场动画；固定底部锚点')
        parser.add_argument('--bounce-strength',type=float,help='弹跳缩放幅度0–0.15；横屏建议0.04')
        parser.add_argument('--bounce-ms',type=float,help='弹跳入场时长120–800ms')
        parser.add_argument('--animated-cues',help='仅指定cue弹跳，用逗号分隔；空字符串表示全部')
    if stage=='style':
        parser.add_argument('--events',help='[{start,end,template,text,zoom}]')
        parser.add_argument('--grade',default='auto',help='auto/none或素材库调色id')
        parser.add_argument('--bgm')
        parser.add_argument('--bgm-preset',help='auto或内置BGM id；不提供则不加音乐')
        parser.add_argument('--bgm-source',help='BGM来源和授权说明')
        parser.add_argument('--bgm-gain',type=float,default=-24)
    args=parser.parse_args()
    try:
        FUNCTIONS[stage](args)
    except Exception as error:
        # Never print request headers, credentials or exception tracebacks from network clients.
        if isinstance(error,(ValueError,FileNotFoundError,KeyError)):
            print(f'失败：{error}',file=sys.stderr)
        else:
            print(f'失败：{type(error).__name__}；检查网络、输入文件或任务日志',file=sys.stderr)
        raise SystemExit(1)


if __name__=='__main__':main()
