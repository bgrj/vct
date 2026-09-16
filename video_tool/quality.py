# -*- coding: utf-8 -*-
"""候选帧质量评分

评分思路与需求保持一致 —— 整体画面清晰完整优先，人像自然度作为强约束项：

1. 清晰度：Laplacian 方差，按「同一句台词内部相对排序」归一化。
   纯黑/纯色/大虚化画面本身没有纹理，方差天然很低，不能因此当作模糊，
   所以这类「平坦画面」不参与模糊淘汰，只按亮度与人像加分参与排序。
2. 运动模糊：与相邻候选帧的差分幅度（帧间差异越大越可能是运动瞬间），
   结合清晰度共同判定严重模糊。权重刻意压低——帧间差异衡量的是运动幅度
   而非模糊本身，过高会盖过睁眼与嘴部等自然度判定。
3. 人像自然度：mediapipe Face Landmarker 给出眼睛开合比 EAR（左右眼分别计算）、
   嘴部纵横比 MAR、嘴角对称性与人脸面积。
   * 眼睛是重点：EAR 同时做「绝对标定」「句内相对标定」与「双眼是否对称睁开」；
   * 闭眼重罚、半闭眼轻罚、单眼微闭（眨眼瞬间）轻罚，大张口也扣分（需求硬约束），
     而非仅不加分；
   * 嘴部只作为「是否正在说话」的辅助参考，权重低于眼睛：闭着眼说话的画面
     再自然也不适合出图。
4. 亮度：极暗/极亮只轻微降权、不淘汰（用户明确允许「全黑 + 底部台词」的画面）。
5. 情境匹配：同一句台词内再比一次「谁更贴合这句话」——构图（人像居中程度
   与人脸占比）、时序（越贴近句子中点越好，句首句尾降权以避开转场残留）、
   镜头一致（优先选中句子中点所在镜头）；严重运动模糊与闭眼作为硬门槛。

眨眼、张嘴都是瞬时状态，单帧阈值极易误杀，因此核心做法是「同一句台词采样
多帧后相对比较、取最优」，绝对阈值只用于剔除严重运动模糊与闭眼瞬间。

人脸分析优先使用本地 mediapipe 模型（models/face_landmarker.task）；模型缺失
时自动回退到 OpenCV 自带的人脸级联检测（只能给出人脸框，无法判定睁眼）。
"""

from dataclasses import dataclass, field
import os

from .config import VideoToolError


class MissingDependency(VideoToolError):
    """缺少必要的第三方依赖（opencv-python / numpy）。"""


try:
    import numpy as np
except Exception:                                    # pragma: no cover
    np = None

try:
    import cv2
except Exception:                                    # pragma: no cover
    cv2 = None

# mediapipe 依赖的 glog 会在导入与推理时向 stderr 打印大量告警（模型加速、反馈张量
# 等），属正常现象却会淹没命令行输出，因此这里在导入前压低日志级别。
os.environ.setdefault('GLOG_minloglevel', '2')
os.environ.setdefault('TF_CPP_MIN_LOG_LEVEL', '3')

try:
    import mediapipe as mp
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
except Exception:                                    # pragma: no cover
    mp = None
    mp_python = None
    mp_vision = None

from . import config

# 人脸分析前把画面缩到该宽度，显著提速且几乎不影响判定精度
FACE_INPUT_WIDTH = 960

# Face Landmarker 眼部与嘴部关键点索引
_RIGHT_EYE = (33, 160, 158, 133, 153, 144)
_LEFT_EYE = (362, 385, 387, 263, 373, 380)
_MOUTH_LEFT, _MOUTH_RIGHT = 61, 291
_MOUTH_UPPER, _MOUTH_LOWER = 13, 14


def ensure_dependencies():
    """确认图像处理依赖可用，缺失时给出可执行的修复提示。"""
    if np is None or cv2 is None:
        raise MissingDependency(
            '缺少 opencv-python / numpy，请在项目目录执行：pip install -r requirements.txt'
        )


