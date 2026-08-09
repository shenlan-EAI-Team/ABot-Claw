#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import argparse, json, math, sys, time, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
import yaml
ROOT=Path(__file__).resolve().parents[3]
cfg=yaml.safe_load((ROOT/'config/deployment.yaml').read_text())
robot=cfg['robot']['base_url'].rstrip('/'); services=cfg['services']; ex_cfg=cfg['execution']
SUPPORTED={'navigate','face_wait','speak','detect_object','grasp','release','wait'}

def request(url,method='GET',body=None,headers=None,timeout=10):
    raw=None if body is None else json.dumps(body,ensure_ascii=False).encode(); h={'Accept':'application/json'}
    if raw is not None:h['Content-Type']='application/json'
    if headers:h.update(headers)
    q=urllib.request.Request(url,data=raw,headers=h,method=method)
    try:
        with urllib.request.urlopen(q,timeout=timeout) as r:
            t=r.read().decode(errors='replace'); return json.loads(t) if t else {}
    except urllib.error.HTTPError as e: raise RuntimeError(f'HTTP {e.code} {url}: '+e.read().decode(errors='replace'))
    except urllib.error.URLError as e: raise RuntimeError(f'request failed {url}: {e.reason}')
def first(d,*keys):
    for k in keys:
        if isinstance(d,dict) and d.get(k) not in (None,''):return d[k]
def list_items(d):
    if isinstance(d,list):return d
    if isinstance(d,dict):
        for k in ('results','items','hits','records'):
            if isinstance(d.get(k),list):return d[k]
        if isinstance(d.get('data'),dict):return list_items(d['data'])
    return []
def target_pose(hit):
    if not isinstance(hit,dict):return None
    for k in ('target_pose','place_pose','pose'):
        if isinstance(hit.get(k),dict):return hit[k]
    return hit if 'x' in hit and 'y' in hit else None

def validate(plan):
    if not isinstance(plan,dict) or not isinstance(plan.get('steps'),list) or not plan['steps']:raise ValueError('plan.steps must be a non-empty list')
    if len(plan['steps'])>20:raise ValueError('plan has more than 20 steps')
    ids=set(); worst=0.0
    for i,s in enumerate(plan['steps']):
        if not isinstance(s,dict):raise ValueError(f'step {i} is not object')
        sid=s.get('id'); typ=s.get('type')
        if not isinstance(sid,str) or not sid:raise ValueError(f'step {i} missing id')
        if sid in ids:raise ValueError('duplicate step id: '+sid)
        ids.add(sid)
        if typ not in SUPPORTED:raise ValueError(f'unsupported step type: {typ}')
        if typ=='navigate' and not s.get('location') and not isinstance(s.get('target_pose'),dict):raise ValueError(sid+' navigate needs location or target_pose')
        if typ=='face_wait' and not s.get('target') and not s.get('any_known',False):raise ValueError(sid+' face_wait needs target or any_known')
        if typ=='speak' and not isinstance(s.get('text'),str):raise ValueError(sid+' speak needs text')
        if typ in ('detect_object','grasp') and not s.get('object'):raise ValueError(sid+' needs object')
        if typ=='wait' and float(s.get('seconds',0))<0:raise ValueError(sid+' wait seconds invalid')
        when=s.get('when')
        if when is not None:
            if not isinstance(when,dict) or when.get('step') not in ids:raise ValueError(sid+' when must reference an earlier step')
        if typ=='navigate': worst+=float(s.get('timeout_s',ex_cfg['navigation_timeout_s']))
        elif typ=='face_wait': worst+=float(s.get('timeout_s',ex_cfg['face_wait_timeout_s']))
        elif typ=='wait': worst+=float(s.get('seconds',0))
        else: worst+=10
    worst+=float(ex_cfg.get('code_overhead_s',25))
    if worst>float(ex_cfg['max_plan_runtime_s']):raise ValueError(f'plan worst-case runtime {worst:.1f}s exceeds limit {ex_cfg["max_plan_runtime_s"]}s')
    return worst

def resolve_locations(plan):
    base=services['spatial_memory']['url'].rstrip('/')
    for s in plan['steps']:
        if s['type']=='navigate' and not isinstance(s.get('target_pose'),dict):
            name=s['location']; q=request(base+'/query/place','POST',{'name':name,'n_results':5}); hits=list_items(q)
            if not hits:raise RuntimeError('place not found: '+name)
            tp=target_pose(hits[0])
            if not tp or 'x' not in tp or 'y' not in tp:raise RuntimeError('place has no navigation pose: '+name)
            s['target_pose']=tp
    return plan

