#!/usr/bin/env python3
from pathlib import Path
import json, urllib.request, urllib.error
import yaml
ROOT=Path(__file__).resolve().parents[3]
cfg=yaml.safe_load((ROOT/'config/deployment.yaml').read_text())
base=cfg['robot']['base_url'].rstrip('/')
def get(path):
    try:
        with urllib.request.urlopen(base+path, timeout=5) as r:
            return {'ok': True, 'data': json.loads(r.read().decode())}
    except Exception as e: return {'ok': False, 'error': str(e)}
health=get('/health'); state=get('/state')
print(json.dumps({'status':'success' if health['ok'] else 'failed','robot':cfg['robot']['id'],'health':health,'state':state},ensure_ascii=False,indent=2))
raise SystemExit(0 if health['ok'] else 1)
