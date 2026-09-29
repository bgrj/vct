# -*- coding: utf-8 -*-
"""Replace remaining user-facing Chinese strings in pipeline.py via unicode escapes."""
from pathlib import Path
import py_compile

p = Path(r'd:\03_Knowledge\intimate\video-pics-cut\video_tool\pipeline.py')
t = p.read_text(encoding='utf-8')

# (old, new) — old written with \u escapes so this file stays ASCII-safe
pairs = [
    ('\u8865\u8dd1 %d/%d\uff08\u7b2c %d \u53e5\uff09', 'refill %d/%d (cue %d)'),
    ('\u7b2c %d \u53e5\uff08\u8865\u8dd1\uff09\uff1a%s', 'cue %d (refill): %s'),
    ('\u7b2c %d \u53e5\uff08\u8865\u8dd1\uff09\uff1a\u672a\u9884\u671f\u9519\u8bef %s',
     'cue %d (refill): unexpected %s'),
    ('\u91cd\u91c7\u6837 %d/%d\uff08\u7b2c %d \u53e5\uff0c\u539f\u72b6\u6001\uff1a%s\uff09',
     'refine %d/%d (cue %d, was: %s)'),
    ('\u672a\u5224\u5b9a', 'unknown'),
    ('\u7b2c %d \u53e5\uff08\u91cd\u91c7\u6837\uff09\uff1a%s', 'cue %d (refine): %s'),
    ('\u5171 %d \u53e5\u53f0\u8bcd\uff0c\u672c\u6b21\u5904\u7406\u7b2c %d~%d \u53e5',
     '%d cues total; processing %d~%d'),
    ('\u7b2c %d/%d \u53e5', 'cue %d/%d'),
    ('%s \u5b8c\u6210\uff1a%s\uff08\u5f97\u5206 %.2f\uff0c\u4eba\u8138 %d\uff0c\u773c\u775b %s\uff09',
     '%s done: %s (score %.2f, faces %d, eyes %s)'),
    ('\u7b2c %d \u53e5\uff1a%s', 'cue %d: %s'),
    ('\u7b2c %d \u53e5\uff1a\u672a\u9884\u671f\u9519\u8bef %s', 'cue %d: unexpected %s'),
    ('\u5b8c\u6574\u6027\u6821\u9a8c\uff1a\u53f0\u8bcd %d \u53e5\uff0c\u672c\u6b21\u5e94\u6709 %d \u5f20\uff0c\u76ee\u5f55\u5185 %d \u5f20\uff0c\u7d22\u5f15 %d \u884c',
     'integrity: cues %d, expected %d, on disk %d, index rows %d'),
    ('\u5267\u60c5\u5ef6\u7eed\u5e27\uff1a%s', 'story gaps: %s'),
    ('\u5267\u60c5\u5ef6\u7eed\u5e27\uff1a\u53d1\u73b0 %d \u4e2a\u65e0\u53f0\u8bcd\u7a7a\u7a97\uff08\u95f4\u9694 \u2265 %.1f \u79d2\uff0c\u542b\u7247\u5934/\u7247\u5c3e\uff09',
     'story gaps: %d windows (interval >= %.1f s, incl. head/tail)'),
    ('\u5267\u60c5\u5ef6\u7eed\u5e27\uff1a\u8865\u622a %d \u5f20\uff0c\u8df3\u8fc7\u5df2\u5904\u7406\u7a7a\u7a97 %d \u4e2a',
     'story gaps: captured %d, skipped %d'),
    ('\u53d1\u73b0 %d \u4e2a\u672a\u4e0b\u8f7d\u5b8c\u6210\u7684\u6587\u4ef6\uff0c\u8bf7\u5148\u4e0b\u8f7d\u5b8c\u6574\uff1a',
     'found %d incomplete downloads; finish download first:'),
    ('\n=== [%d/%d] \u5f00\u59cb\u5904\u7406 %s ===',
     '\n=== [%d/%d] start %s ==='),
    ('\u5904\u7406\u5931\u8d25\uff1a%s', 'failed: %s'),
    ('\u53f0\u8bcd\u667a\u80fd\u622a\u56fe - \u6c47\u603b', 'Dialogue capture - summary'),
    ('\u89c6\u9891\uff1a%s', 'Video: %s'),
    ('  \u8f93\u51fa\u76ee\u5f55\uff1a%s', '  Output: %s'),
    ('  \u6210\u529f %d \u5f20\uff08\u964d\u7ea7 %d \u5f20\uff09\uff0f\u8df3\u8fc7 %d \u5f20\uff0f\u5931\u8d25 %d \u53e5\uff0f\u8017\u65f6 %.1f \u79d2',
     '  ok %d (degraded %d) / skip %d / fail %d / %.1f s'),
    ('  \u5b8c\u6574\u6027\uff1a\u53f0\u8bcd %d \u53e5\uff0c\u7f3a\u56fe %d \u53e5\uff0c\u8865\u8dd1 %d \u5f20\uff0c\u6837\u5f0f\u57fa\u7ebf %s',
     '  integrity: cues %d, missing %d, refilled %d, style %s'),
    ('\u5185\u7f6e\u9ed8\u8ba4\u503c', 'built-in defaults'),
    ('\u62bd\u6837\u8bd5\u8dd1\uff08%d/%d \u53e5\uff09', 'SAMPLE (%d/%d cues)'),
    ("verdict = '\u901a\u8fc7' if episode.success else '\u65e0\u65b0\u589e\uff08\u5747\u4e3a\u5df2\u5b58\u5728\u56fe\u7247\uff09'",
     "verdict = 'PASS' if episode.success else 'NO NEW (all existed)'"),
    ("verdict = '\u9700\u4eba\u5de5\u590d\u6838'",
     "verdict = 'NEEDS REVIEW'"),
    ('  \u91cd\u91c7\u6837\u6362\u5e27\uff1a\u89e6\u53d1 %d \u53e5\uff0c\u6210\u529f\u6362\u5e27 %d \u53e5',
     '  eye refine: triggered %d, improved %d'),
    ('  \u5267\u60c5\u5ef6\u7eed\u5e27\uff1a\u7a7a\u7a97 %d \u4e2a\uff0c\u8865\u622a %d \u5f20\uff0c\u8df3\u8fc7\u5df2\u5904\u7406 %d \u4e2a',
     '  story gaps: windows %d, captured %d, skipped %d'),
    ('  \u9a8c\u6536\u7ed3\u8bba\uff1a%s\uff08\u5e94\u51fa\u56fe %d \u5f20 / \u53f0\u8bcd %d \u53e5\uff0c\u5931\u8d25 %d \u53e5\uff09',
     '  verdict: %s (expected %d / cues %d, fail %d)'),
    ('  \u91cd\u5efa\u6e05\u7406\uff1a%d \u5f20\u5386\u53f2\u56fe\u7247',
     '  clean rebuild removed: %d images'),
    ('\u5408\u8ba1\uff1a\u6210\u529f %d \u5f20\uff0c\u964d\u7ea7 %d \u5f20\uff0c\u8df3\u8fc7 %d \u5f20\uff0c\u5931\u8d25 %d \u53e5\uff0c\u5267\u60c5\u5ef6\u7eed\u5e27 %d \u5f20',
     'TOTAL: ok %d, degraded %d, skip %d, fail %d, story %d'),
    ('\u672a\u4e0b\u8f7d\u5b8c\u6210\uff08\u5df2\u8df3\u8fc7\uff0c\u8bf7\u4e0b\u8f7d\u5b8c\u6574\u540e\u518d\u5904\u7406\uff09\uff1a',
     'Incomplete downloads (skipped):'),
    ('\u65e0\u6cd5\u8bc6\u522b\u7684\u6587\u4ef6\uff08\u5df2\u8df3\u8fc7\uff09\uff1a',
     'Unrecognized files (skipped):'),
    ("return '\u7ea6 %d \u79d2' % int(remaining)",
     "return '~%d s' % int(remaining)"),
    ("return '\u7ea6 %.1f \u5206\u949f' % (remaining / 60.0)",
     "return '~%.1f min' % (remaining / 60.0)"),
    ('"""\u5904\u7406\u8f93\u5165\u76ee\u5f55\u4e2d\u7684\u5168\u90e8\u89c6\u9891\uff0c\u6bcf\u4e2a\u89c6\u9891\u8f93\u51fa\u5230\u5176\u6240\u5728\u76ee\u5f55\u7684 <\u76ee\u5f55\u540d>-pc \u4e0b\u3002"""',
     '"""Process all videos under input_dir; each writes to <basename>-pics."""'),
]

n = 0
for a, b in pairs:
    if a in t:
        t = t.replace(a, b)
        n += 1
    else:
        print('miss:', a.encode('unicode_escape')[:80])

# Fix missing closing paren on Report written if needed
if "reporter.status('Report written: %s' % path\n" in t and "reporter.status('Report written: %s' % path)\n" not in t:
    t = t.replace("reporter.status('Report written: %s' % path\n",
                  "reporter.status('Report written: %s' % path)\n")
    print('fixed paren')

p.write_text(t, encoding='utf-8')
print('replaced', n)
py_compile.compile(str(p), doraise=True)
print('syntax ok')
