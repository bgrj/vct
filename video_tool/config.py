# -*- coding: utf-8 -*-
"""全局配置与默认参数

集中存放各模块共享的常量与默认值，避免魔法数字散落在各处。
"""

import os


class VideoToolError(Exception):
    """本项目自定义异常基类。"""


# ---------------------------------------------------------------------------
# 应用信息
# ---------------------------------------------------------------------------
APP_TITLE = "视频截图工具"
APP_VERSION = "2.0"
COPYRIGHT = "© 2025 一模型Ai (https://jmlovestore.com) - 不会开发软件吗 🙂 Ai会哦"

# ---------------------------------------------------------------------------
# 外部可执行文件（FFmpeg / FFprobe）
# ---------------------------------------------------------------------------
# 找不到时按顺序在这些目录中搜索，最后回退到系统 PATH
FFMPEG_SEARCH_DIRS = [
    r"E:\Tools\ffmpeg\bin",
    r"C:\ffmpeg\bin",
    r"D:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"D:\Program Files\ffmpeg\bin",
]

# 单次调用的超时时间（秒）
FFPROBE_TIMEOUT = 60
FFMPEG_FRAME_TIMEOUT = 180
FFMPEG_SUBTITLE_TIMEOUT = 900

# ---------------------------------------------------------------------------
# 视频文件识别
# ---------------------------------------------------------------------------
VIDEO_EXTENSIONS = [
    '.mp4', '.avi', '.mkv', '.mov', '.wmv', '.flv', '.webm',
    '.m4v', '.mpg', '.mpeg', '.3gp',
]

# 各下载工具产生的「未下载完成」文件后缀，扫描时跳过并在报告中说明原因
INCOMPLETE_EXTS = [
    '.qkdownloading', '.part', '.crdownload', '.!qb',
    '.downloading', '.tmp', '.filepart',
]

# ---------------------------------------------------------------------------
# 字幕与台词
# ---------------------------------------------------------------------------
# 优先选用的字幕轨名称关键字（按顺序匹配，先简后繁）
SUBTITLE_NAME_PREFERENCE = ["简体中文", "简体", "中文简体", "chs", "sc", "gb"]
# 按语言码兜底（ISO639-2/B 与 ISO639-1）
SUBTITLE_LANG_PREFERENCE = ["zh", "chi", "zho", "chs", "cht"]
# 可转换为文本的字幕编码；图像类字幕（PGS/VOBSUB）无法转 SRT
TEXT_SUBTITLE_CODECS = [
    "subrip", "srt", "ass", "ssa", "mov_text",
    "webvtt", "text", "s_text/utf8",
]

# 台词文本清洗与合并
MIN_CUE_DURATION = 0.35     # 短于该时长的台词合并到相邻句
MERGE_GAP = 0.12            # 相邻两句间隔小于该值时合并（受下面两个上限约束）
MAX_CUE_DURATION = 6.0      # 合并后台词最长时长，避免连续对白被粘成一句
MAX_CUE_TEXT_LINES = 3      # 渲染时最多行数，同时也是合并时的文本行数上限

# 字幕文件解码尝试顺序（中文常见编码）
SRT_ENCODINGS = ["utf-8-sig", "utf-8", "gb18030", "gbk", "big5"]

# ---------------------------------------------------------------------------
# 候选帧采样与评分
# ---------------------------------------------------------------------------
CANDIDATE_MIN = 5           # 每句最少候选帧数
CANDIDATE_MAX = 10          # 每句最多候选帧数
CANDIDATE_FPS = 3.0         # 候选帧采样密度（帧/秒）
CANDIDATE_SCALE_WIDTH = 640  # 打分前的降采样宽度（提速）

# 评分权重
# 说明：帧间差异衡量的是「画面运动幅度」，并非模糊本身，权重过高会盖过
# 睁眼/嘴部等自然度判定（实测中曾出现这种情况），因此这里刻意压低，
# 真正的模糊交给「句内相对清晰度 + 严重模糊惩罚」处理。
WEIGHT_SHARPNESS = 1.0
WEIGHT_MOTION = 0.7
WEIGHT_FACE = 1.0
WEIGHT_BRIGHTNESS = 0.25
# 情境匹配：构图（人像居中且占比合理）、时序（贴近句子中点）、镜头一致
WEIGHT_COMPOSITION = 0.55
WEIGHT_TIMING = 0.35
WEIGHT_SHOT = 0.30

