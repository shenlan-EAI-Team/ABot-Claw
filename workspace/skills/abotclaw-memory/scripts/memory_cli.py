#!/usr/bin/env python3
from pathlib import Path
import argparse, json, urllib.request, urllib.error, yaml
ROOT=Path(__file__).resolve().parents[3]
cfg=yaml.safe_load((ROOT/'config/deployment.yaml').read_text())
base=cfg['services']['spatial_memory']['url'].rstrip('/')
def req(path, body):
    raw=json.dumps(body,ensure_ascii=False).encode()
    q=urllib.request.Request(base+path,data=raw,headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(q,timeout=10) as r: return json.loads(r.read().decode())
p=argparse.ArgumentParser(); sp=p.add_subparsers(dest='cmd',required=True)
q=sp.add_parser('query-place'); q.add_argument('--name',required=True); q.add_argument('--n-results',type=int,default=5)
u=sp.add_parser('upsert-place'); u.add_argument('--json-file',required=True)
a=p.parse_args()
try:
    if a.cmd=='query-place': out=req('/query/place',{'name':a.name,'n_results':a.n_results})
    else: out=req('/memory/place/upsert',json.loads(Path(a.json_file).read_text()))
    print(json.dumps({'status':'success','result':out},ensure_ascii=False,indent=2))
except Exception as e:
    print(json.dumps({'status':'failed','error':str(e)},ensure_ascii=False,indent=2)); raise SystemExit(1)