@dataclass
class FrameScore:
    """一帧候选画面的评分结果。"""

    path: str
    timestamp: float
    sharpness: float = 0.0        # 清晰度（Laplacian 方差）
    sharpness_norm: float = 0.0   # 句内归一化清晰度
    motion_penalty: float = 0.0   # 运动幅度（句内归一化），越大越可能模糊
    face_count: int = 0           # 检出人脸数量，0 表示无人像
    face_bonus: float = 0.0       # 人像加分：睁眼程度、嘴部自然度、人脸面积
    brightness: float = 0.0       # 平均亮度（0-255）
    total: float = 0.0            # 综合得分，用于同句候选排序
    degraded: bool = False        # 是否属降级输出（无人像 / 平坦画面 / 整体低分）
    flat: bool = False            # 是否为纯色、全黑、大虚化等平坦画面
    composition_score: float = 0.0
    timing_score: float = 0.0
    shot_id: int = 0
    shot_score: float = 0.0
    eye_state: str = ''
    detail: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 基础图像度量
# ---------------------------------------------------------------------------
def imread_unicode(path, flags=None):
    """读取图片，兼容 Windows 下含中文/非 ASCII 的路径。

    cv2.imread 在 Windows 上遇到非 ASCII 路径会直接返回 None，因此改用
    np.fromfile + cv2.imdecode。项目路径或临时目录含中文时这一步是必需的。
    """
    if flags is None:
        flags = cv2.IMREAD_COLOR
    try:
        buffer = np.fromfile(path, dtype=np.uint8)
        if buffer.size == 0:
            return None
        return cv2.imdecode(buffer, flags)
    except Exception:
        return None


