# -*- coding: utf-8 -*-
"""剧情延续帧质量审计：基于索引 + 基础图像指标，给每张 X 帧打「建议保留/复核/删除」

背景：第 08 集跑完后留下 149 张 X 帧。用户的实际需求里有一部分画面是过渡镜头
/ 黑场 / 同空窗内近重复 / 关键剧情不明显 的「补但不必留」。本工具把这些
候选筛出来，让人在 Excel 里一眼看完再决定。

判定规则（每一项独立打 flag，不互斥；最终 recommend 取最严格的）：
- 模糊    ：图像 Laplacian 方差 <  30   → flag=模糊
- 极暗    ：图像平均亮度    <   8   → flag=极暗
- 黑场卡  ：索引 frame_type 含 '平坦' → flag=黑场卡
- 无脸    ：face_count = 0            → flag=无脸（与无脸台词同口径）
- 镜头模糊：sharpness < 20            → flag=镜头模糊
- 重复    ：同空窗内其它 X 与该帧画面时间码间隔 < 1.5 秒 → flag=同空窗近重复
- 画面在空窗边缘：离 gap.start 或 gap.end < 0.6 秒 → flag=空窗边缘

最终 recommend：
- 命中 1 个 flag → 复核
- 命中 2 个 flag → 建议删除
- 无 flag       → 保留
- 单独列出：每个空窗首张（时间码最早）永远标记为「保留」（空窗代表帧）
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

import cv2
import numpy as np

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from video_tool import config, renderer  # noqa: E402


_AUDIT_HEADER = [
    '推荐', '句序', '文件名', '空窗起点', '空窗终点', '空窗时长',
    '画面时间码', '清晰度', '画面类型', '人脸数', '眼睛状态',
    '前接台词', '后接台词', '命中flag',
]


def _load_story_rows(index_path: str) -> list:
    """从 _index.csv 中提取所有剧情延续帧行（句序匹配 \\d{4}[a-z]+）。"""
    rows = []
    if not os.path.isfile(index_path):
        return rows
    with open(index_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            return rows
        idx = {col: position for position, col in enumerate(header)}
        for raw in reader:
            if not raw or not raw[0].strip():
                continue
            if not renderer._STORY_SEQ_RE.match(raw[0].strip()):
                continue
            try:
                sharp = float(raw[idx.get('清晰度', -1)] or '0')
            except ValueError:
                sharp = 0.0
            try:
                face_count = int(float(raw[idx.get('人脸数', -1)] or '0'))
            except ValueError:
                face_count = 0
            try:
                duration = float(raw[idx.get('时长(秒)', -1)] or '0')
            except ValueError:
                duration = 0.0
            try:
                start_tc = raw[idx.get('起始时间码', -1)] or ''
                end_tc = raw[idx.get('结束时间码', -1)] or ''
            except IndexError:
                start_tc = end_tc = ''
            text = (raw[idx.get('台词', -1)] or '').strip()
            rows.append({
                'seq': raw[0].strip(),
                'image': raw[idx.get('文件名', -1)] if '文件名' in idx else '',
                'start_tc': start_tc,
                'end_tc': end_tc,
                'duration': duration,
                'frame_tc': raw[idx.get('画面时间码', -1)] if '画面时间码' in idx else '',
                'sharpness': sharp,
                'frame_type': raw[idx.get('画面类型', -1)] if '画面类型' in idx else '',
                'face_count': face_count,
                'eye_state': raw[idx.get('眼睛状态', -1)] if '眼睛状态' in idx else '',
                'text': text,
                'sharpness_norm': raw[idx.get('综合得分', -1)] if '综合得分' in idx else '',
            })
    return rows


def _parse_tc_to_seconds(tc: str) -> float:
    """把时间码（HH:MM:SS.mmm 或 HH-MM-SS.mmm）解析为秒。"""
    if not tc:
        return 0.0
    try:
        normalized = tc.replace('-', ':')
        h_part, m_part, rest = normalized.split(':')
        return int(h_part) * 3600 + int(m_part) * 60 + float(rest)
    except (ValueError, AttributeError):
        return 0.0


def _image_brightness(path: str) -> float:
    """读图像平均亮度（0~255）。失败时返回 -1。"""
    try:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return -1.0
        return float(np.mean(img))
    except Exception:
        return -1.0


def _image_sharpness(path: str) -> float:
    """Laplacian 方差，值越大越清晰。失败时返回 -1。"""
    try:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return -1.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return -1.0


def _split_text(text: str) -> tuple:
    """把索引里的「前接第 N 句「...」后接第 M 句「...」」拆成两段。"""
    import re
    prev_m = re.search(r'前接第\s*(\d+)\s*句「([^」]*)」', text or '')
    next_m = re.search(r'后接第\s*(\d+)\s*句「([^」]*)」', text or '')
    return ((prev_m.group(2) if prev_m else '').strip(),
            ((next_m.group(1) + ': ' + next_m.group(2)) if next_m else '').strip())


def audit(output_dir: str, sharpness_threshold: float = 30.0,
          brightness_threshold: float = 8.0,
          near_dup_seconds: float = 1.5,
          edge_margin: float = 0.6) -> list:
    """对单集输出目录里所有剧情帧做审计，返回审计行列表（已按推荐/空窗排序）。"""
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    rows = _load_story_rows(index_path)
    if not rows:
        return []

    # 同空窗分组（start_tc~end_tc）
    gaps: dict = {}
    for row in rows:
        key = '%s~%s' % (row['start_tc'], row['end_tc'])
        gaps.setdefault(key, []).append(row)
    for key, group in gaps.items():
        group.sort(key=lambda r: r['seq'])  # seq 已天然按时间排

    audit_rows = []
    for key, group in gaps.items():
        # 找该空窗首张（时间码最早）：保留代表帧
        earliest = min(group, key=lambda r: _parse_tc_to_seconds(r['frame_tc']))
        for row in group:
            image_path = os.path.join(output_dir, row['image'])
            brightness = _image_brightness(image_path) if row['image'] else -1.0
            lap_sharp = _image_sharpness(image_path) if row['image'] else -1.0

            flags = []
            soft_flags = []
            if row['image'] and lap_sharp >= 0 and lap_sharp < sharpness_threshold:
                flags.append('模糊(Lap<%.0f)' % sharpness_threshold)
            if row['image'] and brightness >= 0 and brightness < brightness_threshold:
                flags.append('极暗(L<%.0f)' % brightness_threshold)
            if '平坦' in row['frame_type']:
                flags.append('黑场卡')
            # 软标记：剧情帧无脸常常是合法选择（物件特写 / 背影 / 空镜），
            # 仅作参考，不计入"建议删除"。
            if row['face_count'] == 0:
                soft_flags.append('无脸')
            if row['sharpness'] > 0 and row['sharpness'] < 20:
                soft_flags.append('镜头模糊')
            # 同空窗近重复：与同空窗内其它行画面时间码间隔 < near_dup_seconds
            this_t = _parse_tc_to_seconds(row['frame_tc'])
            for other in group:
                if other is row:
                    continue
                other_t = _parse_tc_to_seconds(other['frame_tc'])
                if abs(other_t - this_t) < near_dup_seconds and other is not earliest:
                    flags.append('同空窗近重复(%.1fs)' % abs(other_t - this_t))
                    break
            # 空窗边缘
            gap_start = _parse_tc_to_seconds(row['start_tc'])
            gap_end = _parse_tc_to_seconds(row['end_tc'])
            if gap_start and this_t - gap_start < edge_margin:
                soft_flags.append('贴近空窗起点')
            if gap_end and gap_end - this_t < edge_margin:
                soft_flags.append('贴近空窗终点')

            if row is earliest:
                soft_flags.insert(0, '空窗首张')  # 软标记，永远保留代表帧

            # 推荐：硬 flag 计数；软 flag 只在「无任何硬 flag」时降一级
            if '空窗首张' in soft_flags:
                recommend = '保留'
            elif len(flags) >= 2:
                recommend = '建议删除'
            elif len(flags) == 1:
                recommend = '复核'
            else:
                recommend = '保留'

            all_flags = flags + ['(软)' + s for s in soft_flags if s != '空窗首张']

            prev_text, next_text = _split_text(row['text'])
            audit_rows.append({
                '推荐': recommend,
                '句序': row['seq'],
                '文件名': row['image'],
                '空窗起点': row['start_tc'],
                '空窗终点': row['end_tc'],
                '空窗时长': row['duration'],
                '画面时间码': row['frame_tc'],
                '清晰度': row['sharpness'],
                '画面类型': row['frame_type'],
                '人脸数': row['face_count'],
                '眼睛状态': row['eye_state'],
                '前接台词': prev_text,
                '后接台词': next_text,
                '命中flag': ' / '.join(all_flags) if all_flags else '—',
            })

    # 排序：建议删除 → 复核 → 保留；同推荐内按空窗起点
    rank = {'建议删除': 0, '复核': 1, '保留': 2}
    audit_rows.sort(key=lambda r: (rank.get(r['推荐'], 9), r['空窗起点'], r['句序']))
    return audit_rows


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='剧情延续帧质量审计：输出每张 X 帧的客观指标与建议'
    )
    parser.add_argument('--input', required=True,
                        help='单集目录（…\\Love in the big City\\08）')
    parser.add_argument('--output', default=None,
                        help='审计 CSV 输出路径，默认写到 <input_basename>-pc/_audit.csv')
    parser.add_argument('--sharp-threshold', type=float, default=30.0,
                        help='Laplacian 方差低于该值判为模糊，默认 30')
    parser.add_argument('--bright-threshold', type=float, default=8.0,
                        help='平均亮度低于该值判为极暗，默认 8')
    args = parser.parse_args(argv)

    video_dir = os.path.abspath(args.input)
    base = os.path.basename(video_dir.rstrip('\\/'))
    output_dir = os.path.join(video_dir, base + config.OUTPUT_SUFFIX)
    audit_csv = args.output or os.path.join(output_dir, '_audit.csv')

    rows = audit(output_dir, sharpness_threshold=args.sharp_threshold,
                 brightness_threshold=args.bright_threshold)
    if not rows:
        print('未发现剧情延续帧行（_index.csv 句序需匹配 \\d{4}[a-z]+ 格式）。')
        return 1

    with open(audit_csv, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=_AUDIT_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    n_delete = sum(1 for r in rows if r['推荐'] == '建议删除')
    n_review = sum(1 for r in rows if r['推荐'] == '复核')
    n_keep = sum(1 for r in rows if r['推荐'] == '保留')
    print('审计结果（共 %d 张剧情帧）' % len(rows))
    print('  建议删除：%d' % n_delete)
    print('  复核    ：%d' % n_review)
    print('  保留    ：%d' % n_keep)
    print('已写入：%s' % audit_csv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
