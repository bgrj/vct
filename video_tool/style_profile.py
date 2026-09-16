# -*- coding: utf-8 -*-
"""参考素材样式基线

把「人工逐张截图」里固化下来的字幕观感（字号、描边、行距、贴底位置）与构图
习惯，从参考素材图片中统计出来，写成 style_profile.json，供 renderer（画字）
与 quality（构图阈值）共同消费。

做法是统计式特征提取而非模型训练：
* 参考素材只有几百张，目标只是把人工审美固化成可复现、可调、可回滚的参数；
* 字幕必然出现在画面底部，因此在底部条带内检测「亮字 + 暗描边」像素；
* 单张图的测量噪声很大（有的图没字幕、有的字被裁切），因此对整批样本取
  中位数，得到稳定的基线，而不是被个别极端样本带偏。

依赖 cv2 / numpy；两者缺失时由调用方决定是否降级（本模块在 import 时不报错）。
"""

import json
import os
from dataclasses import asdict, dataclass

from . import config

try:
    import numpy as np
except Exception:                                        # pragma: no cover
    np = None

try:
    import cv2
except Exception:                                        # pragma: no cover
    cv2 = None


# ---------------------------------------------------------------------------
# 样式契约
# ---------------------------------------------------------------------------
@dataclass
class SubtitleStyle:
    """由参考素材统计得到的字幕样式基线。"""

    font_path: str = ''                 # 优先粗体中文字体
    font_size_ratio: float = config.FONT_SIZE_RATIO      # 字号 / 画面高度
    stroke_ratio: float = config.STROKE_RATIO            # 描边宽度 / 字号
    line_spacing: float = config.FONT_LINE_SPACING       # 行距倍数
    bottom_margin_ratio: float = config.BOTTOM_MARGIN_RATIO   # 末行底部距画面底部 / 画面高度
    max_lines: int = config.MAX_CUE_LINES                # 单句最大行数
    text_color: tuple = config.TEXT_COLOR                # 白字
    stroke_color: tuple = config.STROKE_COLOR            # 黑描边
    source: str = '内置默认值'                            # 基线来源说明

    def to_dict(self):
        data = asdict(self)
        data['text_color'] = list(self.text_color)
        data['stroke_color'] = list(self.stroke_color)
        return data

    @classmethod
    def from_dict(cls, data):
        """从 JSON 字典构造；缺失字段沿用默认值，颜色统一转为元组。"""
        data = data or {}
        kwargs = {}
        for name in ('font_path', 'font_size_ratio', 'stroke_ratio', 'line_spacing',
                     'bottom_margin_ratio', 'max_lines', 'source'):
            if name in data and data[name] is not None:
                kwargs[name] = data[name]
        for name, fallback in (('text_color', config.TEXT_COLOR),
                               ('stroke_color', config.STROKE_COLOR)):
            value = data.get(name)
            try:
                kwargs[name] = tuple(int(v) for v in value)
            except (TypeError, ValueError):
                kwargs[name] = fallback

        style = cls(**kwargs)
        style.normalize()
        return style

    def normalize(self):
        """把统计结果夹到合理区间，避免异常样本导致字号/描边失控。"""
        self.font_size_ratio = float(min(0.080, max(0.020, self.font_size_ratio)))
        # 下限 0.05：素材统计值偏保守，实拍中白字压在亮部背景上时会不够清晰
        self.stroke_ratio = float(min(0.200, max(0.050, self.stroke_ratio)))
        self.line_spacing = float(min(1.30, max(1.05, self.line_spacing)))
        self.bottom_margin_ratio = float(min(0.120, max(0.002, self.bottom_margin_ratio)))
        self.max_lines = int(min(4, max(1, self.max_lines)))
        if not self.font_path:
            self.font_path = default_font_path()
        return self


def default_font_path():
    """按候选顺序返回第一个存在的中文字体（粗体优先）。"""
    for candidate in config.FONT_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate
    return config.FONT_CANDIDATES[0] if config.FONT_CANDIDATES else ''


def default_style(source='内置默认值'):
    """内置默认样式（参考素材不可用时的兜底）。"""
    return SubtitleStyle(font_path=default_font_path(), source=source).normalize()


# ---------------------------------------------------------------------------
# 读取与写出
# ---------------------------------------------------------------------------
_profile_cache = {}


