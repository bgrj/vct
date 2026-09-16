# -*- coding: utf-8 -*-
"""台词渲染与结果落盘

用 Pillow 在最优帧的画面底部绘制该句台词：白字 + 黑描边、无底板、水平居中、
末行贴底，长句自动折行。字号、描边、行距与贴底距离统一取自参考素材统计出的
样式基线（video_tool.style_profile），不在此处硬编码。
同时负责有序命名、索引 CSV 与处理报告的生成。
"""

import csv
import os
import re

from PIL import Image, ImageDraw, ImageFont

from . import config, style_profile

_font_cache = {}
_font_warning_shown = False


# ---------------------------------------------------------------------------
# 字体与文本
# ---------------------------------------------------------------------------
def load_font(size):
    """按候选顺序加载中文字体，全部缺失时回退 Pillow 默认字体。"""
    global _font_warning_shown
    if size in _font_cache:
        return _font_cache[size]

    font = None
    for candidate in config.FONT_CANDIDATES:
        if os.path.isfile(candidate):
            try:
                font = ImageFont.truetype(candidate, size)
                break
            except Exception:
                font = None

    if font is None:
        font = ImageFont.load_default()
        if not _font_warning_shown:
            print('警告：未找到可用中文字体，台词可能显示为方块，请检查系统字体。')
            _font_warning_shown = True

    _font_cache[size] = font
    return font


def wrap_text(text, font, max_width, draw):
    """按可用宽度把台词折行，保留原文中的换行。

    中文台词没有空格，因此按字符逐个测量宽度来折行。
    """
    lines = []
    for raw_line in str(text).split('\n'):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        current = ''
        for char in raw_line:
            trial = current + char
            if not current or draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = char
        if current:
            lines.append(current)
    return lines


def format_timecode(seconds):
    """把秒数格式化为适合文件名的 HH-MM-SS.mmm。"""
    seconds = max(0.0, float(seconds or 0.0))
    whole = int(seconds)
    millis = int(round((seconds - whole) * 1000))
    if millis >= 1000:
        millis -= 1000
        whole += 1
    hours, remainder = divmod(whole, 3600)
    minutes, secs = divmod(remainder, 60)
    return '%02d-%02d-%02d.%03d' % (hours, minutes, secs, millis)


_IMAGE_NAME_RE = re.compile(r'^(?P<episode>.+)_(?P<index>\d+)_(?P<time>\d{2}-\d{2}-\d{2}\.\d{3})$')


def build_image_name(episode_tag, index, timestamp):
    """生成有序图片名：<集号>_<四位句序>_<时间码>.jpg。

    句序在最前，保证按文件名排序即为台词顺序。
    """
    return '%s_%04d_%s.%s' % (
        episode_tag, int(index), format_timecode(timestamp), config.IMAGE_FORMAT
    )


def parse_image_timecode(name):
    """从图片文件名反解画面时间码（秒），无法解析时返回 None。

    用于断点续跑时把历史图片补回索引表：文件名本身就是时间码的来源。
    """
    match = _IMAGE_NAME_RE.match(os.path.splitext(name)[0])
    if not match:
        return None
    hours, minutes, seconds = match.group('time').split('-')
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def find_existing_image(output_dir, episode_tag, index):
    """断点续跑：查找该句是否已生成过图片。"""
    prefix = '%s_%04d_' % (episode_tag, int(index))
    try:
        for name in os.listdir(output_dir):
            if name.startswith(prefix) and name.lower().endswith(
                ('.jpg', '.jpeg', '.png')
            ):
                return os.path.join(output_dir, name)
    except OSError:
        return None
    return None


def parse_image_index(name):
    """从图片文件名反解句序（完整性校验用），无法解析时返回 None。"""
    match = _IMAGE_NAME_RE.match(os.path.splitext(name)[0])
    if not match:
        return None
    try:
        return int(match.group('index'))
    except (TypeError, ValueError):
        return None


