#!/usr/bin/env python3
from pathlib import Path
import argparse, json, time, urllib.request, urllib.error, yaml
ROOT=Path(__file__).resolve().parents[3]
cfg=yaml.safe_load((ROOT/'config/deployment.yaml').read_text())
robot=cfg['robot']['base_url'].rstrip('/'); mem=cfg['services']['spatial_memory']['url'].rstrip('/')
def request(url,method='GET',body=None,headers=None,timeout=10):
    raw=None if body is None else json.dumps(body,ensure_ascii=False).encode(); h={'Accept':'application/json'}
    if raw is not None:h['Content-Type']='application/json'
    if headers:h.update(headers)
    q=urllib.request.Request(url,data=raw,headers=h,method=method)
    with urllib.request.urlopen(q,timeout=timeout) as r:
        t=r.read().decode(); return json.loads(t) if t else {}
def first(d,*keys):
    for k in keys:
        if isinstance(d,dict) and d.get(k) not in (None,''):return d[k]
def items(d):
    if isinstance(d,list):return d
    if isinstance(d,dict):
        for k in ('results','items','hits','records'):
            if isinstance(d.get(k),list):return d[k]
        if isinstance(d.get('data'),dict):return items(d['data'])
    return []
def pose(hit):
    for k in ('target_pose','place_pose','pose'):
        if isinstance(hit.get(k),dict):return hit[k]
    return hit if 'x' in hit and 'y' in hit else None
def code_for(tp,timeout_s):
    return f'''import json\nimport math\nimport time\nfrom geometry_msgs.msg import PoseStamped\ntarget={json.dumps(tp,ensure_ascii=False)!r}\ntarget=json.loads(target)\np=PoseStamped(); p.header.frame_id=str(target.get("frame_id") or "map")\np.pose.position.x=float(target["x"]); p.pose.position.y=float(target["y"]); p.pose.position.z=float(target.get("z",0))\nif all(target.get(k) is not None for k in ("qx","qy","qz","qw")):\n p.pose.orientation.x=float(target["qx"]); p.pose.orientation.y=float(target["qy"]); p.pose.orientation.z=float(target["qz"]); p.pose.orientation.w=float(target["qw"])\nelse:\n yaw=float(target.get("yaw",0)); p.pose.orientation.z=math.sin(yaw/2); p.pose.orientation.w=math.cos(yaw/2)\nnav=Nav2Anywhere(); nav.nav_to_pose(p); reached=nav.wait_until_reached(timeout_sec={float(timeout_s)})\ncurrent=nav.get_current_pose()\nout={{"status":"success" if reached else "failed","reached":bool(reached),"target":target,"current":str(current)}}\nprint("RESULT_JSON="+json.dumps(out,ensure_ascii=False,default=str),flush=True)\n'''
def extract_result(d):
    texts=[]
    def walk(v):
        if isinstance(v,dict):
            for k,x in v.items():
                if k in ('stdout','output','logs','raw') and isinstance(x,str):texts.append(x)
                walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(d)
    for t in texts:
        for line in t.splitlines():
            if line.startswith('RESULT_JSON='):
                try:return json.loads(line.split('=',1)[1])
                except:pass
p=argparse.ArgumentParser(); p.add_argument('--name',required=True); p.add_argument('--timeout',type=float); a=p.parse_args(); lease_id=None
try:
    q=request(mem+'/query/place','POST',{'name':a.name,'n_results':5}); hits=items(q)
    if not hits:raise RuntimeError('place not found: '+a.name)
    tp=pose(hits[0]); timeout_s=a.timeout or cfg['execution']['navigation_timeout_s']
    lease=request(robot+'/lease/acquire','POST',{'holder':cfg['execution']['lease_holder_prefix']+'-navigate'}); lease_id=first(lease,'lease_id','id','lease')
    sub=request(robot+'/code/execute','POST',{'code':code_for(tp,timeout_s),'timeout':timeout_s+25},{'X-Lease-Id':lease_id},15); eid=first(sub,'execution_id','id','task_id','run_id')
    if not eid:raise RuntimeError('missing execution id: '+repr(sub))
    deadline=time.monotonic()+timeout_s+30; result=None
    while time.monotonic()<deadline:
        d=request(robot+'/code/result/'+eid,timeout=10); result=extract_result(d)
        if result is not None:break
        state=str(first(d,'status','state','execution_status') or '').lower()
        if state in ('failed','error','timeout','timed_out'):raise RuntimeError(repr(d))
        time.sleep(2)
    if result is None:raise RuntimeError('navigation result timeout')
    print(json.dumps({'status':result.get('status'),'location_name':a.name,'result':result},ensure_ascii=False,indent=2)); raise SystemExit(0 if result.get('status')=='success' else 1)
except SystemExit: raise
except Exception as e:
    print(json.dumps({'status':'failed','error':str(e)},ensure_ascii=False,indent=2)); raise SystemExit(1)
finally:
    if lease_id:
        try:request(robot+'/lease/release','POST',{'lease_id':lease_id},timeout=5)
        except Exception:pass