def _load_color_and_gray(path, width=None):
    """读取图片并等比缩放，返回 (彩色图, 灰度图)。读取失败返回 (None, None)。"""
    width = width or config.CANDIDATE_SCALE_WIDTH
    image = imread_unicode(path, cv2.IMREAD_COLOR)
    if image is None:
        return None, None

    height, original_width = image.shape[:2]
    scale = float(width) / original_width if original_width > width else 1.0
    if scale < 1.0:
        image = cv2.resize(
            image,
            (max(1, int(original_width * scale)), max(1, int(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return image, gray


def _sharpness(gray):
    """Laplacian 方差，越大越清晰，对细节缺失敏感。"""
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _is_flat(gray):
    """判断是否为平坦画面（纯黑/纯色/大虚化）。

    这类画面没有纹理，Laplacian 方差天然很低，不能因此判定为「模糊」。
    """
    return float(gray.std()) < config.FLAT_STD_THRESHOLD


def _inter_frame_difference(gray_a, gray_b):
    """两帧灰度差的平均绝对值，用于衡量画面运动幅度。"""
    if gray_a is None or gray_b is None or gray_a.shape != gray_b.shape:
        return 0.0
    return float(np.mean(np.abs(gray_a.astype(np.int16) - gray_b.astype(np.int16))))


def _brightness_penalty(brightness):
    """亮度偏离合理区间时给出 0~1 的轻微惩罚，不作淘汰。"""
    if brightness < config.BRIGHTNESS_LOW:
        gap = (config.BRIGHTNESS_LOW - brightness) / max(1.0, config.BRIGHTNESS_LOW)
        return min(1.0, gap)
    if brightness > config.BRIGHTNESS_HIGH:
        gap = (brightness - config.BRIGHTNESS_HIGH) / max(1.0, 255.0 - config.BRIGHTNESS_HIGH)
        return min(1.0, gap)
    return 0.0


# ---------------------------------------------------------------------------
# 人像几何度量
# ---------------------------------------------------------------------------
def _distance(point_a, point_b):
    return float(np.linalg.norm(np.asarray(point_a, dtype=np.float32)
                                - np.asarray(point_b, dtype=np.float32)))


def _eye_aspect_ratios(points):
    """左右眼开合比（EAR）。

    EAR = 眼睛高度 / 眼睛宽度，越大表示睁得越开。返回值 (较小值, 较大值)：
    较小值用于判断「有没有闭眼」，两者之比用于判断「是不是一只眼眯着」
    （典型的眨眼瞬间）。
    """
    ratios = []
    for indices in (_RIGHT_EYE, _LEFT_EYE):
        picked = [points[i] for i in indices]
        horizontal = _distance(picked[0], picked[3])
        if horizontal <= 1e-6:
            continue
        vertical = _distance(picked[1], picked[5]) + _distance(picked[2], picked[4])
        ratios.append(vertical / (2.0 * horizontal))
    if not ratios:
        return 0.0, 0.0
    return float(min(ratios)), float(max(ratios))


def _eye_aspect_ratio(points):
    """眼睛开合比 EAR：取双眼中的较小值，宁可保守也不误判。"""
    return _eye_aspect_ratios(points)[0]


def _eye_balance(ear_small, ear_large):
    """双眼开合平衡度：两眼开合接近时接近 1，一眼睁一眼眯时明显偏低。"""
    if ear_large <= 1e-6:
        return 0.0
    if ear_small <= 0.0:
        return 0.0
    ratio = ear_small / ear_large
    span = max(1e-6, 1.0 - config.EYE_BALANCE_MIN)
    return float(max(0.0, min(1.0, (ratio - config.EYE_BALANCE_MIN) / span)))


def _mouth_aspect_ratio(points):
    """嘴部纵横比 MAR：数值大表示正在大张口。"""
    width = _distance(points[_MOUTH_LEFT], points[_MOUTH_RIGHT])
    if width <= 1e-6:
        return 0.0
    return _distance(points[_MOUTH_UPPER], points[_MOUTH_LOWER]) / width


def _mouth_asymmetry(points):
    """嘴角高度差与嘴宽之比，用于识别嘴部扭曲的不自然瞬间。"""
    width = _distance(points[_MOUTH_LEFT], points[_MOUTH_RIGHT])
    if width <= 1e-6:
        return 0.0
    return abs(points[_MOUTH_LEFT][1] - points[_MOUTH_RIGHT][1]) / width


class FaceAnalyzer:
    """人像指标提取（纯本地推理）。

    只负责给出客观指标（人脸数、EAR、MAR、面积等），是否加分、是否扣分
    交由 score_candidates 结合「同句候选」做相对判断。

    三种后台模式：
      mesh   —— mediapipe Face Landmarker，含左/右眼与嘴部关键点，判定最完整
      haar   —— OpenCV 自带级联检测，仅人脸框（模型文件缺失时的兜底）
      none   —— 无人脸分析，其余评分逻辑照常工作
    """

    def __init__(self):
        self._landmarker = None
        self._cascade = None
        self._mode = 'none'
        self._init_backend()

    def _init_backend(self):
        # 人脸指标提取依赖 OpenCV（缩放、转灰度、Laplacian），缺失时不做任何人脸分析
        if cv2 is None:
            return
        if self._init_landmarker():
            self._mode = 'mesh'
            return
        if self._init_cascade():
            self._mode = 'haar'

    def _init_landmarker(self):
        """尝试加载 mediapipe Face Landmarker（需要本地模型文件）。"""
        if mp is None or mp_vision is None or mp_python is None:
            return False

        model_path = config.face_model_path()
        if not os.path.isfile(model_path):
            return False

        try:
            options = mp_vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=model_path),
                running_mode=mp_vision.RunningMode.IMAGE,
                num_faces=config.FACE_MAX_NUM,
                min_face_detection_confidence=config.FACE_MIN_DETECTION_CONFIDENCE,
                min_face_presence_confidence=config.FACE_MIN_DETECTION_CONFIDENCE,
                min_tracking_confidence=config.FACE_MIN_DETECTION_CONFIDENCE,
            )
            self._landmarker = mp_vision.FaceLandmarker.create_from_options(options)
            return True
        except Exception:
            self._landmarker = None
            return False

    def _init_cascade(self):
        """回退方案：OpenCV 自带的 Haar 人脸级联（无需额外下载）。"""
        try:
            cascade_dir = getattr(getattr(cv2, 'data', None), 'haarcascades', None)
            if not cascade_dir:
                return False
            path = os.path.join(cascade_dir, 'haarcascade_frontalface_default.xml')
            if not os.path.isfile(path):
                return False
            cascade = cv2.CascadeClassifier(path)
            if cascade.empty():
                return False
            self._cascade = cascade
            return True
        except Exception:
            self._cascade = None
            return False

    @property
    def mode(self):
        """当前后台模式：mesh / haar / none。"""
        return self._mode

    @property
    def available(self):
        return self._mode != 'none'

    @property
    def can_judge_eyes(self):
        """只有 mesh 模式才能判定睁眼与嘴部自然度。"""
        return self._mode == 'mesh'

    def describe(self):
        if self._mode == 'mesh':
            return 'mediapipe Face Landmarker（本地推理，含睁眼与嘴部判定）'
        if self._mode == 'haar':
            return 'OpenCV 人脸级联（本地推理，仅人脸框；未找到 %s）' % config.FACE_MODEL_FILENAME
        return '未启用（缺少 mediapipe 或模型文件，仅按画面质量评分）'

    def close(self):
        try:
            if self._landmarker is not None:
                self._landmarker.close()
        except Exception:
            pass

    def analyze(self, color_image):
        """返回 (人脸数, 最佳人脸的指标字典)。

        指标字典可能包含：area_ratio / face_sharpness / ear / mar /
        mouth_asymmetry / landmarks_available。
        """
        if self._mode == 'none' or color_image is None:
            return 0, {}

        height, width = color_image.shape[:2]
        scale = float(FACE_INPUT_WIDTH) / width if width > FACE_INPUT_WIDTH else 1.0
        if scale < 1.0:
            prepared = cv2.resize(
                color_image,
                (max(1, int(width * scale)), max(1, int(height * scale))),
                interpolation=cv2.INTER_AREA,
            )
        else:
            prepared = color_image

        if self._mode == 'mesh':
            return self._analyze_with_landmarker(prepared)
        return self._analyze_with_cascade(prepared)

    # -- mediapipe Face Landmarker -----------------------------------------
    def _analyze_with_landmarker(self, color_image):
        try:
            rgb = np.ascontiguousarray(cv2.cvtColor(color_image, cv2.COLOR_BGR2RGB))
            height, width = rgb.shape[:2]
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = self._landmarker.detect(mp_image)
        except Exception:
            return 0, {}

        faces = getattr(result, 'face_landmarks', None)
        if not faces:
            return 0, {}

        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        best_metrics = {}
        best_area = -1.0
        for landmarks in faces:
            points = np.array(
                [(lm.x * width, lm.y * height) for lm in landmarks],
                dtype=np.float32,
            )
            if points.shape[0] <= _MOUTH_LOWER:
                continue
            metrics = self._landmark_metrics(points, gray, width, height)
            # 多张人脸时以面积最大者为准（通常即说话的主体人物）
            if metrics['area_ratio'] > best_area:
                best_area = metrics['area_ratio']
                best_metrics = metrics
        return len(faces), best_metrics

    @staticmethod
    def _landmark_metrics(points, gray, width, height):
        xs, ys = points[:, 0], points[:, 1]
        box_width = max(0.0, float(xs.max() - xs.min()))
        box_height = max(0.0, float(ys.max() - ys.min()))
        center_x = float((xs.min() + xs.max()) / 2.0) / float(width or 1)
        center_y = float((ys.min() + ys.max()) / 2.0) / float(height or 1)
        ear_small, ear_large = _eye_aspect_ratios(points)
        return {
            'area_ratio': (box_width * box_height) / float(width * height or 1),
            'center_x': center_x,
            'center_y': center_y,
            'face_sharpness': FaceAnalyzer._crop_sharpness(gray, xs, ys),
            'ear': ear_small,
            'ear_small': ear_small,
            'ear_large': ear_large,
            'eye_balance': _eye_balance(ear_small, ear_large),
            'mar': _mouth_aspect_ratio(points),
            'mouth_asymmetry': _mouth_asymmetry(points),
            'landmarks_available': True,
        }

    @staticmethod
    def _crop_sharpness(gray, xs, ys):
        """人脸区域的 Laplacian 方差。"""
        height, width = gray.shape[:2]
        x0 = int(max(0, min(width - 1, xs.min())))
        x1 = int(max(0, min(width, xs.max())))
        y0 = int(max(0, min(height - 1, ys.min())))
        y1 = int(max(0, min(height, ys.max())))
        if x1 - x0 < 4 or y1 - y0 < 4:
            return 0.0
        return float(cv2.Laplacian(gray[y0:y1, x0:x1], cv2.CV_64F).var())

    # -- OpenCV 级联兜底 ---------------------------------------------------
    def _analyze_with_cascade(self, color_image):
        try:
            gray = cv2.cvtColor(color_image, cv2.COLOR_BGR2GRAY)
            height, width = gray.shape[:2]
            min_side = max(24, int(min(width, height) * 0.06))
            faces = self._cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_side, min_side)
            )
        except Exception:
            return 0, {}

        if faces is None or len(faces) == 0:
            return 0, {}

        best_metrics = {}
        best_area = -1.0
        for (x, y, box_w, box_h) in faces:
            metrics = {
                'area_ratio': float(box_w * box_h) / float(width * height or 1),
                'center_x': (x + box_w / 2.0) / float(width or 1),
                'center_y': (y + box_h / 2.0) / float(height or 1),
                'face_sharpness': float(
                    cv2.Laplacian(gray[y:y + box_h, x:x + box_w], cv2.CV_64F).var()
                ),
                'landmarks_available': False,
            }
            if metrics['area_ratio'] > best_area:
                best_area = metrics['area_ratio']
                best_metrics = metrics
        return len(faces), best_metrics


