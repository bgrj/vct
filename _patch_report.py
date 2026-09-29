# -*- coding: utf-8 -*-
from pathlib import Path

path = Path(r'd:\03_Knowledge\intimate\video-pics-cut\video_tool\pipeline.py')
text = path.read_text(encoding='utf-8')
start = text.index('def _write_episode_report(result, reporter):')
end = text.index('\n# ---------------------------------------------------------------------------\n# 整目录处理', start)
new_fn = '''def _write_episode_report(result, reporter):
    """Write per-episode report."""
    expected_images = result.success + result.skipped + result.refilled
    aligned = (expected_images >= result.total_cues
               and result.integrity_missing == 0
               and result.failed == 0)
    sampled = result.planned_cues < result.total_cues
    if sampled:
        verdict = ('SAMPLE RUN (%d/%d cues — confirm style then full run)'
                   % (result.planned_cues, result.total_cues))
    elif aligned and result.success == 0:
        verdict = 'NO NEW IMAGES (all existed; use --clean to rebuild)'
    elif aligned and result.eye_closed == 0:
        verdict = 'PASS (counts aligned, no closed-eye frames)'
    elif aligned and result.eye_closed:
        verdict = ('PASS with review (%d closed-eye frames remain; spot-check)'
                   % result.eye_closed)
    elif aligned:
        verdict = 'PASS (counts aligned)'
    else:
        verdict = 'NEEDS REVIEW (see failures / missing above)'

    lines = [
        'Dialogue capture - report',
        '=' * 46,
        'Video: %s' % os.path.basename(result.video),
        'Output: %s' % result.output_dir,
        'Subtitle track: %s' % result.subtitle_info,
        'Face backend: %s' % result.face_backend,
        'Style profile: %s' % (result.style_source or 'built-in defaults'),
        '',
        'Total cues: %d' % result.total_cues,
        'Processed this run: %d' % result.planned_cues,
        'Success: %d' % result.success,
        'Degraded: %d (no face / flat / low score — kept)' % result.degraded,
        'Skipped existing: %d (resume)' % result.skipped,
        'Failed: %d' % result.failed,
        'Elapsed: %.1f s' % result.elapsed,
        '',
        'Integrity:',
        '  Missing: %d' % result.integrity_missing,
        '  Auto-refilled: %d' % result.refilled,
        '',
        'Faces / eyes (new this run: %d):' % (result.eye_open + result.eye_half
                                              + result.eye_closed + result.eye_unknown),
        '  open: %d' % result.eye_open,
        '  half: %d' % result.eye_half,
        '  closed: %d' % result.eye_closed,
        '  no face: %d' % result.eye_unknown,
        '',
        'Episode check:',
        '  Counts: cues %d / expected %d (ok %d + skip %d + refill %d) / failed %d'
        % (result.total_cues, expected_images, result.success, result.skipped,
           result.refilled, result.failed),
        '  Verdict: %s' % verdict,
        '  Note: %d degraded frames are normal.' % result.degraded,
        '  Spot-check open-eye and no-face images; use --eye-refine half if needed.',
    ]

    if result.eye_refine_tried:
        anchor = next(
            (position for position, text in enumerate(lines) if text.startswith('  no face')),
            None,
        )
        refine_line = ('  eye refine: %d improved of %d triggered'
                       % (result.eye_refined, result.eye_refine_tried))
        if anchor is None:
            lines.append(refine_line)
        else:
            lines.insert(anchor + 1, refine_line)

    if result.story_enabled:
        story_lines = [
            '',
            'Story-gap frames (human review required):',
            '  Gaps: %d (interval >= %.1f s)'
            % (result.story_gaps_total, result.story_gap_min),
            '  Captured this run: %d' % result.story_frames,
            '  Skipped gaps: %d' % result.story_skipped,
        ]
        if result.story_notes:
            story_lines.append('  Gap details:')
            story_lines.extend(
                '    - %s ~ %s (%.1f s): %s'
                % (subtitles.format_timecode(gap.start),
                   subtitles.format_timecode(gap.end),
                   gap.duration, note)
                for gap, note in result.story_notes
            )
        story_lines.extend([
            '  Review tip: browse 0004a/b by name; empty filename = ledger.',
        ])
        anchor = next(
            (position for position, text in enumerate(lines)
             if text.startswith('Faces / eyes')),
            None,
        )
        if anchor is None:
            lines.extend(story_lines)
        else:
            lines[anchor:anchor] = story_lines

    if result.cleaned_images:
        lines.insert(8, 'Clean rebuild removed: %d old images' % result.cleaned_images)

    if result.errors:
        lines.append('')
        lines.append('Failures:')
        lines.extend('  - %s' % item for item in result.errors[:50])
        if len(result.errors) > 50:
            lines.append('  ... (%d total, truncated)' % len(result.errors))

    lines.append('')
    lines.append('Tip: re-run skips existing images (resume).')

    path = os.path.join(result.output_dir, config.REPORT_NAME)
    renderer.write_report(lines, path)
    reporter.status('Report written: %s' % path


'''
path.write_text(text[:start] + new_fn + text[end:], encoding='utf-8')
text2 = path.read_text(encoding='utf-8')
old = "lines.append('  眼睛状态：睁开 %d 张／半闭 %d 张／闭合 %d 张／无人像 %d 张'"
new = "lines.append('  eyes: open %d / half %d / closed %d / no-face %d'"
if old in text2:
    path.write_text(text2.replace(old, new), encoding='utf-8')
    print('summary patched')
else:
    print('summary line not found (ok if already english)')
print('done')
