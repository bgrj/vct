# -*- coding: utf-8 -*-
"""Migrate Chinese _index.csv / _audit.csv headers and enums to English.

Usage:
    python tools/migrate_csv_locale.py --input ..\\love-in-the-big-city\\01
    python tools/migrate_csv_locale.py --input ..\\love-in-the-big-city --all
    python tools/migrate_csv_locale.py --input ..\\love-in-the-big-city\\01 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import os
import sys

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from video_tool import config  # noqa: E402

INDEX_HEADER_MAP = {
    '句序': 'seq',
    '起始时间码': 'start_tc',
    '结束时间码': 'end_tc',
    '时长(秒)': 'duration_sec',
    '台词': 'dialogue',
    '文件名': 'filename',
    '画面时间码': 'frame_tc',
    '综合得分': 'score',
    '清晰度': 'sharpness',
    '运动惩罚': 'motion_penalty',
    '人脸数': 'face_count',
    '眼睛状态': 'eye_state',
    '嘴部自然': 'mouth_natural',
    '说话状态': 'speaking_state',
    '构图分': 'composition_score',
    '时序分': 'timing_score',
    '镜头组': 'shot_group',
    '画面类型': 'frame_type',
    '是否降级': 'degraded',
}

AUDIT_HEADER_MAP = {
    '推荐': 'recommendation',
    '句序': 'seq',
    '文件名': 'filename',
    '空窗起点': 'gap_start',
    '空窗终点': 'gap_end',
    '空窗时长': 'gap_duration',
    '画面时间码': 'frame_tc',
    '清晰度': 'sharpness',
    '画面类型': 'frame_type',
    '人脸数': 'face_count',
    '眼睛状态': 'eye_state',
    '前接台词': 'prev_dialogue',
    '后接台词': 'next_dialogue',
    '命中flag': 'flags',
}

CELL_MAP = {
    '睁开': config.EYE_OPEN,
    '半闭': config.EYE_HALF,
    '闭合': config.EYE_CLOSED,
    '正在说话': config.SPEAKING,
    '闭嘴': config.SPEAKING_CLOSED,
    '张口过大': config.SPEAKING_TOO_OPEN,
    '是': config.YES,
    '否': config.NO,
    '建议删除': config.RECOMMEND_DELETE,
    '复核': config.RECOMMEND_REVIEW,
    '保留': config.RECOMMEND_KEEP,
    '平坦画面': 'flat',
    '正常画面': 'normal',
}


def _map_header(header, mapping):
    return [mapping.get(col, col) for col in header]


def _map_cell(value):
    text = (value or '').strip()
    if text in CELL_MAP:
        return CELL_MAP[text]
    # frame_type may contain 平坦
    if '平坦' in text and text not in CELL_MAP:
        return text.replace('平坦画面', 'flat').replace('平坦', 'flat')
    return value


def migrate_csv(path, header_map, dry_run=False):
    if not os.path.isfile(path):
        return False, 'missing'
    with open(path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            return False, 'empty'
        rows = list(reader)

    already = all(col in config.INDEX_COLUMNS or col in config.AUDIT_COLUMNS
                  or col not in header_map for col in header)
    # If no Chinese headers present, still map cell values
    new_header = _map_header(header, header_map)
    new_rows = [[_map_cell(cell) for cell in row] for row in rows]

    changed = new_header != header or new_rows != rows
    if not changed:
        return False, 'already_english'
    if dry_run:
        return True, 'would_write'
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(new_header)
        writer.writerows(new_rows)
    return True, 'written'


def episode_output_dir(episode_dir):
    base = os.path.basename(episode_dir.rstrip('\\/'))
    return os.path.join(episode_dir, base + config.OUTPUT_SUFFIX)


def migrate_episode(episode_dir, dry_run=False):
    out = episode_output_dir(episode_dir)
    results = []
    for name, mapping in (
        (config.INDEX_CSV_NAME, INDEX_HEADER_MAP),
        (config.AUDIT_CSV_NAME, AUDIT_HEADER_MAP),
    ):
        path = os.path.join(out, name)
        ok, status = migrate_csv(path, mapping, dry_run=dry_run)
        results.append((path, ok, status))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Migrate Chinese index/audit CSV locale to English'
    )
    parser.add_argument('--input', required=True,
                        help='Episode folder, or show root with --all')
    parser.add_argument('--all', action='store_true',
                        help='Treat --input as show root; migrate every NN/NN-pics')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)

    root = os.path.abspath(args.input)
    episodes = []
    if args.all:
        for name in sorted(os.listdir(root)):
            ep = os.path.join(root, name)
            if not os.path.isdir(ep):
                continue
            out = episode_output_dir(ep)
            if os.path.isdir(out):
                episodes.append(ep)
    else:
        episodes = [root]

    if not episodes:
        print('No episode output dirs found under %s' % root)
        return 1

    for ep in episodes:
        print('Episode: %s' % ep)
        for path, ok, status in migrate_episode(ep, dry_run=args.dry_run):
            mark = 'OK' if ok else '--'
            print('  [%s] %s (%s)' % (mark, path, status))
    return 0


if __name__ == '__main__':
    sys.exit(main())
