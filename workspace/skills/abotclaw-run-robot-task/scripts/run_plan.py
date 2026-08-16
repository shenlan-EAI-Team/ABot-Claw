#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import argparse
import json
import math
import sys
import time
import urllib.request
import urllib.error

from concurrent.futures import ThreadPoolExecutor, as_completed

import yaml


ROOT = Path(__file__).resolve().parents[3]

cfg = yaml.safe_load(
    (ROOT / 'config/deployment.yaml').read_text()
)

robot = cfg['robot']['base_url'].rstrip('/')
services = cfg['services']
ex_cfg = cfg['execution']

DEFAULT_PLACE_MATCH_RADIUS_M = float(
    cfg.get('acceptance', {}).get(
        'navigation_position_tolerance_m',
        0.5
    )
)


SUPPORTED = {
    'navigate',
    'face_wait',
    'speak',
    'detect_object',
    'grasp',
    'release',
    'wait',
    'vpr_verify',
    'remember_location',
    'remember_visual_location'
}


def request(url, method='GET', body=None, headers=None, timeout=10):

    raw = None if body is None else json.dumps(
        body,
        ensure_ascii=False
    ).encode()

    h = {
        'Accept': 'application/json'
    }

    if raw is not None:
        h['Content-Type'] = 'application/json'

    if headers:
        h.update(headers)

    q = urllib.request.Request(
        url,
        data=raw,
        headers=h,
        method=method
    )

    try:
        with urllib.request.urlopen(q, timeout=timeout) as r:
            t = r.read().decode(errors='replace')
            return json.loads(t) if t else {}

    except urllib.error.HTTPError as e:
        raise RuntimeError(
            f'HTTP {e.code} {url}: '
            + e.read().decode(errors='replace')
        )

    except urllib.error.URLError as e:
        raise RuntimeError(
            f'request failed {url}: {e.reason}'
        )


def first(d, *keys):

    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ''):
            return d[k]


def list_items(d):

    if isinstance(d, list):
        return d

    if isinstance(d, dict):

        for k in (
            'results',
            'items',
            'hits',
            'records'
        ):
            if isinstance(d.get(k), list):
                return d[k]

        if isinstance(d.get('data'), dict):
            return list_items(d['data'])

    return []


def target_pose(hit):

    if not isinstance(hit, dict):
        return None

    for k in (
        'target_pose',
        'place_pose',
        'pose'
    ):

        if isinstance(hit.get(k), dict):
            return hit[k]

    return hit if 'x' in hit and 'y' in hit else None


# ============================================================
# Plan validation
# ============================================================

