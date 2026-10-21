#!/usr/bin/env python3
"""Rebuild concat.json from edl.csv. Run inside this mix folder."""
import csv, json
from pathlib import Path

root = Path(__file__).resolve().parent
rows = list(csv.DictReader((root / "edl.csv").open(encoding="utf-8-sig")))
media, clips = [], []
for i, row in enumerate(rows, 1):
    mid, cid = f"m{i}", f"c{i}"
    if row["kind"] == "title":
        path, name = f"cards/{row['file']}.png", row["file"]
    else:
        path, name = f"../../{row['episode']}/{row['file']}", row["file"]
    media.append({"id": mid, "path": path, "name": name, "kind": "image",
                  "width": 1920, "height": 1080, "hasAudio": False})
    clips.append({
        "id": cid, "trackId": "T1", "mediaId": mid, "name": name, "kind": "image",
        "start": round(float(row["start"]), 3), "duration": float(row["duration"]),
        "sourceStart": 0, "volume": 1, "fadeIn": 0.28, "fadeOut": 0.28,
        "scale": 1, "offsetX": 0, "offsetY": 0, "rotation": 0, "opacity": 1, "speed": 1,
    })
tracks = [{"id": f"T{n}", "visible": True, "muted": n == 4} for n in range(1, 5)]
video = {"width": 1920, "height": 1080, "rateNum": 24, "rateDen": 1}
doc = {
    "concat": "0.1.0", "version": 1, "name": "不得人爱，便无法爱人", "video": video,
    "media": media, "tracks": tracks, "clips": clips, "fonts": [],
    "timelines": [{"id": "TL1", "name": "混剪", "video": video, "tracks": tracks, "clips": clips}],
    "activeTimelineId": "TL1",
}
(root / "concat.json").write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
print("wrote", root / "concat.json", "clips", len(clips))