def list_image_indexes(output_dir, episode_tag):
    """列出输出目录中属于该集的图片句序集合。

    完整性校验需要「目录里实际有哪些句」，逐句调用 find_existing_image 会把
    输出目录反复列上几百遍，因此这里一次性列目录并反解句序。
    """
    indexes = set()
    try:
        names = os.listdir(output_dir)
    except OSError:
        return indexes

    prefix = '%s_' % episode_tag
    for name in names:
        if not name.startswith(prefix):
            continue
        if os.path.splitext(name)[1].lower() not in ('.jpg', '.jpeg', '.png'):
            continue
        index = parse_image_index(name)
        if index is not None:
            indexes.add(index)
    return indexes


def count_index_rows(path):
    """统计索引表的数据行数（不含表头）；文件缺失时返回 0。"""
    if not os.path.isfile(path):
        return 0

    count = 0
    try:
        with open(path, 'r', newline='', encoding='utf-8-sig') as handle:
            reader = csv.reader(handle)
            next(reader, None)                    # 跳过表头
            for raw in reader:
                if raw and raw[0].strip():
                    count += 1
    except (OSError, csv.Error):
        return 0
    return count


# ---------------------------------------------------------------------------
# 绘制台词
# ---------------------------------------------------------------------------
def _fit_text(text, width, height, draw, style):
    """在样式基线的字号基础上挑选可容纳台词的字号（style 为样式基线）。"""
    band_height = max(28, int(height * config.BOTTOM_BAND_RATIO))
    margin_x = int(width * config.TEXT_MARGIN_RATIO)
    max_text_width = max(40, width - margin_x * 2)

    base_size = int(round(height * style.font_size_ratio))
    base_size = max(config.FONT_SIZE_MIN, min(config.FONT_SIZE_MAX, base_size))

    font = load_font(base_size)
    lines = wrap_text(text, font, max_text_width, draw)

    for size in range(base_size, config.FONT_SIZE_MIN - 1, -1):
        font = load_font(size)
        lines = wrap_text(text, font, max_text_width, draw)
        line_height = size * style.line_spacing
        needed = line_height * max(1, len(lines))
        if len(lines) <= style.max_lines and needed <= band_height:
            return font, lines, max_text_width

    # 实在放不下时只保留前几行并加省略号
    font = load_font(config.FONT_SIZE_MIN)
    lines = wrap_text(text, font, max_text_width, draw)
    if len(lines) > style.max_lines:
        lines = lines[:style.max_lines]
        lines[-1] = lines[-1][:-1] + '…' if len(lines[-1]) > 1 else lines[-1] + '…'
    return font, lines, max_text_width


def render_subtitle(src_image, dst_image, text, style=None):
    """以「白字 + 黑描边、水平居中、贴底」方式绘制台词并保存。

    返回 (是否成功渲染文字, 使用的字号)。
    """
    style = style or style_profile.load_style_profile()
    image = Image.open(src_image).convert('RGB')
    width, height = image.size

    draw = ImageDraw.Draw(image)

    font, lines, _ = _fit_text(text, width, height, draw, style)
    if not lines:
        image.save(dst_image, quality=config.JPEG_QUALITY)
        return False, 0

    font_size = getattr(font, 'size', config.FONT_SIZE_MIN)
    line_height = font_size * style.line_spacing
    stroke_width = max(2, int(round(font_size * style.stroke_ratio)))

    # 底部对齐，留一点下边距；色块高度不超过底部区域
    bottom_margin = max(2, int(round(height * style.bottom_margin_ratio)))
    text_top = height - bottom_margin - line_height * len(lines)
    for position, line in enumerate(lines):
        line_width = draw.textlength(line, font=font)
        draw.text(
            ((width - line_width) / 2.0, text_top + position * line_height),
            line,
            font=font,
            fill=config.TEXT_COLOR,
            stroke_width=stroke_width,
            stroke_fill=config.STROKE_COLOR,
        )

    image.save(dst_image, quality=config.JPEG_QUALITY)
    return True, font_size


# ---------------------------------------------------------------------------
# 索引与报告
# ---------------------------------------------------------------------------
INDEX_HEADER = [
    '句序', '起始时间码', '结束时间码', '时长(秒)', '台词',
    '文件名', '画面时间码', '综合得分', '清晰度', '运动惩罚',
    '人脸数', '眼睛状态', '嘴部自然', '说话状态', '构图分', '时序分', '镜头组',
    '画面类型', '是否降级',
]

