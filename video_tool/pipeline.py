# -*- coding: utf-8 -*-
"""Dialogue capture pipeline.

Scan -> derive <episode>-pics -> subtitles -> sample/score/render ->
index + report. Resume-friendly; one episode at a time.
"""

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field, replace as dataclass_replace

from . import config, ffmpeg_utils, quality, renderer, story_gaps, style_profile, subtitles
from .config import VideoToolError

_EPISODE_RE = re.compile(r'[Ss](\d{1,2})[Ee](\d{1,3})')

# Map quality enums to index CSV cell values (English)
_SPEAKING_TEXT = {
    'speaking': config.SPEAKING,
    'idle': config.SPEAKING_CLOSED,
    'open': config.SPEAKING_TOO_OPEN,
}

_EYE_TEXT = {
    'open': config.EYE_OPEN,
    'half': config.EYE_HALF,
    'closed': config.EYE_CLOSED,
}

_EYE_TEXT_STATE = {text: state for state, text in _EYE_TEXT.items()}
_EYE_TEXT_STATE.update({'睁开': 'open', '半闭': 'half', '闭合': 'closed'})


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
class Reporter:
    """Default progress/status callbacks; GUI may supply a custom subclass."""

    def status(self, text):
        """Report one status line."""
        print(text)

    def progress(self, value):
        """Report progress in 0..100."""

    def cancelled(self):
        """Whether the user cancelled."""
        return False


class SilentReporter(Reporter):
    """Silent callbacks for automated tests."""

    def status(self, text):
        pass


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------
@dataclass
class DialogueOptions:
    """Tunable options for dialogue capture mode."""

    subtitle_track: str = 'auto'      # 'auto' or subtitle-track name keyword
    candidate_min: int = config.CANDIDATE_MIN
    candidate_max: int = config.CANDIDATE_MAX
    candidate_fps: float = config.CANDIDATE_FPS
    burn_subtitle: bool = True        # burn dialogue text onto the image bottom
    start_cue: int = 1                # first cue index (for sample runs)
    max_cues: int = 0                 # max cues to process; 0 = all
    resume: bool = True               # skip cues that already have an image
    clean: bool = False               # rebuild: clear prior images + index first
    eye_refine: str = 'closed'        # eye-refine mode: closed / half / off
    story_gaps: bool = True           # capture story-gap frames between cues
    story_gap_min: float = config.STORY_GAP_MIN          # min gap length (seconds)
    story_gap_max_per: int = config.STORY_GAP_MAX_PER_GAP    # max frames per gap
    write_index: bool = True
    write_report: bool = True


@dataclass
class EpisodeResult:
    """Per-episode processing result."""

    video: str = ''
    output_dir: str = ''
    subtitle_info: str = ''
    total_cues: int = 0
    planned_cues: int = 0
    success: int = 0
    degraded: int = 0
    skipped: int = 0
    failed: int = 0
    elapsed: float = 0.0
    rows: list = field(default_factory=list)
    backfill_rows: list = field(default_factory=list)   # skipped (resume) rows for index fill
    errors: list = field(default_factory=list)
    face_backend: str = ''
    style_source: str = ''            # style baseline source (reference stats)
    cleaned_images: int = 0           # old images removed on clean rebuild
    integrity_missing: int = 0        # cues missing images after integrity check
    refilled: int = 0                 # cues recovered by post-check refill
    eye_open: int = 0                 # frames with open eyes (good)
    eye_half: int = 0                 # frames with half-closed eyes
    eye_closed: int = 0               # frames with closed eyes (fallback)
    eye_unknown: int = 0              # no usable face; eye state unknown
    eye_refined: int = 0              # cues improved by eye-refine resample
    eye_refine_tried: int = 0         # cues that triggered eye refine
    story_enabled: bool = False       # whether story-gap capture ran
    story_gap_min: float = 0.0        # gap threshold used (seconds)
    story_gaps_total: int = 0         # enumerated no-dialogue gaps
    story_frames: int = 0             # story-gap frames captured this run
    story_skipped: int = 0            # gaps skipped (already done / rejected)
    story_rows: list = field(default_factory=list)        # story-gap index rows
    story_notes: list = field(default_factory=list)       # [(StoryGap, note), ...]