# ---------------------------------------------------------------------------
# 人像加分与硬惩罚
# ---------------------------------------------------------------------------
def _face_bonus(metrics, ear_reference):
    """把客观指标折算为 0~1 的人像加分。

    ear_reference 为同一句台词内所有候选帧的最大眼睛开合比，用于做句内
    相对标定，避免「人脸远近」导致 EAR 绝对值漂移造成的误判。
    """
    if not metrics:
        return 0.0

    weights = config.FACE_SCORE_WEIGHTS
    area_score = min(1.0, metrics.get('area_ratio', 0.0) / config.FACE_AREA_REFERENCE)
    sharpness_score = min(
        1.0, metrics.get('face_sharpness', 0.0) / config.FACE_SHARPNESS_REFERENCE
    )

    if not metrics.get('landmarks_available'):
        # 兜底模式：只能按人脸面积与人脸清晰度给分
        return 0.6 * area_score + 0.4 * sharpness_score

    ear = metrics.get('ear', 0.0)
    mar = metrics.get('mar', 0.0)
    asymmetry = metrics.get('mouth_asymmetry', 0.0)

    # 睁眼程度 = 绝对标定（是否达到「明显睁开」）+ 句内相对标定（同句里谁睁得更大）
    # + 双眼是否对称睁开（避开「一只眼正常、一只眼眯着」的眨眼瞬间）。
    ear_small = metrics.get('ear_small', ear)
    ear_large = metrics.get('ear_large', ear)
    balance = metrics.get('eye_balance')
    if balance is None:
        balance = _eye_balance(ear_small, ear_large)

    if ear <= config.EAR_CLOSED_THRESHOLD or ear_large <= 1e-6:
        eye_score = 0.0
    else:
        span = max(1e-6, config.EAR_OPEN_REFERENCE - config.EAR_CLOSED_THRESHOLD)
        absolute = min(1.0, (ear - config.EAR_CLOSED_THRESHOLD) / span)
        relative = min(1.0, ear / ear_reference) if ear_reference > 0 else 1.0
        base = 0.5 * absolute + 0.5 * relative
        balance_weight = config.EAR_BALANCE_WEIGHT
        eye_score = (1.0 - balance_weight) * base + balance_weight * balance

    # 说话活跃度：张口幅度落在「自然说话」区间给满分；闭嘴（没在说话）降权；
    # 开口过大沿区间线性衰减到 0（更夸张的张口另有 _hard_penalty 扣分）。
    if mar < config.MAR_NATURAL_MIN:
        mouth_score = config.MOUTH_IDLE_SCORE
    elif mar <= config.MAR_NATURAL_MAX:
        mouth_score = 1.0
    else:
        span = max(1e-6, config.MAR_OPEN_THRESHOLD - config.MAR_NATURAL_MAX)
        mouth_score = max(0.0, 1.0 - (mar - config.MAR_NATURAL_MAX) / span)

    symmetry_score = max(
        0.0, 1.0 - min(1.0, asymmetry / config.MOUTH_ASYMMETRY_LIMIT)
    )

    score = (
        weights['eye'] * eye_score
        + weights['mouth'] * mouth_score
        + weights['symmetry'] * symmetry_score
        + weights['area'] * area_score
    )
    # 人脸区域本身模糊时再打折，避免「有脸但糊」被选为最优
    return float(score * (0.6 + 0.4 * sharpness_score))