# 句内镜头分组：相邻候选帧灰度直方图相关性低于该值视为发生了切镜
SHOT_CHANGE_CORRELATION = 0.72
# 时序偏置：句首/句尾各多大比例的时间区间降权（避开切镜残留与转场）
TIMING_EDGE_RATIO = 0.12

# 构图分：人脸框中心到画面中心的距离达到该比例（相对半对角线）时给 0 分
COMPOSITION_CENTER_TOLERANCE = 0.55
COMPOSITION_CENTER_WEIGHT = 0.6
COMPOSITION_AREA_WEIGHT = 0.4

# 「平坦画面」判定：灰度标准差低于该值视为纯黑/纯色/大虚化，不做模糊淘汰
FLAT_STD_THRESHOLD = 8.0
# 平坦画面在清晰度归一化中使用的中性分，避免被误杀也不至于盖过真正清晰的画面
FLAT_SHARPNESS_NEUTRAL = 0.45
# 亮度合理区间（0-255），超出只轻微降权、不淘汰
BRIGHTNESS_LOW = 30.0
BRIGHTNESS_HIGH = 235.0
# 严重运动模糊：清晰度低于全句候选最高值的该比例，且帧间差异明显时判为模糊
SEVERE_BLUR_RATIO = 0.18
SEVERE_MOTION_RATIO = 0.60
SEVERE_BLUR_PENALTY = 1.2

# 闭眼/半闭眼与嘴部夸张的硬惩罚。
# 需求明确要求「不能出现眨眼、半闭眼、嘴部扭曲」，仅靠加分项不足以压住
# 其他指标的优势，因此这类帧直接扣分；若整句候选都如此，惩罚一视同仁，
# 不会导致该句无法出图。
#
# 眼睛优先于嘴巴：出图时最刺眼的是「这个人闭着眼」，其次是「半闭不精神」，
# 最后才是嘴型。因此眼睛采用「闭眼重罚 + 半闭轻罚 + 双眼不平衡轻罚」三层，
# 嘴部只保留「张口过大」一层。
EYE_CLOSED_PENALTY = 0.70
EYE_HALF_PENALTY = 0.30         # 半闭眼（介于闭眼与睁开之间）的扣分
EYE_BALANCE_MIN = 0.62          # 双眼开合比「较小 / 较大」低于该值视为单眼微闭
EYE_BALANCE_PENALTY = 0.35      # 单眼微闭（一眼睁一眼眯）的扣分，典型眨眼瞬间
MOUTH_OPEN_PENALTY = 0.20

# 降级判定：综合得分低于该值视为画质偏低
LOW_QUALITY_THRESHOLD = 0.15

# ---------------------------------------------------------------------------
# 人脸（人像自然度为加分项，不做淘汰门槛）
# ---------------------------------------------------------------------------
FACE_MIN_DETECTION_CONFIDENCE = 0.5
# 眼睛开合比（EAR）阈值。实测本片源：睁眼约 0.18~0.28，闭眼/半闭眼低于 0.12，
# 因此闭眼判据取 0.13，满分判据取 0.26。
# 说明：EAR = 眼睛的「高度 / 宽度」，数值越大表示眼睛睁得越开，是判断眨眼
# 与否的通用做法；这里同时看「绝对数值」和「同一句内谁睁得更大」。
EAR_CLOSED_THRESHOLD = 0.13     # 眼睛开合比低于该值视为闭眼/半闭眼
EAR_OPEN_REFERENCE = 0.26       # 眼睛开合比达到该值视为充分睁开
EAR_HALF_RATIO = 0.80           # 睁眼参考值的该比例以下记为「半闭眼」（介于闭眼与睁开之间）
EAR_BALANCE_WEIGHT = 0.20       # 人像加分中「双眼开合是否对称」所占比例
# 优选窗口：综合得分落在「本句最高分 - 该值」以内的候选帧，改按「眼睛是否睁开」
# 优先挑选。眼睛比画质细节更容易被一眼看出问题，因此给一个有限度的优先权——
# 得分明显更差的帧（例如糊了一截）不会被它顶掉。
EYE_PREFERENCE_MARGIN = 0.15
# 闭眼帧重采样：整句候选全部闭眼时（多为眨眼瞬间恰好被采样命中），把候选帧数
# 与采样密度临时提高再挑一次。这类句子每集通常只有个位数，成本可忽略，却能把
# 「闭眼废片」压到接近零。
EYE_REFINE_CANDIDATE_MAX = 30   # 重采样时的每句候选帧上限
EYE_REFINE_CANDIDATE_FPS = 6.0  # 重采样时的采样密度（帧/秒）
EYE_REFINE_MODES = ('closed', 'half', 'off')   # 重采样触发口径