@dataclass
class RunResult:
    """Full batch-run result."""

    input_dir: str = ''
    episodes: list = field(default_factory=list)
    incomplete_files: list = field(default_factory=list)
    unreadable_files: list = field(default_factory=list)

    @property
    def success(self):
        return sum(item.success for item in self.episodes)

    @property
    def degraded(self):
        return sum(item.degraded for item in self.episodes)

    @property
    def failed(self):
        return sum(item.failed for item in self.episodes)

    @property
    def skipped(self):
        return sum(item.skipped for item in self.episodes)


# ---------------------------------------------------------------------------
# Paths and naming
# ---------------------------------------------------------------------------
def episode_tag(video_path):
    """Extract episode tag from filename, e.g. Love...S01E03....mkv -> S01E03."""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    match = _EPISODE_RE.search(stem)
    if match:
        return 'S%02dE%02d' % (int(match.group(1)), int(match.group(2)))
    return stem or 'video'


def clean_output(output_dir, episode=None):
    """Remove this tool's prior images and index from output_dir (clean rebuild).

    Only deletes our own artifacts; leaves other user files alone. Returns
    the number of images removed.
    """
    if not os.path.isdir(output_dir):
        return 0
    removed = 0
    suffix = '.' + config.IMAGE_FORMAT.lower().lstrip('.')
    for name in sorted(os.listdir(output_dir)):
        path = os.path.join(output_dir, name)
        if not os.path.isfile(path):
            continue
        is_image = name.lower().endswith(suffix)
        is_meta = name in (config.INDEX_CSV_NAME, config.REPORT_NAME)
        if not (is_image or is_meta):
            continue
        if episode and is_image and not name.startswith(episode):
            continue
        try:
            os.remove(path)
            if is_image:
                removed += 1
        except OSError:
            continue
    return removed


def derive_output_dir(video_dir):
    """Derive output dir = episode folder basename + -pics.

    Example: ...\\your-show\\03 -> ...\\your-show\\03\\03-pics
    """
    video_dir = os.path.abspath(video_dir)
    return os.path.join(video_dir, os.path.basename(video_dir) + config.OUTPUT_SUFFIX)


# ---------------------------------------------------------------------------
# Directory scan
# ---------------------------------------------------------------------------
def scan_directory(input_dir, max_depth=10, reporter=None):
    """Recursively scan input_dir.

    Returns (video paths, incomplete files, unreadable files).
    """
    reporter = reporter or SilentReporter()
    videos, incomplete, unreadable = [], [], []
    base_depth = input_dir.rstrip('\\/').count(os.sep)

    for current_dir, dir_names, file_names in os.walk(input_dir):
        depth = current_dir.rstrip('\\/').count(os.sep) - base_depth
        if depth >= max_depth:
            dir_names[:] = []
            continue

        # Skip our own -pics output dirs so prior artifacts are not re-scanned
        dir_names[:] = [
            name for name in dir_names
            if not name.endswith(config.OUTPUT_SUFFIX)
        ]

        for file_name in file_names:
            path = os.path.join(current_dir, file_name)
            if os.path.islink(path):
                continue

            if ffmpeg_utils.is_incomplete_file(path):
                incomplete.append(path)
                reporter.status('skip incomplete download: %s' % file_name)
                continue

            extension = os.path.splitext(file_name)[1].lower()
            if extension not in config.VIDEO_EXTENSIONS:
                continue

            if ffmpeg_utils.is_video_file(path):
                videos.append(path)
            else:
                unreadable.append(path)

    videos.sort()
    incomplete.sort()
    unreadable.sort()
    return videos, incomplete, unreadable


# ---------------------------------------------------------------------------
# Per-episode processing
# ---------------------------------------------------------------------------
def _sample_candidates(video_path, cue, options, work_dir):
    """Sample candidate frames in the cue interval; return [(path, seconds), ...]."""
    duration = max(cue.duration, 0.12)
    desired = int(round(duration * options.candidate_fps))
    desired = max(options.candidate_min, min(options.candidate_max, desired))
    fps = desired / duration

    # Clear prior cue candidates so scoring is not polluted
    for name in os.listdir(work_dir):
        if name.startswith('cand_'):
            try:
                os.remove(os.path.join(work_dir, name))
            except OSError:
                pass

    pattern = os.path.join(work_dir, 'cand_%03d.jpg')
    files = ffmpeg_utils.sample_frames(video_path, cue.start, duration, fps, pattern)

    candidates = []
    for position, path in enumerate(files):
        timestamp = cue.start + position / fps
        timestamp = min(max(timestamp, cue.start), cue.end)
        candidates.append((path, timestamp))
    return candidates