def required_services(plan):
    req=set()
    for s in plan['steps']:
        if s['type']=='navigate':req.add('spatial_memory')
        if s['type']=='face_wait':req.add('face')
        if s['type'] in ('detect_object','grasp'):req.add('yolo')
        if s['type']=='grasp' and not services.get('anygrasp',{}).get('optional',True):req.add('anygrasp')
    return req

def preflight(plan):
    checks={'robot':robot+'/health'}
    for n in required_services(plan):checks[n]=services[n]['url'].rstrip('/')+'/health'
    out={}
    def one(n,u):
        try:return n,{'ok':True,'data':request(u,timeout=5)}
        except Exception as e:return n,{'ok':False,'error':str(e)}
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        for f in as_completed([pool.submit(one,n,u) for n,u in checks.items()]):n,v=f.result();out[n]=v
    failed=[n for n,v in out.items() if not v['ok']]
    if failed:raise RuntimeError('preflight failed: '+json.dumps(out,ensure_ascii=False))
    return out

def build_code(plan):
    plan_literal=json.dumps(plan,ensure_ascii=False)
    return r'''import json
import math
import time
from geometry_msgs.msg import PoseStamped
plan=json.loads(__PLAN__)
results={}
ordered=[]
nav=None
tts_ready=False

def field_value(obj,path):
    cur=obj
    for part in str(path).split('.'):
        if not isinstance(cur,dict):return None
        cur=cur.get(part)
    return cur

def condition_ok(cond):
    if not cond:return True
    prev=results.get(cond.get('step'))
    if prev is None:return False
    return field_value(prev,cond.get('field','status'))==cond.get('equals')

def record(step,status,started,**extra):
    item={'id':step['id'],'type':step['type'],'status':status,'duration_s':round(time.monotonic()-started,3)}
    item.update(extra);results[step['id']]=item;ordered.append(item)
    print('STAGE_JSON='+json.dumps(item,ensure_ascii=False,default=str),flush=True)
    return item

def make_pose(t):
    p=PoseStamped();p.header.frame_id=str(t.get('frame_id') or 'map')
    p.pose.position.x=float(t['x']);p.pose.position.y=float(t['y']);p.pose.position.z=float(t.get('z',0))
    if all(t.get(k) is not None for k in ('qx','qy','qz','qw')):
        p.pose.orientation.x=float(t['qx']);p.pose.orientation.y=float(t['qy']);p.pose.orientation.z=float(t['qz']);p.pose.orientation.w=float(t['qw'])
    else:
        yaw=float(t.get('yaw',0));p.pose.orientation.z=math.sin(yaw/2);p.pose.orientation.w=math.cos(yaw/2)
    return p

def face_results(resp):
    if isinstance(resp,list):return resp
    if isinstance(resp,dict):
        if isinstance(resp.get('results'),list):return resp['results']
        if isinstance(resp.get('data'),dict) and isinstance(resp['data'].get('results'),list):return resp['data']['results']
    return []
def face_name(x):
    return x.get('name') or x.get('person_name') or x.get('identity') or x.get('label') if isinstance(x,dict) else None

fatal=False
for step in plan['steps']:
    started=time.monotonic()
    if not condition_ok(step.get('when')):
        record(step,'skipped',started,reason='condition_not_met');continue
    try:
        typ=step['type']
        if typ=='navigate':
            if nav is None:nav=Nav2Anywhere()
            p=make_pose(step['target_pose']);nav.nav_to_pose(p)
            reached=bool(nav.wait_until_reached(timeout_sec=float(step.get('timeout_s',75))))
            record(step,'success' if reached else 'failed',started,reached=reached,location=step.get('location'),target_pose=step['target_pose'])
            if not reached and not step.get('continue_on_failure',False):fatal=True
        elif typ=='face_wait':
            deadline=time.monotonic()+float(step.get('timeout_s',30));matched=None;last=[];frames=0
            while time.monotonic()<deadline:
                last=face_results(face.recognize_current_frame());frames+=1
                for item in last:
                    name=face_name(item)
                    if (step.get('any_known') and name) or (step.get('target') and name==step.get('target')):
                        matched=item;break
                if matched is not None:break
                time.sleep(float(step.get('poll_interval_s',1)))
            record(step,'success' if matched is not None else 'not_detected',started,matched=matched is not None,name=face_name(matched) if matched else None,match=matched,frames_checked=frames,last_results=last)
            if matched is None and step.get('required',False) and not step.get('continue_on_failure',False):fatal=True
        elif typ=='speak':
            if not tts_ready:tts.initialize();tts_ready=True
            tts.speak(step['text']);record(step,'success',started,spoken_text=step['text'])
        elif typ=='detect_object':
            rgb,_=camera.get_frame();det=yolo.detect_on_rgb(rgb);record(step,'success',started,object=step['object'],detections=det)
        elif typ=='grasp':
            ok=bool(grasp_something(step['object']));record(step,'success' if ok else 'failed',started,object=step['object'],grasped=ok)
            if not ok and not step.get('continue_on_failure',False):fatal=True
        elif typ=='release':
            fn=release_something if step.get('mode','full')=='full' else release_object
            val=fn();record(step,'success',started,result=val)
        elif typ=='wait':
            sec=float(step.get('seconds',0));time.sleep(sec);record(step,'success',started,seconds=sec)
    except Exception as e:
        record(step,'failed',started,error=str(e));
        if not step.get('continue_on_failure',False):fatal=True
    if fatal:break
status='failed' if any(x['status']=='failed' for x in ordered) else ('partial' if any(x['status'] in ('not_detected','skipped') for x in ordered) else 'success')
print('RESULT_JSON='+json.dumps({'status':status,'stages':ordered},ensure_ascii=False,default=str),flush=True)
'''.replace('__PLAN__',repr(plan_literal))

