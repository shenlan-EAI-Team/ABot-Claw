#!/usr/bin/env python3
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import json, urllib.request, yaml
ROOT=Path(__file__).resolve().parents[3]
cfg=yaml.safe_load((ROOT/'config/deployment.yaml').read_text())
def check(name, item):
    url=item['url'].rstrip('/')+'/health'
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data=json.loads(r.read().decode()); return name, {'online':True,'optional':bool(item.get('optional')),'detail':data}
    except Exception as e: return name, {'online':False,'optional':bool(item.get('optional')),'error':str(e)}
results={}
with ThreadPoolExecutor(max_workers=4) as ex:
    fs=[ex.submit(check,n,v) for n,v in cfg['services'].items()]
    for f in as_completed(fs): n,v=f.result(); results[n]=v
required_failed=[n for n,v in results.items() if not v['online'] and not v['optional'] and cfg['services'][n].get('required',False)]
print(json.dumps({'status':'failed' if required_failed else 'success','services':results,'required_failed':required_failed},ensure_ascii=False,indent=2))
raise SystemExit(1 if required_failed else 0)
