#!/usr/bin/env python3
"""Relink the supplied Concat-format document to this unpacked folder.
This checks files, not Concat runtime compatibility.
"""
from pathlib import Path
import json,shutil
p=Path(__file__).resolve().parent
portable=p/'concat.portable.json'; dest=p/'concat.json'
if not portable.exists():shutil.copy2(dest,portable)
j=json.loads(portable.read_text(encoding='utf-8')); missing=[]
for m in j['media']:
 q=Path(m['path']);q=q if q.is_absolute() else p/q
 if not q.is_file():missing.append(str(q))
 m['path']=str(q.resolve())
for m in j.get('fonts',[]):
 q=Path(m['path']);q=q if q.is_absolute() else p/q
 if not q.is_file():missing.append(str(q))
 m['path']=str(q.resolve())
if missing:raise SystemExit('Missing media:\n'+'\n'.join(missing))
dest.write_text(json.dumps(j,ensure_ascii=False,indent=2),encoding='utf-8')
print(f'Relinked {len(j["media"])} media files to {p}')
print('Concat runtime compatibility has not been tested in this environment.')