def validate(plan):

    if (
        not isinstance(plan, dict)
        or not isinstance(plan.get('steps'), list)
        or not plan['steps']
    ):
        raise ValueError(
            'plan.steps must be a non-empty list'
        )


    if len(plan['steps']) > 20:
        raise ValueError(
            'plan has more than 20 steps'
        )


    ids = set()
    worst = 0.0


    for i, s in enumerate(plan['steps']):

        if not isinstance(s, dict):
            raise ValueError(
                f'step {i} is not object'
            )


        sid = s.get('id')
        typ = s.get('type')


        if not isinstance(sid, str) or not sid:
            raise ValueError(
                f'step {i} missing id'
            )


        if sid in ids:
            raise ValueError(
                'duplicate step id: ' + sid
            )


        ids.add(sid)


        if typ not in SUPPORTED:
            raise ValueError(
                f'unsupported step type: {typ}'
            )


        if typ == 'remember_location':
            if not isinstance(
                s.get('location'),
                str
            ) or not s['location']:
                raise ValueError(
                    sid + ' remember_location needs location'
                )


        if typ == 'remember_visual_location':
            place_id = s.get('place_id')
            place_id_from = s.get('place_id_from')

            if not place_id and not place_id_from:
                raise ValueError(
                    sid + ' remember_visual_location needs place_id or place_id_from'
                )

            if place_id is not None and (
                not isinstance(place_id, str)
                or not place_id
            ):
                raise ValueError(
                    sid + ' remember_visual_location place_id invalid'
                )

            if place_id_from is not None and (
                not isinstance(place_id_from, str)
                or place_id_from not in ids
                or place_id_from == sid
            ):
                raise ValueError(
                    sid + ' place_id_from must reference earlier step'
                )

            semantic_note = s.get('semantic_note')
            if semantic_note is not None and not isinstance(
                semantic_note,
                str
            ):
                raise ValueError(
                    sid + ' semantic_note must be string'
                )

            semantic_tags = s.get('semantic_tags')
            if semantic_tags is not None and (
                not isinstance(semantic_tags, list)
                or not all(isinstance(tag, str) for tag in semantic_tags)
            ):
                raise ValueError(
                    sid + ' semantic_tags must be list[string]'
                )


        if (
            typ == 'navigate'
            and not s.get('location')
            and not s.get('semantic_text')
            and not isinstance(
                s.get('target_pose'),
                dict
            )
        ):
            raise ValueError(
                sid + ' navigate needs location, target_pose, or semantic_text'
            )

        if (
            typ == 'navigate'
            and s.get('semantic_text') is not None
            and (
                not isinstance(s.get('semantic_text'), str)
                or not s['semantic_text']
            )
        ):
            raise ValueError(
                sid + ' semantic_text must be non-empty string'
            )

        if (
            typ == 'navigate'
            and float(
                s.get(
                    'place_match_radius_m',
                    DEFAULT_PLACE_MATCH_RADIUS_M
                )
            ) <= 0
        ):
            raise ValueError(
                sid + ' place_match_radius_m must be positive'
            )


        if (
            typ == 'vpr_verify'
            and not s.get('location')
            and not s.get('place_id')
            and not s.get('place_id_from')
        ):
            raise ValueError(
                sid + ' vpr_verify needs location, place_id, or place_id_from'
            )

        if typ == 'vpr_verify' and s.get('place_id_from') is not None:
            place_id_from = s.get('place_id_from')
            if (
                not isinstance(place_id_from, str)
                or place_id_from not in ids
                or place_id_from == sid
            ):
                raise ValueError(
                    sid + ' place_id_from must reference earlier step'
                )


        if typ == 'face_wait':
            if (
                not s.get('target')
                and not s.get('any_known', False)
            ):
                raise ValueError(
                    sid + ' face_wait needs target or any_known'
                )
            try:
                poll_interval = float(s.get('poll_interval_s', 1.0))
            except (TypeError, ValueError):
                raise ValueError(
                    sid + ' poll_interval_s must be a number'
                )
            if poll_interval <= 0:
                raise ValueError(
                    sid + ' poll_interval_s must be positive'
                )


        if typ == 'speak':
            if not isinstance(
                s.get('text'),
                str
            ):
                raise ValueError(
                    sid + ' speak needs text'
                )


        if typ in (
            'detect_object',
            'grasp'
        ) and not s.get('object'):

            raise ValueError(
                sid + ' needs object'
            )

        if typ == 'grasp':
            use_vlac = s.get('use_vlac', False)
            if not isinstance(use_vlac, bool):
                raise ValueError(
                    sid + ' use_vlac must be boolean'
                )
            task_description = s.get('task_description')
            if task_description is not None and (
                not isinstance(task_description, str)
                or not task_description.strip()
            ):
                raise ValueError(
                    sid + ' task_description must be non-empty string'
                )
            try:
                settle_seconds = float(s.get('settle_seconds', 2.0))
            except (TypeError, ValueError):
                raise ValueError(
                    sid + ' settle_seconds must be a number'
                )
            if settle_seconds < 0:
                raise ValueError(
                    sid + ' settle_seconds must be non-negative'
                )


        if typ == 'wait':
            if float(
                s.get('seconds', 0)
            ) < 0:
                raise ValueError(
                    sid + ' wait seconds invalid'
                )


        when = s.get('when')

        if when is not None:

            if (
                not isinstance(when, dict)
                or when.get('step') not in ids
            ):
                raise ValueError(
                    sid + ' when must reference earlier step'
                )


        if typ == 'navigate':

            worst += float(
                s.get(
                    'timeout_s',
                    ex_cfg['navigation_timeout_s']
                )
            )

        elif typ == 'face_wait':

            worst += float(
                s.get(
                    'timeout_s',
                    ex_cfg['face_wait_timeout_s']
                )
            )

        elif typ == 'wait':

            worst += float(
                s.get('seconds', 0)
            )

        elif typ == 'grasp':

            if s.get('use_vlac', False):
                worst += float(
                    s.get(
                        'timeout_s',
                        ex_cfg.get('grasp_vlac_timeout_s', 180)
                    )
                )
            else:
                # Preserve the original generic-step estimate for plain grasp.
                worst += float(s.get('timeout_s', 10))

        else:

            worst += 10


    worst += float(
        ex_cfg.get(
            'code_overhead_s',
            25
        )
    )


    if worst > float(
        ex_cfg['max_plan_runtime_s']
    ):
        raise ValueError(
            f'plan worst-case runtime {worst:.1f}s exceeds limit'
        )


    return worst