def _hard_penalty(metrics):
    """闭眼/半闭眼/单眼微闭与大张口属于需求明确排除的状态，直接扣分。

    眼睛分三档：完全闭上重罚、半闭轻罚、双眼不对称（一只眼睁一只眼眯）轻罚；
    嘴部只罚「张口过大」。这样即使其它指标（清晰度、构图）很好，也不会把
    一个闭着眼或眯着眼的画面选出来。
    """
    if not metrics or not metrics.get('landmarks_available'):
        return 0.0
    penalty = 0.0
    ear = metrics.get('ear', 0.0)
    ear_large = metrics.get('ear_large', ear)
    if ear <= config.EAR_CLOSED_THRESHOLD:
        penalty += config.EYE_CLOSED_PENALTY
    elif ear < config.EAR_OPEN_REFERENCE * config.EAR_HALF_RATIO:
        # 介于「闭眼」与「完全睁开」之间：单看不够好，但不至于完全不能用
        penalty += config.EYE_HALF_PENALTY
    if ear_large > 1e-6 and (ear / ear_large) < config.EYE_BALANCE_MIN:
        # 一只眼睁得开、另一只明显眯着 —— 眨眼瞬间的典型特征
        penalty += config.EYE_BALANCE_PENALTY
    if metrics.get('mar', 0.0) > config.MAR_OPEN_THRESHOLD:
        penalty += config.MOUTH_OPEN_PENALTY
    return penalty