def _backfill_row(image_path, cue):
    """Index row for a skipped (resume) cue: known fields only, no fake scores."""
    name = os.path.basename(image_path)
    stamp = renderer.parse_image_timecode(name)
    return {
        'index': cue.index,
        'start_timecode': subtitles.format_timecode(cue.start),
        'end_timecode': subtitles.format_timecode(cue.end),
        'duration': round(cue.duration, 3),
        'text': cue.text,
        'image': name,
        'frame_timecode': '' if stamp is None else renderer.format_timecode(stamp),
        'total': '',
        'sharpness': '',
        'motion_penalty': '',
        'face_count': '',
        'eyes_open': '',
        'eye_state_text': '',
        'mouth_natural': '',
        'speaking': '',
        'composition': '',
        'timing': '',
        'shot': '',
        'frame_type': '',
    }


def _format_eta(elapsed, done, total):
    """Estimate remaining time from elapsed progress."""
    if done <= 0 or total <= 0:
        return ''
    remaining = elapsed / done * (total - done)
    if remaining < 60:
        return '~%d sec' % int(remaining)
    return '~%.1f min' % (remaining / 60.0)


def _count_eye_state(result, best):
    """Accumulate eye-state counts for the report summary.

    Eyes are easier to spot-check than mouth shape, so the report tallies
    open / half / closed / no-face separately.
    """
    _add_eye_state(result, best.eye_state, 1)


def _add_eye_state(result, state, delta):
    """Adjust eye-state counters; use negative delta when replacing a frame."""
    state = (state or '')
    if state == 'open':
        result.eye_open += delta
    elif state == 'half':
        result.eye_half += delta
    elif state == 'closed':
        result.eye_closed += delta
    else:
        result.eye_unknown += delta


def _index_row(cue, best, image_name):
    """Build one index row aligned with renderer.INDEX_HEADER."""
    detail = best.detail or {}
    return {
        'index': cue.index,
        'start_timecode': subtitles.format_timecode(cue.start),
        'end_timecode': subtitles.format_timecode(cue.end),
        'duration': round(cue.duration, 3),
        'text': cue.text,
        'image': image_name,
        'frame_timecode': renderer.format_timecode(best.timestamp),
        'total': best.total,
        'sharpness': round(best.sharpness, 1),
        'motion_penalty': best.motion_penalty,
        'face_count': best.face_count,
        'eyes_open': ('' if 'eyes_closed' not in detail
                      else (config.NO if detail['eyes_closed'] else config.YES)),
        'eye_state_text': _EYE_TEXT.get(detail.get('eye_state', ''), ''),
        'mouth_natural': ('' if 'mouth_too_open' not in detail
                          else (config.NO if detail['mouth_too_open'] else config.YES)),
        'speaking': _SPEAKING_TEXT.get(detail.get('speaking', ''), ''),
        'composition': best.composition_score,
        'timing': best.timing_score,
        'shot': best.shot_id,
        'frame_type': 'flat' if best.flat else 'normal',
        'degraded': best.degraded,
    }


def _process_cue(video_path, cue, options, analyzer, work_dir, output_dir, episode):
    """Process one cue: sample -> score -> render.

    Returns (index row, best frame); raises VideoToolError on failure.
    Shared by the main loop and integrity refill so both paths stay identical.
    """
    candidates = _sample_candidates(video_path, cue, options, work_dir)
    if not candidates:
        raise VideoToolError('failed to extract candidate frames')

    scores = quality.score_candidates(candidates, analyzer)
    best = quality.select_best(scores)
    if best is None:
        raise VideoToolError('all candidate frames failed to load')

    image_name = renderer.build_image_name(episode, cue.index, best.timestamp)
    image_path = os.path.join(output_dir, image_name)
    if options.burn_subtitle:
        renderer.render_subtitle(best.path, image_path, cue.text)
    else:
        shutil.copyfile(best.path, image_path)

    return _index_row(cue, best, image_name), best