MAR_OPEN_THRESHOLD = 0.55       # 嘴部纵横比高于该值视为大张口（发音瞬间）
MAR_NATURAL_MIN = 0.05          # 自然开口下限：低于该值视为闭嘴（没在说话）
MAR_NATURAL_MAX = 0.42          # 自然开口上限：区间内视为「正在自然说话」
# 说话活跃度加分：嘴部落在 [MAR_NATURAL_MIN, MAR_NATURAL_MAX] 视为正在自然说话，
# 给满分；完全闭嘴的画面给 MOUTH_IDLE_SCORE（有台词时通常正在说话，闭着嘴的帧
# 更可能是切镜残留或话外音时段），开口过大则线性降到 0（另有硬惩罚兜底）。
MOUTH_IDLE_SCORE = 0.35
MOUTH_ASYMMETRY_LIMIT = 0.35    # 嘴角高度差 / 嘴宽，超出视为嘴部扭曲
FACE_ASYMMETRY_LIMIT = 0.18     # 左右脸关键点对称度失衡上限（大幅侧转/遮挡）
FACE_AREA_REFERENCE = 0.06      # 人脸面积占画面比例达到该值时给满加分
FACE_SHARPNESS_REFERENCE = 300.0  # 人脸区域 Laplacian 方差达到该值视为足够清晰
FACE_MODEL_COMPLEXITY = 1       # mediapipe 人脸检测模型复杂度（0/1）
FACE_MAX_NUM = 3                # 单帧最多分析人脸数

# mediapipe 1.x 的 Tasks API 需要外部模型文件；下载一次后即可完全离线推理。
# 模型缺失时自动回退到 OpenCV 自带的人脸级联检测（仅人脸框，不判定睁眼）。
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACE_MODEL_DIR = os.path.join(PROJECT_ROOT, 'models')
FACE_MODEL_FILENAME = 'face_landmarker.task'
FACE_MODEL_URL = (
    'https://storage.googleapis.com/mediapipe-models/face_landmarker/'
    'face_landmarker/float16/1/face_landmarker.task'
)


def face_model_path():
    """返回人脸关键点模型文件的完整路径。"""
    return os.path.join(FACE_MODEL_DIR, FACE_MODEL_FILENAME)

# ---------------------------------------------------------------------------
# 样式基线（由参考素材统计得出）
# ---------------------------------------------------------------------------
# tools/analyze_reference.py 扫描「素材3.0-3.2图片」后在项目根目录写出该文件，
# renderer 与 quality 共同读取，避免样式参数散落在代码里。
STYLE_PROFILE_NAME = 'style_profile.json'


def style_profile_path():
    """参考素材统计得到的样式基线文件路径。"""
    return os.path.join(PROJECT_ROOT, STYLE_PROFILE_NAME)


# 扫描参考素材时只看画面底部多大比例的区域（字幕一定在这里）
STYLE_SCAN_BAND_RATIO = 0.30
# 亮字判定：亮度不低于该值且饱和度不高于 STYLE_TEXT_MAX_SAT
STYLE_TEXT_MIN_VALUE = 200
STYLE_TEXT_MAX_SAT = 100
# 描边判定：亮度不高于该值
STYLE_STROKE_MAX_VALUE = 80
# 单张参考图至少要有这么多亮字像素才认为「这张图带字幕」
STYLE_MIN_TEXT_PIXELS_RATIO = 0.0004

