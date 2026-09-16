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
# 剧情延续帧命名：<集号>_<前接句序号 4 位><子后缀 a/b/c...>_<时间码>.jpg
# 序号格式 \d{4}[a-z]+ 与台词帧的 \d{4} 共用同一排序空间，'a'..'z' 字符码大于
# '0'..'9'，故 0004 < 0004a < 0005，按文件名字母序天然按叙事顺序排列。
_STORY_IMAGE_NAME_RE = re.compile(
    r'^(?P<episode>.+)_(?P<seq>\d{4}[a-z]+)_(?P<time>\d{2}-\d{2}-\d{2}\.\d{3})$'
)
# 索引表「句序」列识别：与图片名中的 seq 段保持一致。00ab/0000/纯数字 都不算。
_STORY_SEQ_RE = re.compile(r'^\d{4}[a-z]+$')


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


def build_story_image_name(episode_tag, seq, timestamp):
    """生成剧情延续帧图片名：<集号>_<seq 4位+字母>_<时间码>.jpg。

    seq 形如 '0004a' / '0298b'：前 4 位是「前接台词序号」（无前接时为 0000），
    后缀字母是空窗内第几张（a / b / c...）。这种结构让按文件名字母序排列时
    剧情帧天然插在前一句台词图与后一句台词图之间。
    """
    return '%s_%s_%s.%s' % (
        episode_tag, str(seq), format_timecode(timestamp), config.IMAGE_FORMAT
    )


def parse_story_image_seq(name):
    """从剧情延续帧文件名反解 seq（如 '0004a'），无法解析时返回 None。"""
    match = _STORY_IMAGE_NAME_RE.match(os.path.splitext(name)[0])
    if not match:
        return None
    return match.group('seq')


def list_story_seqs(output_dir, episode_tag):
    """列出输出目录中剧情延续帧已占用的 seq 集合（如 {'0004a', '0004b'}）。

    断点续跑时据此识别已生成的剧情帧，避免重复补截；占位但图片已删除的 seq
    不会出现在这里——它们的台账在 _index.csv 中，仍由 capture_story_frames
    通过 read_story_index_rows 走 gap.key 维度判断。
    """
    seqs = set()
    try:
        names = os.listdir(output_dir)
    except OSError:
        return seqs

    prefix = '%s_' % episode_tag
    for name in names:
        if not name.startswith(prefix):
            continue
        if os.path.splitext(name)[1].lower() not in ('.jpg', '.jpeg', '.png'):
            continue
        seq = parse_story_image_seq(name)
        if seq is not None:
            seqs.add(seq)
    return seqs


def read_story_index_rows(path):
    """读取索引表中剧情延续帧的记录，返回 [{'seq','key','image'}, ...]。

    不校验图片是否还存在：这一行的职责是「该空窗已处理过」的台账——
    图片被人工删除时行保留（文件名已由 _read_index_rows 清空），重跑据此
    跳过，避免把人工淘汰过的空窗再补一遍造成重复劳动。识别条件是 seq 匹配
    `\\d{4}[a-z]+`（与图片名 _STORY_IMAGE_NAME_RE 保持一致）；旧 X## 格式
    的索引需要先经 tools/migrate_story_naming.py 迁移。
    """
    records = []
    if not os.path.isfile(path):
        return records

    try:
        with open(path, 'r', newline='', encoding='utf-8-sig') as handle:
            reader = csv.reader(handle)
            header = next(reader, None)
            if not header:
                return records
            try:
                start_col = header.index('起始时间码')
                end_col = header.index('结束时间码')
                image_col = header.index('文件名')
            except ValueError:
                return records
            for raw in reader:
                if not raw or not raw[0].strip():
                    continue
                index_text = raw[0].strip()
                if not _STORY_SEQ_RE.match(index_text):
                    continue
                start_tc = raw[start_col].strip() if len(raw) > start_col else ''
                end_tc = raw[end_col].strip() if len(raw) > end_col else ''
                image = raw[image_col].strip() if len(raw) > image_col else ''
                records.append({
                    'seq': index_text,
                    'key': '%s~%s' % (start_tc, end_tc),
                    'image': image,
                })
    except (OSError, csv.Error):
        return []
    return records


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
    """句序排序键：让剧情帧按「4 位数字 + 字母」精确插在对应台词之间。

    解析规则：
    - 纯整数（含 '1'、'0001'）：按数值升序
    - 剧情帧 '0004a'：前缀按数值排，相同前缀再按字母后缀升序
      → 0004 < 0004a < 0004b < ... < 0005
    - 片头占位 0000a/b/c 的数值 0 最小，自然落到最前
    - 其他异常值落到末尾桶，按字符串排序（不抛错）
    """
    text = str(value).strip()
    match = re.match(r'^(\d{4})([a-z]*)$', text)
    if match:
        prefix, suffix = int(match.group(1)), match.group(2)
        return (0, prefix, suffix)
    try:
        return (0, int(text), '')
    except (TypeError, ValueError):
        return (1, 0, text)


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
                # 图片被删除后不应继续留在索引里，否则索引会与目录内容不符。
                # 例外：剧情延续帧行保留（清空文件名）——它的起止时间码记录着
                # 「该空窗已处理」，重跑据此跳过，防止人工淘汰过的画面被重复
                # 补截；想重新补截时删除该行即可。识别条件是句序匹配剧情帧格式
                # \d{4}[a-z]+，与图片名 _STORY_IMAGE_NAME_RE 保持一致。
                if image_name and not os.path.isfile(os.path.join(output_dir, image_name)):
                    if _STORY_SEQ_RE.match(raw[0].strip()):
                        retained = list(raw)
                        retained[_INDEX_IMAGE_COLUMN] = ''
                        existing[raw[0].strip()] = retained
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