# ============================================================
# Resolve Memory
# ============================================================

def resolve_locations(plan):

    base = services[
        'spatial_memory'
    ]['url'].rstrip('/')

    resolved_steps = {}

    for s in plan['steps']:

        if s['type'] == 'navigate':
            if not isinstance(
                s.get('target_pose'),
                dict
            ):
                if s.get('semantic_text'):
                    semantic_query = request(
                        base + '/query/semantic/text',
                        'POST',
                        {
                            'text': s['semantic_text'],
                            'n_results': 5,
                            'memory_type': 'semantic_frame'
                        }
                    )
                    semantic_hits = list_items(semantic_query)
                    if not semantic_hits:
                        raise RuntimeError(
                            'semantic visual memory not found'
                        )

                    semantic_hit = semantic_hits[0]
                    semantic_pose = target_pose(semantic_hit)
                    if not semantic_pose or (
                        semantic_pose.get('x') is None
                        or semantic_pose.get('y') is None
                    ):
                        raise RuntimeError(
                            'semantic visual memory has no target_pose'
                        )

                    semantic_x = float(semantic_pose['x'])
                    semantic_y = float(semantic_pose['y'])
                    radius = float(
                        s.get(
                            'place_match_radius_m',
                            DEFAULT_PLACE_MATCH_RADIUS_M
                        )
                    )
                    position_query = request(
                        base + '/query/position',
                        'POST',
                        {
                            'x': semantic_x,
                            'y': semantic_y,
                            'radius': radius,
                            'n_results': 5,
                            'memory_type': 'place'
                        }
                    )
                    place_hits = []
                    for candidate in list_items(position_query):
                        candidate_pose = target_pose(candidate)
                        if candidate_pose and (
                            candidate_pose.get('x') is not None
                            and candidate_pose.get('y') is not None
                        ):
                            place_hits.append((candidate, candidate_pose))

                    if not place_hits:
                        raise RuntimeError(
                            'no place associated with semantic visual memory'
                        )

                    hit, tp = min(
                        place_hits,
                        key=lambda item: math.hypot(
                            float(item[1]['x']) - semantic_x,
                            float(item[1]['y']) - semantic_y
                        )
                    )
                    place_id = (
                        hit.get('place_id')
                        or hit.get('id')
                    )
                    if not place_id:
                        raise RuntimeError(
                            'nearby place result missing place_id'
                        )

                    s['target_pose'] = tp
                    s['place_id'] = place_id
                    s['semantic_match_id'] = (
                        semantic_hit.get('memory_id')
                        or semantic_hit.get('id')
                    )
                    s['semantic_match_name'] = semantic_hit.get('name')
                    s['semantic_confidence'] = semantic_hit.get('confidence')
                    s['place_match_radius_m'] = radius
                else:
                    name = s['location']
                    q = request(
                        base + '/query/place',
                        'POST',
                        {
                            'name': name,
                            'n_results': 5
                        }
                    )
                    hits = list_items(q)
                    if not hits:
                        raise RuntimeError(
                            'place not found: ' + name
                        )

                    hit = hits[0]
                    tp = target_pose(hit)
                    if not tp:
                        raise RuntimeError(
                            'place has no navigation pose: '
                            + name
                        )

                    s['target_pose'] = tp
                    s['place_id'] = (
                        hit.get('place_id')
                        or hit.get('id')
                    )


        elif s['type'] == 'vpr_verify':
            if s.get('place_id_from') and not s.get('place_id'):
                source = resolved_steps.get(s['place_id_from'])
                place_id = source.get('place_id') if source else None
                if not place_id:
                    raise RuntimeError(
                        'place_id_from result missing place_id: '
                        + s['place_id_from']
                    )
                s['place_id'] = place_id

            if s.get('location') and not s.get('place_id'):
                q = request(
                    base + '/query/place',
                    'POST',
                    {
                        'name': s['location'],
                        'n_results': 5
                    }
                )


                hits = list_items(q)


                if not hits:
                    raise RuntimeError(
                        'place not found: '
                        + s['location']
                    )


                hit = hits[0]


                s['place_id'] = (
                    hit.get('place_id')
                    or hit.get('id')
                )

        resolved_steps[s['id']] = s

    return plan



