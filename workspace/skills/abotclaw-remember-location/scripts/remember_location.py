#!/usr/bin/env python3
from pathlib import Path
import argparse, json, math, urllib.request, yaml
ROOT=Path(__file__).resolve().parents[3]
cfg=yaml.safe_load((ROOT/'config/deployment.yaml').read_text())
robot=cfg['robot']['base_url'].rstrip('/'); mem=cfg['services']['spatial_memory']['url'].rstrip('/')
def get_json(url):
    with urllib.request.urlopen(url,timeout=10) as r:return json.loads(r.read().decode())
def post_json(url,body):
    q=urllib.request.Request(url,data=json.dumps(body,ensure_ascii=False).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(q,timeout=10) as r:return json.loads(r.read().decode())
def pose_from(data):
    d=data.get('pose',data)
    if isinstance(d.get('pose'),dict): d=d['pose']
    pos=d.get('position',d); ori=d.get('orientation',d)
    x=pos.get('x'); y=pos.get('y')
    if x is None or y is None: raise RuntimeError('current pose missing x/y')
    qx=float(ori.get('x',ori.get('qx',0))); qy=float(ori.get('y',ori.get('qy',0))); qz=float(ori.get('z',ori.get('qz',0))); qw=float(ori.get('w',ori.get('qw',1)))
    return {'x':float(x),'y':float(y),'z':float(pos.get('z',0)),'qx':qx,'qy':qy,'qz':qz,'qw':qw,'frame_id':data.get('frame_id') or data.get('header',{}).get('frame_id') or 'map'}
p=argparse.ArgumentParser(); p.add_argument('--name',required=True); p.add_argument('--note',default=''); a=p.parse_args()
try:
    raw=get_json(robot+'/nav/current_pose'); pose=pose_from(raw)
    body={'place_name':a.name.strip(),'robot_id':cfg['robot']['id'],'robot_type':'humanoid','place_pose':pose,'note':a.note}
    saved=post_json(mem+'/memory/place/upsert',body)
    print(json.dumps({'status':'success','location_name':a.name,'pose':pose,'memory':saved},ensure_ascii=False,indent=2))
except Exception as e:
    print(json.dumps({'status':'failed','error':str(e)},ensure_ascii=False,indent=2)); raise SystemExit(1)
