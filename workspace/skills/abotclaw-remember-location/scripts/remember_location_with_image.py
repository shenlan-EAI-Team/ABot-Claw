#!/usr/bin/env python3
"""Record the current G1 map pose under a semantic place name AND attach a D455
visual reference image to the Spatial Memory place record.

- D455 frame: captured on the robot via /code/execute (camera.get_frame()),
  JPEG-encoded and returned base64 to the host.
- Pose: read host-side from /nav/current_pose (no robot motion, no lease needed
  for the pose itself).
- Memory upsert: uses the /memory/place/upsert image field (base64 data-uri) +
  image_captured_at.

Deterministic; does not move the robot.
"""
from __future__ import annotations
from pathlib import Path
import argparse, base64, json, sys, time, urllib.request, urllib.error, yaml

ROOT = Path(__file__).resolve().parents[3]
cfg = yaml.safe_load((ROOT / 'config/deployment.yaml').read_text())
robot = cfg['robot']['base_url'].rstrip('/')
mem = cfg['services']['spatial_memory']['url'].rstrip('/')
ex_cfg = cfg['execution']


def request(url, method='GET', body=None, headers=None, timeout=10):
    raw = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    h = {'Accept': 'application/json'}
    if raw is not None:
        h['Content-Type'] = 'application/json'
    if headers:
        h.update(headers)
    q = urllib.request.Request(url, data=raw, headers=h, method=method)
    try:
        with urllib.request.urlopen(q, timeout=timeout) as r:
            t = r.read().decode(errors='replace')
            return json.loads(t) if t else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f'HTTP {e.code} {url}: ' + e.read().decode(errors='replace'))
    except urllib.error.URLError as e:
        raise RuntimeError(f'request failed {url}: {e.reason}')


def first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ''):
            return d[k]


def extract_payload(d, prefix):
    texts = []
    def walk(v):
        if isinstance(v, dict):
            for k, x in v.items():
                if k in ('stdout', 'output', 'logs', 'raw', 'stderr') and isinstance(x, str):
                    texts.append(x)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(d)
    for t in texts:
        for line in t.splitlines():
            if line.strip().startswith(prefix):
                try:
                    return json.loads(line.strip()[len(prefix):])
                except Exception:
                    pass
    return None


def current_pose():
    raw = request(robot + '/nav/current_pose', timeout=10)
    # /nav/current_pose returns a navigation/pose frame
    return raw


CAPTURE_CODE = r'''import json, base64
out = {}
rgb, depth = camera.get_frame()
if rgb is None:
    out['ok'] = False
    out['error'] = 'd455 frame is None'
else:
    import cv2
    ok, buf = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    if not ok:
        out['ok'] = False
        out['error'] = 'jpeg encode failed'
    else:
        out['ok'] = True
        out['width'] = int(rgb.shape[1])
        out['height'] = int(rgb.shape[0])
        out['jpeg_b64'] = base64.b64encode(buf.tobytes()).decode('ascii')
print('CAPTURE_JSON=' + json.dumps(out, ensure_ascii=False, default=str), flush=True)
'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--name', required=True)
    ap.add_argument('--note', default='')
    a = ap.parse_args()

    lease_id = None
    started = time.monotonic()
    try:
        # Preflight before acquiring lease (per execution rules).
        request(robot + '/health', timeout=5)
        request(mem + '/health', timeout=5)

        lease = request(robot + '/lease/acquire', 'POST', {'holder': ex_cfg['lease_holder_prefix'] + '-img'})
        lease_id = first(lease, 'lease_id', 'id', 'lease')
        if not lease_id:
            raise RuntimeError('lease response missing lease id: ' + repr(lease))

        submitted = request(robot + '/code/execute', 'POST', {'code': CAPTURE_CODE, 'timeout': 30}, {'X-Lease-Id': lease_id}, 15)
        eid = first(submitted, 'execution_id', 'id', 'task_id', 'run_id')
        if not eid:
            raise RuntimeError('execute response missing id: ' + repr(submitted))

        deadline = time.monotonic() + 40
        cap = None
        while time.monotonic() < deadline:
            d = request(robot + '/code/result/' + eid, timeout=10)
            cap = extract_payload(d, 'CAPTURE_JSON=')
            if cap is not None:
                break
            state = str(first(d, 'status', 'state', 'execution_status') or '').lower()
            if state in ('failed', 'error', 'timeout', 'timed_out', 'cancelled'):
                raise RuntimeError('remote capture failed: ' + repr(d))
            time.sleep(float(ex_cfg['result_poll_interval_s']))
        if cap is None or not cap.get('ok'):
            raise RuntimeError('D455 capture failed: ' + repr(cap))

        # Pose + timestamp host-side.
        pose = current_pose()
        captured_at = time.time()

        data_uri = 'data:image/jpeg;base64,' + cap['jpeg_b64']
        body = {
            'place_name': a.name.strip(),
            'robot_id': cfg['robot']['id'],
            'robot_type': 'humanoid',
            'place_pose': pose,
            'note': a.note,
            'image': data_uri,
            'image_captured_at': captured_at,
            'task_description': 'D455 现场视觉参考（保存视觉特征）',
        }
        saved = request(mem + '/memory/place/upsert', 'POST', body, timeout=25)

        out = {
            'status': 'success',
            'location_name': a.name,
            'image': {'width': cap['width'], 'height': cap['height'], 'jpeg_bytes': len(cap['jpeg_b64']) * 3 // 4},
            'image_captured_at': captured_at,
            'pose_raw': pose,
            'memory': saved,
            'duration_s': round(time.monotonic() - started, 2),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if saved.get('ok') else 1
    except Exception as e:
        print(json.dumps({'status': 'failed', 'error': str(e), 'duration_s': round(time.monotonic() - started, 2)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        if lease_id:
            try:
                request(robot + '/lease/release', 'POST', {'lease_id': lease_id}, timeout=5)
            except Exception as e:
                print(json.dumps({'warning': 'lease release failed', 'error': str(e)}, ensure_ascii=False), file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