# ============================================================

def required_services(plan):

    req = set()

    for s in plan['steps']:

        if s['type'] == 'navigate':
            req.add('spatial_memory')


        if s['type'] == 'vpr_verify':
            req.add('spatial_memory')


        if s['type'] == 'face_wait':
            req.add('face')


        if s['type'] in (
            'detect_object',
            'grasp'
        ):
            req.add('yolo')

        if s['type']=='remember_location':
           req.add('spatial_memory')

        if s['type']=='remember_visual_location':
           req.add('spatial_memory')


    return req



def preflight(plan):

    checks = {
        'robot':
        robot + '/health'
    }


    for n in required_services(plan):

        checks[n] = (
            services[n]['url'].rstrip('/')
            + '/health'
        )


    out = {}


    def one(n,u):

        try:

            return n,{
                'ok':True,
                'data':request(u,timeout=5)
            }

        except Exception as e:

            return n,{
                'ok':False,
                'error':str(e)
            }


    with ThreadPoolExecutor(
        max_workers=len(checks)
    ) as pool:

        for f in as_completed(
            [
                pool.submit(one,n,u)
                for n,u in checks.items()
            ]
        ):

            n,v=f.result()
            out[n]=v


    failed=[
        n
        for n,v in out.items()
        if not v['ok']
    ]


    if failed:

        raise RuntimeError(
            'preflight failed: '
            + json.dumps(
                out,
                ensure_ascii=False
            )
        )


    return out



# ============================================================