def _integrity_state(output_dir, episode, selected, options):
    """Integrity check triad: expected indexes, present indexes, missing cues, index rows.

    Cue count, on-disk images, and index rows can diverge after resume, per-cue
    failures, or leftover files; the wrap-up uses this to decide on refill.
    """
    expected = {cue.index for cue in selected}
    present = renderer.list_image_indexes(output_dir, episode)
    missing = [cue for cue in selected if cue.index not in present]
    index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
    indexed = renderer.count_index_rows(index_path) if options.write_index else 0
    return expected, present, missing, indexed


def _refill_missing_cues(video_path, missing, options, analyzer, work_dir, output_dir,
                         episode, result, failed_indexes, reporter):
    """Re-run cues that have dialogue but no image; return successful refill count.

    Reuses _process_cue so refill matches the normal path. Successful refills
    are removed from failed_indexes to avoid double-counting failures.
    """
    refilled = 0
    for order, cue in enumerate(missing, start=1):
        if reporter.cancelled():
            reporter.status('cancelled; remaining missing cues not refilled')
            break

        prefix = 'refill %d/%d (cue %d)' % (order, len(missing), cue.index)
        try:
            row, best = _process_cue(video_path, cue, options, analyzer,
                                     work_dir, output_dir, episode)
            result.rows.append(row)
            result.success += 1
            if best.degraded:
                result.degraded += 1
            _count_eye_state(result, best)
            failed_indexes.discard(cue.index)
            refilled += 1
            reporter.status('%s done: %s' % (prefix, row['image']))
        except VideoToolError as exc:
            failed_indexes.add(cue.index)
            result.errors.append('cue %d (refill): %s' % (cue.index, exc))
            reporter.status('%s failed: %s' % (prefix, exc))
        except Exception as exc:                      # catch-all; never abort the episode
            failed_indexes.add(cue.index)
            result.errors.append('cue %d (refill): unexpected error %s' % (cue.index, exc))
            reporter.status('%s unexpected error: %s' % (prefix, exc))
    return refilled


def _remove_quietly(path):
    """Delete a file; ignore missing/permission errors (cleanup must not abort)."""
    try:
        if path and os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


def _replace_row(result, old_row, new_row):
    """Replace an index-row object in result.rows by identity."""
    for position, row in enumerate(result.rows):
        if row is old_row:
            result.rows[position] = new_row
            return True
    return False


def _retry_options(options):
    """Copy of options for eye refine: denser sampling only; same scoring policy."""
    return dataclass_replace(
        options,
        candidate_max=max(options.candidate_max, config.EYE_REFINE_CANDIDATE_MAX),
        candidate_fps=max(options.candidate_fps, config.EYE_REFINE_CANDIDATE_FPS),
    )


def _eye_refine_targets(result, cues_by_index, mode):
    """Cues whose chosen frame eye state falls within the refine mode."""
    targets = []
    for row in result.rows:
        state = _EYE_TEXT_STATE.get(row.get('eye_state_text', ''), '')
        if not quality.eye_needs_refine(state, mode):
            continue
        cue = cues_by_index.get(row['index'])
        if cue is not None:
            targets.append((cue, row, state))
    return targets


def _eye_refine(video_path, cues_by_index, options, analyzer, work_dir, output_dir,
                episode, result, reporter):
    """Resample cues with no open-eye candidate using denser sampling.

    Triggered by the chosen frame's eye state: select_best already excludes
    closed-eye frames from the pool, so a closed pick means every candidate
    failed. These cues are usually few per episode; a denser second pass is
    cheap and cuts closed-eye rejects.

    Replace image + index row only when eye state improves; otherwise delete
    the new image and keep the original so each cue still has one file.
    """
    mode = options.eye_refine or 'off'
    if mode not in config.EYE_REFINE_MODES:
        mode = 'closed'
    if mode == 'off':
        return 0

    targets = _eye_refine_targets(result, cues_by_index, mode)
    result.eye_refine_tried = len(targets)
    if not targets:
        return 0

    retry_options = _retry_options(options)
    refined = 0
    for order, (cue, row, state) in enumerate(targets, start=1):
        if reporter.cancelled():
            reporter.status('cancelled; remaining eye-refine cues skipped')
            break

        prefix = 'eye refine %d/%d (cue %d, was: %s)' % (
            order, len(targets), cue.index, _EYE_TEXT.get(state, 'unknown'))

        try:
            new_row, best = _process_cue(video_path, cue, retry_options, analyzer,
                                         work_dir, output_dir, episode)
        except Exception as exc:                      # catch-all; never abort the episode
            result.errors.append('cue %d (eye refine): %s' % (cue.index, exc))
            reporter.status('%s failed: %s' % (prefix, exc))
            continue

        improved = quality.eye_rank(best.eye_state) > quality.eye_rank(state)
        if not improved:
            # No better frame: drop the extra image and keep the original
            if new_row['image'] != row['image']:
                _remove_quietly(os.path.join(output_dir, new_row['image']))
            reporter.status('%s no improvement; keep original' % prefix)
            continue

        if new_row['image'] != row['image']:
            _remove_quietly(os.path.join(output_dir, row['image']))
        _replace_row(result, row, new_row)

        _add_eye_state(result, state, -1)
        _add_eye_state(result, best.eye_state, 1)
        if best.degraded and not row.get('degraded'):
            result.degraded += 1
        elif row.get('degraded') and not best.degraded:
            result.degraded -= 1

        refined += 1
        reporter.status('%s replaced: %s (eye %s, score %.2f)' % (
            prefix, new_row['image'],
            _EYE_TEXT.get(best.eye_state or '', 'unknown'), best.total))

    return refined