def extract_payload(d,prefix):
    texts=[]
    def walk(v):
        if isinstance(v,dict):
            for k,x in v.items():
                if k in ('stdout','output','logs','raw','stderr') and isinstance(x,str):texts.append(x)
                walk(x)
        elif isinstance(v,list):
            for x in v:walk(x)
    walk(d); found=None
    for t in texts:
        for line in t.splitlines():
            if line.strip().startswith(prefix):
                try:found=json.loads(line.strip()[len(prefix):])
                except:pass
    return found

def main():
    ap=argparse.ArgumentParser();g=ap.add_mutually_exclusive_group(required=True);g.add_argument('--plan-file');g.add_argument('--plan-json');a=ap.parse_args()
    plan=json.loads(Path(a.plan_file).read_text()) if a.plan_file else json.loads(a.plan_json)
    lease_id=None;started=time.monotonic()
    try:
        worst=validate(plan);plan=resolve_locations(plan);checks=preflight(plan);code=build_code(plan)
        lease=request(robot+'/lease/acquire','POST',{'holder':ex_cfg['lease_holder_prefix']+'-plan'});lease_id=first(lease,'lease_id','id','lease')
        if not lease_id:raise RuntimeError('lease response missing lease id')
        timeout=min(float(ex_cfg['max_plan_runtime_s']),worst)
        submitted=request(robot+'/code/execute','POST',{'code':code,'timeout':timeout},{'X-Lease-Id':lease_id},15);eid=first(submitted,'execution_id','id','task_id','run_id')
        if not eid:raise RuntimeError('execute response missing id: '+repr(submitted))
        deadline=time.monotonic()+timeout+10;last_progress=0;result=None
        while time.monotonic()<deadline:
            d=request(robot+'/code/result/'+eid,timeout=10);result=extract_payload(d,'RESULT_JSON=')
            if result is not None:break
            now=time.monotonic()
            if now-last_progress>=10:print(json.dumps({'status':'running','elapsed_s':round(now-started,1),'execution_id':eid},ensure_ascii=False),flush=True);last_progress=now
            state=str(first(d,'status','state','execution_status') or '').lower()
            if state in ('failed','error','timeout','timed_out','cancelled'):raise RuntimeError('remote execution failed: '+repr(d))
            time.sleep(float(ex_cfg['result_poll_interval_s']))
        if result is None:raise RuntimeError('plan result timeout')
        out={'status':result.get('status','failed'),'preflight':checks,'result':result,'duration_s':round(time.monotonic()-started,2)}
        print(json.dumps(out,ensure_ascii=False,indent=2))
        return 0 if out['status'] in ('success','partial') else 1
    except Exception as e:
        print(json.dumps({'status':'failed','error':str(e),'duration_s':round(time.monotonic()-started,2)},ensure_ascii=False,indent=2));return 1
    finally:
        if lease_id:
            try:request(robot+'/lease/release','POST',{'lease_id':lease_id},timeout=5)
            except Exception as e:print(json.dumps({'warning':'lease release failed','error':str(e)},ensure_ascii=False),file=sys.stderr)
if __name__=='__main__':raise SystemExit(main())