_INDEX_IMAGE_COLUMN = INDEX_HEADER.index('文件名')


def _index_sort_key(value):
    """句序排序键：能转成整数的按数值排，否则按字符串排。"""
    try:
        return (0, int(str(value).strip()), '')
    except (TypeError, ValueError):
        return (1, 0, str(value))


def _read_index_rows(path):
    """读取已有索引表，丢弃图片已不存在的记录，返回 {句序: 原始行}。"""
    existing = {}
    if not os.path.isfile(path):
        return existing

    output_dir = os.path.dirname(path)
    try:
        with open(path, 'r', newline='', encoding='utf-8-sig') as handle:
            reader = csv.reader(handle)
            next(reader, None)                    # 跳过表头
            for raw in reader:
                if not raw or not raw[0].strip():
                    continue
                image_name = raw[_INDEX_IMAGE_COLUMN].strip() if len(raw) > _INDEX_IMAGE_COLUMN else ''
                # 图片被删除后不应继续留在索引里，否则索引会与目录内容不符
                if image_name and not os.path.isfile(os.path.join(output_dir, image_name)):
                    continue
                existing[raw[0].strip()] = raw
    except (OSError, csv.Error):
        return {}
    return existing


def _row_to_columns(row):
    """把一行处理结果转成索引表的列顺序。

    '眼睛状态' 用三态：睁开 / 半闭 / 闭合，无有效人脸时留空；旧索引里的
    「是 / 否」原样保留，重新跑一遍（`--clean`）就会换成三态文案。
    '是否降级' 用三态：True -> 是、False -> 否、缺失 -> 空（历史图片的降级
    状态无法追溯，留空而不是冒充「否」）。
    """
    degraded = row.get('degraded')
    return [
        row.get('index', ''),
        row.get('start_timecode', ''),
        row.get('end_timecode', ''),
        row.get('duration', ''),
        row.get('text', '').replace('\n', ' / '),
        row.get('image', ''),
        row.get('frame_timecode', ''),
        row.get('total', ''),
        row.get('sharpness', ''),
        row.get('motion_penalty', ''),
        row.get('face_count', ''),
        row.get('eye_state_text', row.get('eyes_open', '')),
        row.get('mouth_natural', ''),
        row.get('speaking', ''),
        row.get('composition', ''),
        row.get('timing', ''),
        row.get('shot', ''),
        row.get('frame_type', ''),
        '' if degraded is None else ('是' if degraded else '否'),
    ]


def _write_index_rows(merged, path):
    """按句序排序写出索引表（utf-8-sig，便于 Excel 直接打开）。"""
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(INDEX_HEADER)
        for key in sorted(merged, key=_index_sort_key):
            writer.writerow(merged[key])
    return path


def write_index_csv(rows, path):
    """写出台词索引表。

    采用「合并写入」：保留索引表中已有且图片仍存在的记录，再用手上的新结果覆盖
    同句序的记录。这样抽样试跑（只处理前几句）或断点续跑都不会把整集索引写残。
    """
    merged = _read_index_rows(path)
    for row in rows:
        merged[str(row.get('index', ''))] = _row_to_columns(row)
    return _write_index_rows(merged, path)


def fill_index_gaps(rows, path):
    """只补写索引表中缺失的记录，已存在的记录一律不动。

    断点续跑时被跳过的台词不会重新处理，也就没有评分数据；这里按图片文件名
    与字幕时间轴把它们补回索引，保证索引表覆盖整集所有图片，同时避免用空白
    字段覆盖先前的真实评分。
    """
    merged = _read_index_rows(path)
    for row in rows:
        key = str(row.get('index', ''))
        if key in merged:
            continue
        merged[key] = _row_to_columns(row)
    return _write_index_rows(merged, path)


def write_report(lines, path):
    """写出处理报告（纯文本，utf-8）。"""
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(str(line) for line in lines))
        handle.write('\n')
    return path