# ---------------------------------------------------------------------------
# 候选帧打分与选优
# ---------------------------------------------------------------------------
def _composition_score(metrics):
    """构图分：人像居中且占比合理时得分高。"""
    if not metrics:
        return 0.0
    center_x = metrics.get('center_x', 0.5)
    center_y = metrics.get('center_y', 0.5)
    offset = ((center_x - 0.5) ** 2 + (center_y - 0.5) ** 2) ** 0.5
    center_score = max(0.0, 1.0 - min(1.0, offset / config.COMPOSITION_CENTER_TOLERANCE))
    area_score = min(1.0, metrics.get('area_ratio', 0.0) / config.FACE_AREA_REFERENCE)
    return (config.COMPOSITION_CENTER_WEIGHT * center_score
            + config.COMPOSITION_AREA_WEIGHT * area_score)


def _shot_groups(records):
    """按镜头切分同句候选帧，返回与 records 等长的组号列表。

    相邻候选帧只隔几帧到十几帧，正常运镜下灰度直方图相关性很高；切镜时
    相关性骤降。据此把它们切成若干「镜头组」，避免选中切镜瞬间的残留画面。
    """
    groups = []
    current = 0
    previous_hist = None
    for record in records:
        histogram = cv2.calcHist([record['gray']], [0], None, [64], [0, 256])
        cv2.normalize(histogram, histogram, 0.0, 1.0, cv2.NORM_MINMAX)
        if previous_hist is not None:
            correlation = cv2.compareHist(previous_hist, histogram, cv2.HISTCMP_CORREL)
            if correlation < config.SHOT_CHANGE_CORRELATION:
                current += 1
        groups.append(current)
        previous_hist = histogram
    return groups


def _timing_scores(records):
    """时序分：越贴近句子中点越高，句首/句尾降权（避开转场与切镜残留）。"""
    if not records:
        return []

    times = [record['timestamp'] for record in records]
    start, end = min(times), max(times)
    span = max(1e-6, end - start)
    scores = []
    for timestamp in times:
        position = (timestamp - start) / span
        center_score = 1.0 - min(1.0, abs(position - 0.5) * 2.0)
        if position < config.TIMING_EDGE_RATIO or position > 1.0 - config.TIMING_EDGE_RATIO:
            center_score *= 0.5
        scores.append(center_score)
    return scores


def _eye_state(metrics):
    """把睁眼程度归为 open / half / closed 三态，供人工复核使用。

    除「整体睁得够不够」外，还看双眼是否对称：一只眼睁、一只眼眯（眨眼瞬间）
    同样记为 half，避免「平均值看着正常、其实在眨眼」的帧被当成合格帧。
    """
    if not metrics or not metrics.get('landmarks_available'):
        return ''
    ear = metrics.get('ear', 0.0)
    ear_large = metrics.get('ear_large', ear)
    if ear <= config.EAR_CLOSED_THRESHOLD:
        return 'closed'
    if ear < config.EAR_OPEN_REFERENCE * config.EAR_HALF_RATIO:
        return 'half'
    if ear_large > 1e-6 and (ear / ear_large) < config.EYE_BALANCE_MIN:
        return 'half'
    return 'open'


