# -*- coding: utf-8 -*-
"""Story-gap quality audit: score each story frame for delete / review / keep.

Hard flags (count toward recommendation):
- blur: Laplacian variance < threshold
- dark: mean brightness < threshold
- flat: frame_type contains 'flat' (or legacy Chinese 平坦)
- near-dup: same gap, frame_tc within 1.5s of another frame

Soft flags are informational only.
First frame of each gap is always keep.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

import cv2
import numpy as np

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from video_tool import config, renderer  # noqa: E402

_AUDIT_HEADER = list(config.AUDIT_COLUMNS)


def _col(idx, english, chinese, default=''):
    if english in idx:
        return idx[english]
    if chinese in idx:
        return idx[chinese]
    return default


def _load_story_rows(index_path: str) -> list:
    rows = []
    if not os.path.isfile(index_path):
        return rows
    with open(index_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.reader(handle)
        header = next(reader, None)
        if not header:
            return rows
        idx = {col: position for position, col in enumerate(header)}

        def get(raw, en, zh, default=''):
            key = en if en in idx else zh
            if key not in idx:
                return default
            pos = idx[key]
            return raw[pos] if len(raw) > pos else default

        for raw in reader:
            if not raw or not raw[0].strip():
                continue
            if not renderer._STORY_SEQ_RE.match(raw[0].strip()):
                continue
            try:
                sharp = float(get(raw, 'sharpness', '清晰度', '0') or '0')
            except ValueError:
                sharp = 0.0
            try:
                face_count = int(float(get(raw, 'face_count', '人脸数', '0') or '0'))
            except ValueError:
                face_count = 0
            try:
                duration = float(get(raw, 'duration_sec', '时长(秒)', '0') or '0')
            except ValueError:
                duration = 0.0
            rows.append({
                'seq': raw[0].strip(),
                'image': get(raw, 'filename', '文件名'),
                'start_tc': get(raw, 'start_tc', '起始时间码'),
                'end_tc': get(raw, 'end_tc', '结束时间码'),
                'duration': duration,
                'frame_tc': get(raw, 'frame_tc', '画面时间码'),
                'sharpness': sharp,
                'frame_type': get(raw, 'frame_type', '画面类型'),
                'face_count': face_count,
                'eye_state': get(raw, 'eye_state', '眼睛状态'),
                'text': get(raw, 'dialogue', '台词').strip(),
            })
    return rows


def _parse_tc_to_seconds(tc: str) -> float:
    if not tc:
        return 0.0
    try:
        normalized = tc.replace('-', ':')
        h_part, m_part, rest = normalized.split(':')
        return int(h_part) * 3600 + int(m_part) * 60 + float(rest)
    except (ValueError, AttributeError):
        return 0.0


def _image_brightness(path: str) -> float:
    try:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return -1.0
        return float(np.mean(img))
    except Exception:
        return -1.0


def _image_sharpness(path: str) -> float:
    try:
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            return -1.0
        return float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return -1.0


def _split_text(text: str) -> tuple:
    """Parse prev/next cue snippets from story-row dialogue text (EN or ZH)."""
    prev_m = re.search(r'(?:prev|前接第)\s*(\d+)\s*(?:cue|句)[「"\']?([^」"\']*)', text or '')
    next_m = re.search(r'(?:next|后接第)\s*(\d+)\s*(?:cue|句)[「"\']?([^」"\']*)', text or '')
    return (
        (prev_m.group(2) if prev_m else '').strip(),
        ((next_m.group(1) + ': ' + next_m.group(2)) if next_m else '').strip(),
    )


def audit(output_dir: str, sharpness_threshold: float = 30.0,
          brightness_threshold: float = 8.0,
          near_dup_seconds: float = 1.5,
          edge_margin: float = 0.6) -> list:
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    rows = _load_story_rows(index_path)
    if not rows:
        return []

    gaps = {}
    for row in rows:
        key = '%s~%s' % (row['start_tc'], row['end_tc'])
        gaps.setdefault(key, []).append(row)
    for group in gaps.values():
        group.sort(key=lambda r: r['seq'])

    audit_rows = []
    for key, group in gaps.items():
        earliest = min(group, key=lambda r: _parse_tc_to_seconds(r['frame_tc']))
        for row in group:
            image_path = os.path.join(output_dir, row['image'])
            brightness = _image_brightness(image_path) if row['image'] else -1.0
            lap_sharp = _image_sharpness(image_path) if row['image'] else -1.0

            flags = []
            soft_flags = []
            if row['image'] and lap_sharp >= 0 and lap_sharp < sharpness_threshold:
                flags.append('blur(Lap<%.0f)' % sharpness_threshold)
            if row['image'] and brightness >= 0 and brightness < brightness_threshold:
                flags.append('dark(L<%.0f)' % brightness_threshold)
            ft = row['frame_type'] or ''
            if 'flat' in ft.lower() or '平坦' in ft:
                flags.append('flat_card')
            if row['face_count'] == 0:
                soft_flags.append('no_face')
            if row['sharpness'] > 0 and row['sharpness'] < 20:
                soft_flags.append('soft_blur')

            this_t = _parse_tc_to_seconds(row['frame_tc'])
            for other in group:
                if other is row:
                    continue
                other_t = _parse_tc_to_seconds(other['frame_tc'])
                if abs(other_t - this_t) < near_dup_seconds and other is not earliest:
                    flags.append('near_dup(%.1fs)' % abs(other_t - this_t))
                    break

            gap_start = _parse_tc_to_seconds(row['start_tc'])
            gap_end = _parse_tc_to_seconds(row['end_tc'])
            if gap_start and this_t - gap_start < edge_margin:
                soft_flags.append('near_gap_start')
            if gap_end and gap_end - this_t < edge_margin:
                soft_flags.append('near_gap_end')

            is_first = row is earliest
            if is_first:
                soft_flags.insert(0, 'gap_first')

            if is_first:
                recommend = config.RECOMMEND_KEEP
            elif len(flags) >= 2:
                recommend = config.RECOMMEND_DELETE
            elif len(flags) == 1:
                recommend = config.RECOMMEND_REVIEW
            else:
                recommend = config.RECOMMEND_KEEP

            all_flags = flags + ['(soft)' + s for s in soft_flags if s != 'gap_first']
            prev_text, next_text = _split_text(row['text'])
            audit_rows.append({
                'recommendation': recommend,
                'seq': row['seq'],
                'filename': row['image'],
                'gap_start': row['start_tc'],
                'gap_end': row['end_tc'],
                'gap_duration': row['duration'],
                'frame_tc': row['frame_tc'],
                'sharpness': row['sharpness'],
                'frame_type': row['frame_type'],
                'face_count': row['face_count'],
                'eye_state': row['eye_state'],
                'prev_dialogue': prev_text,
                'next_dialogue': next_text,
                'flags': ' / '.join(all_flags) if all_flags else '-',
            })

    rank = {
        config.RECOMMEND_DELETE: 0,
        config.RECOMMEND_REVIEW: 1,
        config.RECOMMEND_KEEP: 2,
    }
    audit_rows.sort(
        key=lambda r: (rank.get(r['recommendation'], 9), r['gap_start'], r['seq'])
    )
    return audit_rows


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Audit story-gap frames; write objective metrics + recommendation'
    )
    parser.add_argument('--input', required=True,
                        help='Episode folder (e.g. ...\\your-show\\08)')
    parser.add_argument('--output', default=None,
                        help='Audit CSV path (default: <basename>-pics/%s)'
                             % config.AUDIT_CSV_NAME)
    parser.add_argument('--sharp-threshold', type=float, default=30.0)
    parser.add_argument('--bright-threshold', type=float, default=8.0)
    args = parser.parse_args(argv)

    video_dir = os.path.abspath(args.input)
    base = os.path.basename(video_dir.rstrip('\\/'))
    output_dir = os.path.join(video_dir, base + config.OUTPUT_SUFFIX)
    audit_csv = args.output or os.path.join(output_dir, config.AUDIT_CSV_NAME)

    rows = audit(output_dir, sharpness_threshold=args.sharp_threshold,
                 brightness_threshold=args.bright_threshold)
    if not rows:
        print('No story-gap rows found (seq must match \\d{4}[a-z]+).')
        return 1

    with open(audit_csv, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=_AUDIT_HEADER)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    n_delete = sum(1 for r in rows if r['recommendation'] == config.RECOMMEND_DELETE)
    n_review = sum(1 for r in rows if r['recommendation'] == config.RECOMMEND_REVIEW)
    n_keep = sum(1 for r in rows if r['recommendation'] == config.RECOMMEND_KEEP)
    print('Audit: %d story frames' % len(rows))
    print('  delete: %d' % n_delete)
    print('  review: %d' % n_review)
    print('  keep:   %d' % n_keep)
    print('Wrote: %s' % audit_csv)
    return 0


if __name__ == '__main__':
    sys.exit(main())