def load_style_profile(path=None, reload=False):
    """读取样式基线；文件缺失或损坏时回退到内置默认值。

    path 为 None 时使用 config.style_profile_path()。
    """
    path = path or config.style_profile_path()
    if not reload and path in _profile_cache:
        return _profile_cache[path]

    style = None
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as handle:
                data = json.load(handle)
            payload = data.get('style', data) if isinstance(data, dict) else {}
            style = SubtitleStyle.from_dict(payload)
            meta = data.get('source') if isinstance(data, dict) else None
            style.source = str(payload.get('source') or meta or path)
        except (OSError, ValueError, TypeError):
            style = None

    if style is None:
        style = default_style('内置默认值（未找到 %s）' % os.path.basename(path))

    style.normalize()
    _profile_cache[path] = style
    return style


def save_style_profile(style, stats=None, path=None):
    """写出样式基线文件（含统计明细，便于人工核对与再调参）。"""
    path = path or config.style_profile_path()
    payload = {
        'style': style.to_dict(),
        'stats': stats or {},
        'source': style.source,
    }
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    _profile_cache.pop(path, None)
    return path


# ---------------------------------------------------------------------------
# 参考素材特征提取
# ---------------------------------------------------------------------------
def _require_cv():
    if cv2 is None or np is None:
        raise RuntimeError('样式分析需要 opencv-python 与 numpy，请先安装 requirements.txt')


def iter_reference_images(root_dir):
    """递归列出参考素材目录下的全部图片。"""
    extensions = ('.png', '.jpg', '.jpeg', '.bmp', '.webp')
    images = []
    for current_dir, dir_names, file_names in os.walk(root_dir):
        dir_names.sort()
        for name in sorted(file_names):
            if name.lower().endswith(extensions):
                images.append(os.path.join(current_dir, name))
    return images


def read_image(path):
    """读取图片（兼容 Windows 下含中文的路径）。"""
    _require_cv()
    try:
        buffer = np.fromfile(path, dtype=np.uint8)
        if buffer.size == 0:
            return None
        return cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    except Exception:
        return None


def _line_runs(row_flags, max_gap=2):
    """把「有字的行」标记数组切成若干连续行段（允许中间有空隙）。

    返回 [(起始行, 结束行), ...]，均为闭区间下标。
    """
    runs = []
    start = None
    gap = 0
    for position, flag in enumerate(row_flags):
        if flag:
            if start is None:
                start = position
            gap = 0
        elif start is not None:
            gap += 1
            if gap > max_gap:
                runs.append((start, position - gap))
                start = None
                gap = 0
    if start is not None:
        runs.append((start, len(row_flags) - 1 - gap if gap else len(row_flags) - 1))
    return runs


def _stroke_width(mask):
    """估计笔画宽度：白字笔画的「最大内切半径 × 2」。

    用距离变换取内部像素到背景的最大距离，近似等于笔画半宽；对像素分布取
    高分位数，避免个别粗笔画（如「口」的转角）拉高整体估计。
    """
    distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
    values = distance[distance > 0.5]
    if values.size == 0:
        return 0.0
    return float(np.percentile(values, 85) * 2.0)


