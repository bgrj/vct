# Video Pics Cut — Quick Start

Capture one still per dialogue line from a soft-subtitled episode video. White text with black stroke is burned onto the frame; files are named in order.

## 1. Folder layout

Keep the tool and your show as **siblings**:

```text
<workspace>/
├── video-pics-cut/          ← this tool
└── your-show/
    └── 01/                  ← two-digit episode folder
        └── Show.Name.S01E01.mkv   ← filename must contain S01E01
```

After a run you get `01/01-pics/` (`*.jpg` + `_index.csv` + `_report.txt`).

See also [examples/your-show/README.md](examples/your-show/README.md).

## 2. One-time setup

1. Install **Python 3.12** (`python` on PATH)
2. Install **FFmpeg** (add `bin` to PATH, or unpack to `C:\ffmpeg\bin`)
3. Double-click [`scripts/check_env.bat`](scripts/check_env.bat)

Or:

```bash
cd video-pics-cut
python -m pip install -r requirements.txt
python main.py --help
```

## 3. Daily use

1. Double-click [`scripts/run_gui.bat`](scripts/run_gui.bat)
2. Browse to an episode folder (e.g. `your-show\01`)
3. Start; then open `01-pics\_report.txt`

Sample first 3 cues:

```bash
python main.py --input "..\your-show\01" --max-cues 3
```

Quote paths that contain spaces.

## 4. What to check

| File | Role |
| --- | --- |
| `01-pics\*.jpg` | Stills |
| `01-pics\_index.csv` | Cue ↔ filename table |
| `01-pics\_report.txt` | Pass / fail summary |

Full docs: [README.md](README.md).  
Chinese walkthrough with a real show: [examples/love-in-the-big-city/BEGINNER_WALKTHROUGH.zh.md](examples/love-in-the-big-city/BEGINNER_WALKTHROUGH.zh.md).