def build_code(plan):

    plan_literal=json.dumps(
        plan,
        ensure_ascii=False
    )


    return r'''
import json
import math
import time
import cv2

from geometry_msgs.msg import PoseStamped


plan=json.loads(__PLAN__)

results={}
ordered=[]

nav=None
tts_ready=False



def field_value(obj,path):

    cur=obj

    for part in str(path).split('.'):

        if not isinstance(cur,dict):
            return None

        cur=cur.get(part)

    return cur



def condition_ok(cond):

    if not cond:
        return True

    prev=results.get(
        cond.get('step')
    )

    if prev is None:
        return False

    return (
        field_value(
            prev,
            cond.get(
                'field',
                'status'
            )
        )
        ==
        cond.get('equals')
    )



def record(step,status,started,**extra):

    item={
        'id':step['id'],
        'type':step['type'],
        'status':status,
        'duration_s':
        round(
            time.monotonic()-started,
            3
        )
    }

    item.update(extra)

    results[step['id']]=item

    ordered.append(item)

    print(
        'STAGE_JSON='
        +
        json.dumps(
            item,
            ensure_ascii=False,
            default=str
        ),
        flush=True
    )



def make_pose(t):

    p=PoseStamped()

    p.header.frame_id=str(
        t.get('frame_id')
        or 'map'
    )

    p.pose.position.x=float(t['x'])
    p.pose.position.y=float(t['y'])
    p.pose.position.z=float(t.get('z',0))

    quaternion_keys=('qx','qy','qz','qw')

    if all(t.get(k) is not None for k in quaternion_keys):
        p.pose.orientation.x=float(t['qx'])
        p.pose.orientation.y=float(t['qy'])
        p.pose.orientation.z=float(t['qz'])
        p.pose.orientation.w=float(t['qw'])
    else:
        yaw=float(t.get('yaw',0))
        p.pose.orientation.z=math.sin(yaw/2)
        p.pose.orientation.w=math.cos(yaw/2)

    return p



def check_vpr(step):

    rgb,_=camera.get_frame()


    if rgb is None:
        raise RuntimeError(
            "camera frame unavailable"
        )


    path="/tmp/vpr_verify.jpg"


    if not cv2.imwrite(
        path,
        cv2.cvtColor(
            rgb,
            cv2.COLOR_RGB2BGR
        )
    ):
        raise RuntimeError(
            "failed to save VPR verification image"
        )


    result=vpr.search(path)


    if not result:
        raise RuntimeError(
            "empty vpr result"
        )


    return result



def pose_value(current):

    if isinstance(current,dict):
        return current

    stamped=getattr(current,'pose',None)

    if stamped is None:
        raise RuntimeError(
            "current pose unavailable"
        )

    position=stamped.position
    orientation=stamped.orientation

    yaw=math.atan2(
        2.0*(
            orientation.w*orientation.z
            + orientation.x*orientation.y
        ),
        1.0-2.0*(
            orientation.y*orientation.y
            + orientation.z*orientation.z
        )
    )

    return {
        'x':float(position.x),
        'y':float(position.y),
        'z':float(position.z),
        'roll':0.0,
        'pitch':0.0,
        'yaw':yaw,
        'frame_id':str(
            getattr(
                getattr(current,'header',None),
                'frame_id',
                'map'
            )
            or 'map'
        )
    }



fatal=False


for step in plan['steps']:

    started=time.monotonic()


    if fatal:

        record(
            step,
            'skipped',
            started,
            reason='previous_fatal_failure'
        )

        continue


    if not condition_ok(step.get('when')):

        record(
            step,
            'skipped',
            started,
            reason='condition_not_met'
        )

        continue


    try:

        typ=step['type']


        if typ=='navigate':

            if nav is None:
                nav=Nav2Anywhere()


            p=make_pose(
                step['target_pose']
            )


            nav.nav_to_pose(p)


            reached=bool(
                nav.wait_until_reached(
                    timeout_sec=float(
                        step.get(
                            'timeout_s',
                            75
                        )
                    )
                )
            )


            record(
                step,
                'success'
                if reached
                else 'failed',
                started,
                reached=reached,
                location=step.get('location')
            )


            if not reached:
                fatal=True


        elif typ=='face_wait':

            deadline=(
                time.monotonic()
                + float(
                    step.get('timeout_s',30)
                )
            )

            target=step.get('target')
            any_known=bool(
                step.get('any_known',False)
            )
            poll_interval=float(
                step.get('poll_interval_s',1.0)
            )
            matched=None
            last_result=None

            face.start()

            while time.monotonic()<deadline:

                last_result=face.recognize_current_frame()
                matches=(
                    last_result.get('results',[])
                    if isinstance(last_result,dict)
                    else []
                )

                for candidate in matches:
                    name=(
                        candidate.get('name')
                        if isinstance(candidate,dict)
                        else None
                    )
                    if any_known or name==target:
                        matched=candidate
                        break

                if matched is not None:
                    break

                remaining=deadline-time.monotonic()
                if remaining>0:
                    time.sleep(
                        min(poll_interval,remaining)
                    )

            recognized=(matched is not None)

            record(
                step,
                'success'
                if recognized
                else 'partial',
                started,
                recognized=recognized,
                target=target,
                match=matched,
                face_result=last_result
            )


        elif typ=='speak':

            if not tts_ready:
                tts_ready=bool(tts.initialize())

            spoken=(
                bool(tts.speak(step['text']))
                if tts_ready
                else False
            )

            record(
                step,
                'success'
                if spoken
                else 'failed',
                started,
                spoken=spoken,
                text=step['text']
            )

            if not spoken:
                fatal=True


        elif typ=='detect_object':

            rgb,_=camera.get_frame()

            if rgb is None:
                raise RuntimeError(
                    'D455 RGB frame unavailable'
                )

            detections=yolo.detect_on_rgb(rgb)
            target=step['object']
            matched=[]

            for detection in detections:
                if not isinstance(detection,dict):
                    continue
                label=(
                    detection.get('class_name')
                    or detection.get('label')
                    or detection.get('name')
                )
                if str(label)==str(target):
                    matched.append(detection)

            record(
                step,
                'success',
                started,
                found=bool(matched),
                object=target,
                detections=detections,
                matches=matched
            )



        elif typ=='vpr_verify':

            result=check_vpr(step)


            place_id=result.get(
                'place_id'
            )


            expected=step.get(
                'place_id'
            )


            success=(
                place_id is not None
                and
                place_id==expected
            )


            record(
                step,
                'success'
                if success
                else 'failed',
                started,
                vpr_result=result,
                expected_place_id=expected
            )


            if not success:
                fatal=True



        elif typ=='remember_location':

            from robot_sdk.memory_sdk import MemorySDK
            from robot_sdk.navigation_sdk import Nav2Anywhere

            if nav is None:
                nav=Nav2Anywhere()

            pose=pose_value(
                nav.get_current_pose()
            )

            saved=MemorySDK().upsert_place(
                place_name=step['location'],
                robot_id='g1_001',
                robot_type='humanoid',
                place_pose=pose
            )

            place_id=(
                saved.get('place_id')
                or saved.get('id')
            )

            if not place_id:
                raise RuntimeError(
                    "upsert_place result missing place_id"
                )

            record(
                step,
                'success',
                started,
                place_id=place_id,
                pose=pose
            )



        elif typ=='remember_visual_location':

            from robot_sdk.visual_memory_sdk import VisualMemorySDK

            place_id=step.get('place_id')
            robot_pose=None

            if step.get('place_id_from'):
                previous=results[step['place_id_from']]
                place_id=previous['place_id']
                robot_pose=previous.get('pose')

            visual_memory=(
                VisualMemorySDK(
                    camera=camera,
                    vpr=vpr,
                    memory=memory
                )
                .create_visual_memory(
                    place_id=place_id,
                    robot_pose=robot_pose,
                    semantic_note=step.get(
                        'semantic_note',
                        ''
                    ),
                    semantic_tags=step.get(
                        'semantic_tags',
                        []
                    )
                )
            )

            record(
                step,
                'success',
                started,
                place_id=place_id,
                visual_memory=visual_memory
            )



        elif typ=='wait':

            time.sleep(
                float(
                    step.get(
                        'seconds',
                        0
                    )
                )
            )

            record(
                step,
                'success',
                started
            )


        elif typ=='grasp':

            if step.get('use_vlac',False):
                grasp_result=grasp_with_vlac(
                    step['object'],
                    task_description=step.get('task_description'),
                    settle_seconds=float(
                        step.get('settle_seconds',2.0)
                    )
                )

                execution_success=bool(
                    grasp_result.get('execution_success')
                )
                decision=str(
                    grasp_result.get('grasp_decision')
                    or ''
                ).strip().upper()

                if not execution_success:
                    grasp_status='failed'
                elif decision=='REMOVED':
                    grasp_status='success'
                elif decision=='STILL_PRESENT':
                    grasp_status='failed'
                elif decision=='UNCERTAIN':
                    grasp_status='partial'
                elif grasp_result.get('done') is True:
                    grasp_status='success'
                elif grasp_result.get('done') is False:
                    grasp_status='failed'
                else:
                    grasp_status='partial'

                record(
                    step,
                    grasp_status,
                    started,
                    vlac_enabled=True,
                    **grasp_result
                )
            else:
                execution_success=bool(
                    grasp_something(step['object'])
                )
                grasp_status=(
                    'success'
                    if execution_success
                    else 'failed'
                )

                record(
                    step,
                    grasp_status,
                    started,
                    vlac_enabled=False,
                    execution_success=execution_success
                )

            if grasp_status=='failed':
                fatal=True


        elif typ=='release':

            released=bool(release_object())

            record(
                step,
                'success'
                if released
                else 'failed',
                started,
                released=released
            )

            if not released:
                fatal=True


    except Exception as e:

        record(
            step,
            'failed',
            started,
            error=str(e)
        )

        fatal=True



status=(
    'failed'
    if any(
        x['status']=='failed'
        for x in ordered
    )
    else (
        'partial'
        if any(
            x['status']=='partial'
            for x in ordered
        )
        else 'success'
    )
)


print(
    'RESULT_JSON='
    +
    json.dumps(
        {
            'status':status,
            'stages':ordered
        },
        ensure_ascii=False
    ),
    flush=True
)

'''.replace(
    '__PLAN__',
    repr(plan_literal)
)


# 后面 extract_payload/main/finally 保持你原文件即可









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
