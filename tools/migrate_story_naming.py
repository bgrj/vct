# -*- coding: utf-8 -*-
"""Migrate legacy X## story-gap naming to intercalated seq (e.g. 0004a).

Old: story frames used X01/X02/... (sorted after all dialogue rows).
New: seq = 4-digit prev-cue index + letter suffix so filename order intercalates.

Usage:
    python tools/migrate_story_naming.py --input "...\\your-show\\08"
    python tools/migrate_story_naming.py --input "...\\your-show\\08" --dry-run
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from typing import Dict, List, Optional, Tuple

THIS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(THIS_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from video_tool import config, renderer  # noqa: E402

# English keys preferred; Chinese keys accepted for pre-locale indexes
_COL = {
    'seq': ('seq', '句序'),
    'filename': ('filename', '文件名'),
    'start_tc': ('start_tc', '起始时间码'),
    'end_tc': ('end_tc', '结束时间码'),
}


def _cell(row: dict, key: str) -> str:
    for name in _COL[key]:
        if name in row and row[name] is not None:
            return str(row[name])
    return ''


def _set(row: dict, key: str, value: str) -> None:
    for name in _COL[key]:
        if name in row:
            row[name] = value
            return
    # Prefer English key when writing into a mixed/unknown dict
    row[_COL[key][0]] = value


def _old_story_index_re():
    return re.compile(r'^X\d+$', re.IGNORECASE)


def _load_index_rows(index_path: str) -> Tuple[List[str], List[dict]]:
    with open(index_path, 'r', newline='', encoding='utf-8-sig') as handle:
        reader = csv.DictReader(handle)
        header = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return header, rows


def _tc_to_seconds(tc: str) -> float:
    seconds = renderer.parse_timecode_to_seconds(tc) if hasattr(
        renderer, 'parse_timecode_to_seconds') else None
    if seconds is not None:
        return seconds
    if not tc:
        return 0.0
    try:
        normalized = tc.replace('-', ':')
        h_part, m_part, rest = normalized.split(':')
        return int(h_part) * 3600 + int(m_part) * 60 + float(rest)
    except (ValueError, AttributeError):
        return 0.0


def _build_sub_letter_map(per_gap_x_rows: List[dict]) -> Dict[str, str]:
    sorted_rows = sorted(
        per_gap_x_rows,
        key=lambda row: int(_cell(row, 'seq').lstrip('Xx')),
    )
    mapping: Dict[str, str] = {}
    for position, row in enumerate(sorted_rows):
        if position >= len(config.STORY_GAP_SUB_LETTERS):
            raise RuntimeError(
                'too many legacy story frames in one gap (>%d); start=%s end=%s'
                % (len(config.STORY_GAP_SUB_LETTERS),
                   _cell(row, 'start_tc'), _cell(row, 'end_tc'))
            )
        mapping[_cell(row, 'seq')] = config.STORY_GAP_SUB_LETTERS[position]
    return mapping


def _new_image_name_from_episode(episode_tag: str, old_name: str,
                                 new_seq: str) -> str:
    stem, ext = os.path.splitext(old_name)
    parts = stem.rsplit('_', 1)
    if len(parts) != 2:
        raise ValueError('cannot parse old filename: %s' % old_name)
    return '%s_%s_%s%s' % (episode_tag, new_seq, parts[1], ext)


def migrate(input_dir: str, dry_run: bool = False) -> dict:
    video_dir = os.path.abspath(input_dir)
    base = os.path.basename(video_dir.rstrip('\\/'))
    output_dir = os.path.join(video_dir, base + config.OUTPUT_SUFFIX)
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    if not os.path.isfile(index_path):
        raise FileNotFoundError('index not found: %s' % index_path)

    header, rows = _load_index_rows(index_path)
    has_seq = any(c in header for c in _COL['seq'])
    has_fn = any(c in header for c in _COL['filename'])
    has_st = any(c in header for c in _COL['start_tc'])
    has_et = any(c in header for c in _COL['end_tc'])
    if not (has_seq and has_fn and has_st and has_et):
        raise RuntimeError('index missing required columns (seq/filename/start_tc/end_tc)')

    old_x_re = _old_story_index_re()
    cue_rows: List[dict] = []
    old_x_rows: List[dict] = []
    for row in rows:
        idx = _cell(row, 'seq').strip()
        if old_x_re.match(idx):
            old_x_rows.append(row)
        else:
            cue_rows.append(row)
    if not old_x_rows:
        print('No legacy X## rows; nothing to migrate.')
        return {'renamed': 0, 'skipped': 0, 'dry_run': dry_run}

    cue_rows_by_end: List[Tuple[float, int]] = []
    for row in cue_rows:
        end_sec = _tc_to_seconds(_cell(row, 'end_tc'))
        try:
            idx_int = int(_cell(row, 'seq'))
        except ValueError:
            idx_int = 0
        cue_rows_by_end.append((end_sec, idx_int))
    cue_rows_by_end.sort()

    def find_prev_cue_index(gap_start_sec: float) -> Optional[int]:
        best = None
        for end_sec, idx_int in cue_rows_by_end:
            if end_sec <= gap_start_sec + 1e-3:
                if best is None or idx_int > best:
                    best = idx_int
            else:
                break
        return best

    gaps: Dict[str, List[dict]] = {}
    for row in old_x_rows:
        key = '%s~%s' % (_cell(row, 'start_tc'), _cell(row, 'end_tc'))
        gaps.setdefault(key, []).append(row)

    plan: List[dict] = []
    episode_tag: Optional[str] = None
    for key, group in gaps.items():
        sub_map = _build_sub_letter_map(group)
        for old_row in group:
            start_tc = _cell(old_row, 'start_tc')
            start_sec = _tc_to_seconds(start_tc)
            prev_idx = find_prev_cue_index(start_sec)
            sub = sub_map[_cell(old_row, 'seq')]
            new_seq = renderer_make_story_seq(prev_idx, sub)
            old_image = _cell(old_row, 'filename')
            if old_image:
                episode_tag = episode_tag or old_image.split('_', 1)[0]
                new_image = _new_image_name_from_episode(
                    episode_tag, old_image, new_seq
                )
            else:
                new_image = ''
            plan.append({
                'old_row': old_row,
                'old_index': _cell(old_row, 'seq'),
                'new_seq': new_seq,
                'old_image': old_image,
                'new_image': new_image,
                'prev_cue': prev_idx,
                'gap_key': key,
            })

    print('=' * 60)
    print('Plan: %d gaps, %d legacy X frames' % (len(gaps), len(plan)))
    print('-' * 60)
    show_count = min(15, len(plan))
    for item in plan[:show_count]:
        print('  %-6s -> %-6s  (prev #%s)  %s'
              % (item['old_index'], item['new_seq'],
                 item['prev_cue'] if item['prev_cue'] is not None else 'head',
                 item['new_image']))
    if len(plan) > show_count:
        print('  ... (%d more)' % (len(plan) - show_count))
    print('=' * 60)

    if dry_run:
        print('Dry-run: no files modified.')
        return {'renamed': 0, 'skipped': 0, 'planned': len(plan), 'dry_run': True}

    renamed = skipped = 0
    for item in plan:
        if not item['old_image']:
            _set(item['old_row'], 'seq', item['new_seq'])
            _set(item['old_row'], 'filename', '')
            skipped += 1
            continue
        old_path = os.path.join(output_dir, item['old_image'])
        new_path = os.path.join(output_dir, item['new_image'])
        if not os.path.isfile(old_path):
            _set(item['old_row'], 'seq', item['new_seq'])
            _set(item['old_row'], 'filename', '')
            skipped += 1
            continue
        if os.path.isfile(new_path):
            raise RuntimeError('target already exists: %s' % new_path)
        os.rename(old_path, new_path)
        _set(item['old_row'], 'seq', item['new_seq'])
        _set(item['old_row'], 'filename', item['new_image'])
        renamed += 1

    merged = {}
    for row in rows:
        merged[_cell(row, 'seq')] = [row.get(col, '') for col in header]
    sorted_keys = sorted(merged.keys(), key=renderer._index_sort_key)
    with open(index_path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for key in sorted_keys:
            writer.writerow(merged[key])

    print('Done: renamed %d, tombstone/ledger %d; index rewritten.'
          % (renamed, skipped))
    return {'renamed': renamed, 'skipped': skipped, 'dry_run': False}


def renderer_make_story_seq(prev_cue_index, sub_letter):
    from video_tool import story_gaps
    return story_gaps.make_story_seq(prev_cue_index, sub_letter)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Migrate legacy X## story frames to intercalated seq naming'
    )
    parser.add_argument('--input', required=True,
                        help='Episode folder (e.g. ...\\your-show\\08)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print plan only; do not modify files')
    args = parser.parse_args(argv)
    try:
        migrate(args.input, dry_run=args.dry_run)
    except Exception as exc:
        print('Migration failed: %s' % exc)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
