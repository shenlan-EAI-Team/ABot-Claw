#!/usr/bin/env python3
from pathlib import Path
import json, py_compile, sys
root=Path(__file__).resolve().parent
manifest=json.loads((root/'workspace-manifest.json').read_text())
dirs=sorted(p.name for p in (root/'skills').iterdir() if p.is_dir())
errors=[]
if len(dirs)!=10:errors.append(f'expected 10 skills, got {len(dirs)}: {dirs}')
if sorted(manifest['skills'])!=dirs:errors.append('manifest skill list mismatch')
for p in root.rglob('*.py'):
    try:py_compile.compile(str(p),doraise=True)
    except Exception as e:errors.append(f'{p}: {e}')
for forbidden in ('visitor-reception','face-recognition','abotclaw-speak','check-status','robot-executor'):
    if any(forbidden in d for d in dirs):errors.append('forbidden task/capability skill: '+forbidden)
print(json.dumps({'status':'failed' if errors else 'success','skills':dirs,'errors':errors},ensure_ascii=False,indent=2))
raise SystemExit(1 if errors else 0)