def process_video(video_path, options=None, reporter=None):
    """Process one video: cues -> pick frames -> render -> index + report."""
    options = options or DialogueOptions()
    reporter = reporter or SilentReporter()

    output_dir = derive_output_dir(os.path.dirname(video_path))
    os.makedirs(output_dir, exist_ok=True)

    result = EpisodeResult(video=video_path, output_dir=output_dir)
    started = time.time()
    # Candidate frames go to a system temp dir so the -pics folder is not
    # flooded with hundreds of temporary frames that cleanup tools may delete.
    work_dir = tempfile.mkdtemp(prefix='video_tool_cand_')

    try:
        reporter.status('reading streams: %s' % os.path.basename(video_path))
        streams = ffmpeg_utils.probe_streams(video_path)
        if not streams:
            raise VideoToolError('cannot read video stream info')

        reporter.status('exporting subtitle track…')
        cues, subtitle_info = subtitles.load_cues(
            video_path, streams, options.subtitle_track, work_dir
        )
        result.subtitle_info = subtitle_info
        if not cues:
            raise VideoToolError('subtitle track has no cues after parse')

        selected = cues[options.start_cue - 1:]
        if options.max_cues and options.max_cues > 0:
            selected = selected[:options.max_cues]
        if not selected:
            raise VideoToolError('no cues in selected range (total %d)' % len(cues))

        result.total_cues = len(cues)
        result.planned_cues = len(selected)
        reporter.status(
            '%d cues total; processing cues %d~%d'
            % (len(cues), selected[0].index, selected[-1].index)
        )

        analyzer = quality.FaceAnalyzer()
        result.face_backend = analyzer.describe()
        result.style_source = style_profile.load_style_profile().source
        reporter.status('face backend: %s' % result.face_backend)
        reporter.status('style profile: %s' % result.style_source)

        episode = episode_tag(video_path)

        if options.clean:
            # Rebuild: clear prior episode images + index to avoid mixed styles
            result.cleaned_images = clean_output(output_dir, episode)
            reporter.status('clean rebuild removed %d images (+ index)' % result.cleaned_images)

        total = len(selected)
        loop_started = time.time()
        failed_indexes = set()

        for position, cue in enumerate(selected, start=1):
            if reporter.cancelled():
                reporter.status('cancelled; remaining cues not processed')
                break

            prefix = 'cue %d/%d' % (position, total)

            if options.resume:
                existing = renderer.find_existing_image(output_dir, episode, cue.index)
                if existing:
                    result.skipped += 1
                    result.backfill_rows.append(
                        _backfill_row(existing, cue)
                    )
                    reporter.status('%s exists, skip (resume)' % prefix)
                    reporter.progress(position / float(total) * 100.0)
                    continue

            try:
                row, best = _process_cue(video_path, cue, options, analyzer,
                                         work_dir, output_dir, episode)
                result.rows.append(row)
                result.success += 1
                if best.degraded:
                    result.degraded += 1
                _count_eye_state(result, best)

                reporter.status(
                    '%s done: %s (score %.2f, faces %d, eyes %s)'
                    % (prefix, row['image'], best.total, best.face_count,
                       _EYE_TEXT.get(best.eye_state or '', 'unknown'))
                )
            except VideoToolError as exc:
                failed_indexes.add(cue.index)
                result.errors.append('cue %d: %s' % (cue.index, exc))
                reporter.status('%s failed: %s' % (prefix, exc))
            except Exception as exc:                      # catch-all; never abort the episode
                failed_indexes.add(cue.index)
                result.errors.append('cue %d: unexpected error %s' % (cue.index, exc))
                reporter.status('%s unexpected error: %s' % (prefix, exc))

            reporter.progress(position / float(total) * 100.0)
            elapsed_loop = time.time() - loop_started
            eta = _format_eta(elapsed_loop, position, total)
            if eta:
                reporter.status('%s done, ETA %s' % (prefix, eta))

        # Eye refine: denser resample for cues with no open-eye candidate
        if options.eye_refine and options.eye_refine != 'off':
            cues_by_index = {cue.index: cue for cue in selected}
            result.eye_refined = _eye_refine(
                video_path, cues_by_index, options, analyzer, work_dir, output_dir,
                episode, result, reporter,
            )
            if result.eye_refine_tried:
                reporter.status(
                    'eye refine: %d triggered, %d improved'
                    % (result.eye_refine_tried, result.eye_refined)
                )

        # Wrap-up: align cue count / on-disk images / index rows; refill gaps
        expected, present, missing, indexed = _integrity_state(
            output_dir, episode, selected, options
        )
        result.integrity_missing = len(missing)
        reporter.status(
            'integrity: %d cues, expect %d images this run, %d on disk, %d index rows'
            % (len(cues), len(expected), len(present & expected), indexed)
        )
        if missing:
            reporter.status('%d cues missing images; refilling…' % len(missing))
            result.refilled = _refill_missing_cues(
                video_path, missing, options, analyzer, work_dir, output_dir,
                episode, result, failed_indexes, reporter,
            )
            reporter.status('refill done: +%d images, still missing %d'
                            % (result.refilled, len(missing) - result.refilled))
        result.failed = len(failed_indexes)

        # Story-gap frames: silent stretches between cues (reactions, inserts,
        # flashbacks) also carry plot; capture systematically instead of ad-hoc
        # ffmpeg. Skip on sample runs so trial folders stay free of full-run frames.
        if options.story_gaps and result.planned_cues >= result.total_cues:
            try:
                duration = ffmpeg_utils.probe_duration(video_path)
            except VideoToolError as exc:
                result.errors.append('story-gap frames: %s' % exc)
                duration = 0.0
            if duration > 0:
                gaps = story_gaps.compute_gaps(cues, duration, options.story_gap_min)
                result.story_enabled = True
                result.story_gap_min = options.story_gap_min
                result.story_gaps_total = len(gaps)
                reporter.status(
                    'story gaps: %d no-dialogue gaps (interval >= %.1f s, incl. open/end)'
                    % (len(gaps), options.story_gap_min)
                )
                story_rows, notes, story_stats = story_gaps.capture_story_frames(
                    video_path, gaps, analyzer, work_dir, output_dir, episode,
                    os.path.join(output_dir, config.INDEX_CSV_NAME),
                    options.story_gap_max_per, reporter,
                )
                result.story_rows = story_rows
                result.story_notes = notes
                result.story_frames = story_stats['captured']
                result.story_skipped = story_stats['skipped']
                reporter.status(
                    'story gaps: captured %d, skipped %d already-handled gaps'
                    % (result.story_frames, result.story_skipped)
                )

        if options.write_index:
            index_path = os.path.join(output_dir, config.INDEX_CSV_NAME)
            if result.rows:
                renderer.write_index_csv(result.rows, index_path)
            # Skipped cues lack scores but the index should cover the episode
            if result.backfill_rows:
                renderer.fill_index_gaps(result.backfill_rows, index_path)
            # Story-gap rows fill missing entries only; leave historical rows alone
            if result.story_rows:
                renderer.fill_index_gaps(result.story_rows, index_path)

        result.elapsed = time.time() - started
        if options.write_report:
            _write_episode_report(result, reporter)

        return result

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _write_episode_report(result, reporter):
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
    reporter.status('Report written: %s' % path)