def _speaking_state(metrics):
    """说话状态三态：speaking（正在自然说话）/ idle（闭嘴）/ open（张口过大）。"""
    if not metrics or not metrics.get('landmarks_available'):
        return ''
    mar = metrics.get('mar', 0.0)
    if mar < config.MAR_NATURAL_MIN:
        return 'idle'
    if mar <= config.MAR_NATURAL_MAX:
        return 'speaking'
    return 'open'


def score_candidates(candidates, analyzer=None):
    """对同一句台词的候选帧打分。

    candidates 为 [(图片路径, 时间点秒), ...]；返回 FrameScore 列表（顺序与输入一致）。
    """
    ensure_dependencies()
    analyzer = analyzer or FaceAnalyzer()

    records = []
    for path, timestamp in candidates:
        color, gray = _load_color_and_gray(path)
        if gray is None:
            continue

        face_count, metrics = 0, {}
        if analyzer.available:
            face_count, metrics = analyzer.analyze(color)

        records.append({
            'path': path,
            'timestamp': timestamp,
            'gray': gray,
            'sharpness': _sharpness(gray),
            'flat': _is_flat(gray),
            'brightness': float(gray.mean()),
            'face_count': face_count,
            'metrics': metrics,
        })

    if not records:
        return []

    # 句内相对归一化：不同场景的清晰度绝对量级差异极大，绝对阈值不可靠
    sharpness_pool = [r['sharpness'] for r in records if not r['flat']]
    sharpness_max = max(sharpness_pool) if sharpness_pool else 0.0

    ear_reference = max(
        (r['metrics'].get('ear', 0.0) for r in records if r['metrics']), default=0.0
    )

    motion_raw = []
    for position, record in enumerate(records):
        neighbours = []
        if position > 0:
            neighbours.append(
                _inter_frame_difference(records[position - 1]['gray'], record['gray'])
            )
        if position + 1 < len(records):
            neighbours.append(
                _inter_frame_difference(record['gray'], records[position + 1]['gray'])
            )
        motion_raw.append(sum(neighbours) / len(neighbours) if neighbours else 0.0)
    motion_max = max(motion_raw) if motion_raw else 0.0

    shot_ids = _shot_groups(records)
    timing_scores = _timing_scores(records)
    timestamps = [record['timestamp'] for record in records]
    midpoint = (min(timestamps) + max(timestamps)) / 2.0
    mid_position = min(range(len(records)),
                       key=lambda item: abs(records[item]['timestamp'] - midpoint))
    midpoint_shot = shot_ids[mid_position]

    scores = []
    for position, record in enumerate(records):
        if record['flat']:
            # 平坦画面（全黑、纯色、大虚化）不参与模糊判定，给中性清晰度分
            sharpness_norm = config.FLAT_SHARPNESS_NEUTRAL
        elif sharpness_max > 0:
            sharpness_norm = record['sharpness'] / sharpness_max
        else:
            sharpness_norm = 0.0

        motion_norm = (motion_raw[position] / motion_max) if motion_max > 0 else 0.0
        brightness_penalty = _brightness_penalty(record['brightness'])
        face_bonus = _face_bonus(record['metrics'], ear_reference)

        penalty = _hard_penalty(record['metrics'])
        composition = _composition_score(record['metrics'])
        timing = timing_scores[position] if position < len(timing_scores) else 0.0
        shot_score = 1.0 if shot_ids[position] == midpoint_shot else 0.0

        total = (
            config.WEIGHT_SHARPNESS * sharpness_norm
            - config.WEIGHT_MOTION * motion_norm
            + config.WEIGHT_FACE * face_bonus
            - config.WEIGHT_BRIGHTNESS * brightness_penalty
            + config.WEIGHT_COMPOSITION * composition
            + config.WEIGHT_TIMING * timing
            + config.WEIGHT_SHOT * shot_score
            - penalty
        )

        # 严重运动模糊：画面本应有纹理（非平坦）却明显比同句其他帧糊，
        # 同时帧间差异大 —— 这才是真正该避开的一帧。
        severely_blurred = (
            not record['flat']
            and sharpness_norm < config.SEVERE_BLUR_RATIO
            and motion_norm > config.SEVERE_MOTION_RATIO
        )
        if severely_blurred:
            total -= config.SEVERE_BLUR_PENALTY

        metrics = record['metrics'] or {}
        degraded = (
            record['face_count'] == 0
            or record['flat']
            or total < config.LOW_QUALITY_THRESHOLD
        )

        detail = {
            'area_ratio': round(metrics.get('area_ratio', 0.0), 4),
            'face_sharpness': round(metrics.get('face_sharpness', 0.0), 1),
            'landmarks_available': bool(metrics.get('landmarks_available')),
            'severely_blurred': severely_blurred,
            'eye_state': _eye_state(record['metrics']),
            'speaking': _speaking_state(record['metrics']),
        }
        if metrics.get('landmarks_available'):
            ear_small = metrics.get('ear_small', metrics.get('ear', 0.0))
            ear_large = metrics.get('ear_large', ear_small)
            detail.update({
                'ear': round(ear_small, 4),
                'ear_small': round(ear_small, 4),
                'ear_large': round(ear_large, 4),
                'eye_balance': round(metrics.get('eye_balance', _eye_balance(
                    ear_small, ear_large)), 4),
                'mar': round(metrics.get('mar', 0.0), 4),
                'mouth_asymmetry': round(metrics.get('mouth_asymmetry', 0.0), 4),
                'eyes_closed': bool(ear_small <= config.EAR_CLOSED_THRESHOLD),
                # 半闭眼含两种情况：整体睁开不足，或一只眼睁一只眼眯
                'eyes_half': bool(detail.get('eye_state') == 'half'),
                'mouth_too_open': bool(metrics.get('mar', 0.0) > config.MAR_OPEN_THRESHOLD),
            })

        scores.append(FrameScore(
            path=record['path'],
            timestamp=record['timestamp'],
            sharpness=record['sharpness'],
            sharpness_norm=round(sharpness_norm, 4),
            motion_penalty=round(motion_norm, 4),
            face_count=record['face_count'],
            face_bonus=round(face_bonus, 4),
            brightness=round(record['brightness'], 1),
            total=round(total, 4),
            degraded=degraded,
            flat=record['flat'],
            composition_score=round(composition, 4),
            timing_score=round(timing, 4),
            shot_id=shot_ids[position],
            shot_score=shot_score,
            eye_state=detail.get('eye_state', ''),
            detail=detail,
        ))

    return scores