def measure_subtitle(image):
    """测量单张图片的字幕特征。

    返回 None 表示该图没有可识别的「亮字 + 暗描边」字幕；
    否则返回 dict：字行列表与整图比例指标。
    """
    _require_cv()
    if image is None:
        return None

    height, width = image.shape[:2]
    if height < 80 or width < 80:
        return None

    band_ratio = min(0.90, max(0.05, config.STYLE_SCAN_BAND_RATIO))
    band_top = int(height * (1.0 - band_ratio))
    band = image[band_top:, :]

    hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
    # 白色文字：亮度高、饱和度低；黑色描边：亮度极低
    bright = (hsv[:, :, 2] >= config.STYLE_TEXT_MIN_VALUE) & \
             (hsv[:, :, 1] <= config.STYLE_TEXT_MAX_SAT)
    dark = hsv[:, :, 2] <= config.STYLE_STROKE_MAX_VALUE

    band_height, band_width = band.shape[:2]
    radius = max(2, int(round(height * 0.004)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    dark_near_text = cv2.dilate(bright.astype(np.uint8), kernel) > 0
    outline = dark & dark_near_text
    if not outline.any():
        return None

    # 只保留「周围确实有黑描边」的亮字像素，排除画面里的白色高光
    text_mask = bright & (cv2.dilate(outline.astype(np.uint8), kernel) > 0)
    if text_mask.sum() < max(24, config.STYLE_MIN_TEXT_PIXELS_RATIO * band_height * band_width):
        return None

    rows = text_mask.sum(axis=1)
    row_threshold = max(3, int(band_width * 0.003))
    runs = _line_runs(rows >= row_threshold)
    lines = []
    for top, bottom in runs:
        if bottom < top:
            continue
        segment = text_mask[top:bottom + 1, :]
        if segment.sum() < 24:
            continue
        columns = np.where(segment.any(axis=0))[0]
        if columns.size == 0:
            continue
        lines.append({
            'top': int(top),
            'bottom': int(bottom),
            'height': int(bottom - top + 1),
            'left': int(columns[0]),
            'right': int(columns[-1]),
            'center_x': float((columns[0] + columns[-1]) / 2.0),
            'pixels': int(segment.sum()),
            'stroke_width': _stroke_width(segment),
        })

    if not lines:
        return None

    lines.sort(key=lambda item: item['top'])
    heights = [item['height'] for item in lines]
    strokes = [item['stroke_width'] for item in lines if item['stroke_width'] > 0]
    centers = [item['center_x'] for item in lines]
    total_left = min(item['left'] for item in lines)
    total_right = max(item['right'] for item in lines)
    lowest_bottom = max(item['bottom'] for item in lines) + band_top

    spacing = []
    for previous, current in zip(lines, lines[1:]):
        spacing.append((current['top'] - previous['top']) / float(max(1, previous['height'])))

    metrics = {
        'width': width,
        'height': height,
        'line_count': len(lines),
        'font_size_ratio': float(np.median(heights)) / height,
        'stroke_ratio': (float(np.median(strokes)) / float(np.median(heights)))
                        if strokes and np.median(heights) > 0 else 0.0,
        'bottom_margin_ratio': (height - lowest_bottom) / float(height),
        'center_offset_ratio': (float(np.median(centers)) - width / 2.0) / float(width),
        'text_width_ratio': (total_right - total_left) / float(width),
        'line_spacing': float(np.median(spacing)) if spacing else 0.0,
    }
    return metrics


def analyze_reference_images(image_paths, limit=0, reporter=None):
    """批量统计参考素材，返回 {samples, metrics, medians} 形式的统计结果。"""
    _require_cv()
    samples = []
    failures = 0
    for position, path in enumerate(image_paths, start=1):
        if limit and position > limit:
            break
        metrics = measure_subtitle(read_image(path))
        if metrics:
            metrics['path'] = path
            samples.append(metrics)
        else:
            failures += 1
        if reporter and position % 50 == 0:
            reporter('  已分析 %d/%d 张（命中 %d 张）' % (position, len(image_paths), len(samples)))

    medians = {}
    if samples:
        for name in ('font_size_ratio', 'stroke_ratio', 'bottom_margin_ratio',
                     'center_offset_ratio', 'text_width_ratio', 'line_spacing'):
            values = [s[name] for s in samples if s.get(name)]
            medians[name] = float(np.median(values)) if values else 0.0
        # 行数用高分位数：偶尔出现的两行台词不能代表上限，但也不能忽略
        line_counts = [s['line_count'] for s in samples]
        medians['line_count_median'] = float(np.median(line_counts))
        medians['line_count_p90'] = float(np.percentile(line_counts, 90))

    return {
        'samples': len(samples),
        'skipped': failures,
        'medians': medians,
    }


def build_style_from_stats(stats, source):
    """把统计中位数折算成样式基线。"""
    medians = (stats or {}).get('medians') or {}
    style = SubtitleStyle(
        font_path=default_font_path(),
        font_size_ratio=medians.get('font_size_ratio') or config.FONT_SIZE_RATIO,
        stroke_ratio=medians.get('stroke_ratio') or config.STROKE_RATIO,
        # 参考素材测量的是「行顶到行顶 / 字墨高度」，而渲染时行距以字号（em）
        # 为基准，字墨高度约为字号的 0.85，因此这里折算回渲染口径
        line_spacing=((medians.get('line_spacing') or 0.0) * 0.85
                      or config.FONT_LINE_SPACING),
        bottom_margin_ratio=medians.get('bottom_margin_ratio') or config.BOTTOM_MARGIN_RATIO,
        # 参考素材里绝大多数是一行，但长句仍需折行，因此上限取「观测到的
        # 90 分位行数 + 1」并夹在 2~3 行之间
        max_lines=int(min(3, max(2, round((medians.get('line_count_p90') or 1) + 1)))),
        text_color=config.TEXT_COLOR,
        stroke_color=config.STROKE_COLOR,
        source=source,
    )
    return style.normalize()