# ---------------------------------------------------------------------------
# Batch run
# ---------------------------------------------------------------------------
def run(input_dir, options=None, reporter=None, max_depth=10):
    """Process all videos under input_dir; each writes to <basename>-pics."""
    options = options or DialogueOptions()
    reporter = reporter or SilentReporter()

    if not os.path.isdir(input_dir):
        raise VideoToolError('input directory not found: %s' % input_dir)

    run_result = RunResult(input_dir=input_dir)
    reporter.status('scanning: %s' % input_dir)

    videos, incomplete, unreadable = scan_directory(input_dir, max_depth, reporter)
    run_result.incomplete_files = incomplete
    run_result.unreadable_files = unreadable

    if not videos:
        reporter.status('no processable video files found')
        if incomplete:
            reporter.status(
                'found %d incomplete downloads; finish downloading first:'
                % len(incomplete)
            )
            for path in incomplete:
                reporter.status('  - %s' % ffmpeg_utils.describe_incomplete(path))
        return run_result

    for order, video_path in enumerate(videos, start=1):
        if reporter.cancelled():
            reporter.status('cancelled')
            break
        reporter.status(
            '\n=== [%d/%d] processing %s ===' % (order, len(videos), os.path.basename(video_path))
        )
        try:
            run_result.episodes.append(process_video(video_path, options, reporter))
        except VideoToolError as exc:
            episode = EpisodeResult(video=video_path, output_dir=derive_output_dir(
                os.path.dirname(video_path)))
            episode.errors.append(str(exc))
            episode.failed = 1
            run_result.episodes.append(episode)
            reporter.status('failed: %s' % exc)

    return run_result