def _speaking_pool(scores):
    """筛掉明显不合格的帧：严重运动模糊、闭眼（若整句都如此则退回全集）。"""
    pool = [item for item in scores if not item.detail.get('severely_blurred')]
    pool = pool or scores
    pool = [item for item in pool if item.eye_state != 'closed'] or pool
    return pool


# 眼睛状态在排序中的优先级：睁开(2) > 未知/无人像(1) > 半闭(0) > 闭眼(-1)。
# 只作为「综合得分相同」时的次级依据，用于把「睁着眼」的画面顶到前面。
_EYE_RANK = {'open': 2, '': 1, 'half': 0, 'closed': -1}


def eye_rank(state):
    """眼睛状态的排序优先级，供流程编排判断「重采样是否挑到了更好的帧」。"""
    return _EYE_RANK.get(state or '', 1)


def eye_needs_refine(state, mode='closed'):
    """该眼睛状态是否触发重采样。

    closed —— 只重采样「整句候选都闭眼」的句子（默认，代价最小）；
    half    —— 连「半闭 / 单眼微闭」的句子一起重采样（更严格，耗时略增）。
    """
    if mode == 'off':
        return False
    if mode == 'half':
        return state in ('closed', 'half')
    return state == 'closed'


def select_best(scores):
    """在同一句台词的候选帧中选出最优一帧。

    两步走：
    1. 先取本句最高分，把「最高分减去优选窗口」以内的帧视为「一样好」；
    2. 在这批帧里优先「睁着眼」的画面，其次才比综合得分、人像加分、清晰度。

    这样同等画质下不会选到半闭眼的一帧，而明显更差（例如糊了一截）的帧也不会
    仅仅因为睁着眼就被选中。
    """
    if not scores:
        return None

    pool = _speaking_pool(scores)
    if not pool:
        return None

    best_total = max(item.total for item in pool)
    near_best = [
        item for item in pool
        if item.total >= best_total - config.EYE_PREFERENCE_MARGIN
    ]
    return max(
        near_best,
        key=lambda item: (
            _EYE_RANK.get(item.eye_state, 1),
            item.total,
            item.face_bonus,
            item.sharpness,
            -item.timestamp,
        ),
    )