# 人像加分权重。眼睛权重最高：出图最忌讳「闭眼 / 半闭眼」，它比嘴型更容易被
# 一眼看出来；嘴部只作为「是否正在说话」的辅助参考，权重相应下调。
FACE_SCORE_WEIGHTS = {
    'eye': 0.45,      # 睁眼程度（含双眼是否对称睁开）
    'mouth': 0.18,    # 嘴部自然度（是否正在自然说话）
    'symmetry': 0.12,  # 嘴角对称性
    'area': 0.25,     # 人脸面积占比
}

# ---------------------------------------------------------------------------
# 台词渲染（图片底部）
# ---------------------------------------------------------------------------
# 粗体优先：素材3.0 的字幕是粗体描边字，常规字重明显偏细
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
]

# 以下为「未找到样式基线文件」时的内置默认值，数值取自素材3.0 的统计中位数。
# 实际运行时优先采用 style_profile.json 中的统计结果。
BOTTOM_BAND_RATIO = 0.26         # 台词允许占用的最大高度比例（超出则自动缩字号）
FONT_SIZE_RATIO = 0.040          # 字号占画面高度比例
FONT_SIZE_MIN = 20
FONT_SIZE_MAX = 90
FONT_LINE_SPACING = 1.14         # 行距倍数
STROKE_RATIO = 0.085             # 描边宽度 / 字号
BOTTOM_MARGIN_RATIO = 0.022      # 末行底部距画面底部距离 / 画面高度
TEXT_MARGIN_RATIO = 0.045        # 文字距画面左右边距比例
MAX_CUE_LINES = 3                # 单句台词最多渲染行数
TEXT_COLOR = (255, 255, 255)     # 白字
STROKE_COLOR = (0, 0, 0)         # 黑描边（无底板）

# ---------------------------------------------------------------------------
# 剧情延续帧（无台词空窗补截）
# ---------------------------------------------------------------------------
# 台词截图只覆盖「有人在说话」的时段；台词之间的沉默画面（角色反应、物件
# 特写、回忆闪回）同样承载剧情。此处配置系统化补截这些空窗的口径。
STORY_GAP_MIN = 6.0            # 相邻台词间隔达到该秒数视为「剧情空窗」（含片头/片尾）
STORY_GAP_FPS = 1.0            # 空窗内候选帧采样密度（帧/秒）
STORY_GAP_MAX_SAMPLES = 30     # 单个空窗最多采样的候选帧数（防止长片头拖慢处理）
STORY_GAP_MAX_PER_GAP = 3      # 镜头去重后每个空窗最多补截的张数
STORY_GAP_EDGE_MARGIN = 0.4    # 采样点与前后台词的时间边距（秒），避开切镜残留
STORY_GAP_MIN_WINDOW = 0.5     # 扣除边距后不足该秒数的空窗不补截

# 剧情延续帧的句序 = 「前接台词序号 4 位」+「子后缀 a/b/c/...」，例如 0004a、0298b。
# 选 a/b/c 而非 02/03 是为了让剧情帧按文件名字母序天然插在「前一句台词图」与
# 「后一句台词图」之间（'a' > '9' > '0'，于是 0004 < 0004a < 0005）。片头空窗没
# 有效 prev_cue，用全 0 的 0000a/b/c 作为占位，排序时落在最前；片尾空窗则取
# 末句序号 a/b/c，排在最末。字母表预留到 z，默认 STORY_GAP_MAX_PER_GAP=3 远小
# 于 26；如未来上调上限再加双字母方案即可。
STORY_GAP_SUB_LETTERS = 'abcdefghijklmnopqrstuvwxyz'
STORY_GAP_HEAD_INDEX = 0       # 片头空窗的占位序号（无 prev_cue 时使用）

# ---------------------------------------------------------------------------
# 输出规则
# ---------------------------------------------------------------------------
OUTPUT_SUFFIX = "-pc"            # 输出目录 = 视频所在目录名 + 该后缀，如 03 -> 03-pc
IMAGE_FORMAT = "jpg"
JPEG_QUALITY = 95

# 索引与报告文件名
INDEX_CSV_NAME = "_index.csv"
REPORT_NAME = "_report.txt"