def summarize(run_result):
    """Format run results as printable summary lines for CLI and GUI."""
    lines = ['Dialogue capture - summary', '=' * 46]
    for episode in run_result.episodes:
        lines.append('Video: %s' % os.path.basename(episode.video))
        lines.append('  Output: %s' % episode.output_dir)
        lines.append('  ok %d (degraded %d) / skipped %d / failed %d cues / %.1f s'
                     % (episode.success, episode.degraded, episode.skipped,
                        episode.failed, episode.elapsed))
        lines.append('  integrity: %d cues, missing %d, refilled %d, style %s'
                     % (episode.total_cues, episode.integrity_missing,
                        episode.refilled, episode.style_source or 'built-in defaults'))
        covered = episode.success + episode.skipped + episode.refilled
        if episode.planned_cues < episode.total_cues:
            verdict = 'sample run (%d/%d cues)' % (episode.planned_cues, episode.total_cues)
        elif episode.failed == 0 and episode.integrity_missing == 0 and covered >= episode.total_cues:
            verdict = 'PASS' if episode.success else 'no new images (all existed)'
        else:
            verdict = 'needs review'
        lines.append('  eyes: open %d / half %d / closed %d / no-face %d'
                     % (episode.eye_open, episode.eye_half,
                        episode.eye_closed, episode.eye_unknown))
        if episode.eye_refine_tried:
            lines.append('  eye refine: %d triggered, %d improved'
                         % (episode.eye_refine_tried, episode.eye_refined))
        if episode.story_enabled:
            lines.append('  story gaps: %d gaps, captured %d, skipped %d'
                         % (episode.story_gaps_total, episode.story_frames,
                            episode.story_skipped))
        lines.append('  verdict: %s (expected %d images / %d cues, failed %d)'
                     % (verdict, covered, episode.total_cues, episode.failed))
        if episode.cleaned_images:
            lines.append('  clean rebuild removed: %d old images' % episode.cleaned_images)
    lines.append('')
    lines.append('Total: ok %d, degraded %d, skipped %d, failed %d cues, story-gap frames %d'
                 % (run_result.success, run_result.degraded,
                    run_result.skipped, run_result.failed,
                    sum(episode.story_frames for episode in run_result.episodes)))

    if run_result.incomplete_files:
        lines.append('')
        lines.append('Incomplete downloads (skipped; finish download then retry):')
        for path in run_result.incomplete_files:
            lines.append('  - %s' % ffmpeg_utils.describe_incomplete(path))

    if run_result.unreadable_files:
        lines.append('')
        lines.append('Unreadable files (skipped):')
        for path in run_result.unreadable_files:
            lines.append('  - %s' % os.path.basename(path))

    return lines
