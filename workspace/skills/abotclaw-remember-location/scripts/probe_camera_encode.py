#!/usr/bin/env python3
"""Deterministic probe: check D455 frame capture + JPEG encoding availability
on the robot-side /code/execute sandbox. Used to decide how to save a D455
visual reference. This is a read-only capability probe: it does NOT write to
Memory and does NOT move the robot.
"""
from __future__ import annotations
from pathlib import Path
import json, sys, time, urllib.request, urllib.error, yaml

ROOT = Path(__file__).resolve().parents[3]
cfg = yaml.safe_load((ROOT / 'config/deployment.yaml').read_text())
robot = cfg['robot']['base_url'].rstrip('/')
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


CODE = r'''import json, base64, sys
out = {}
# 1) D455 frame availability
try:
    rgb, depth = camera.get_frame()
    out['d455_rgb_shape'] = None if rgb is None else list(rgb.shape)
    out['d455_depth_shape'] = None if depth is None else list(depth.shape)
except Exception as e:
    out['d455_error'] = str(e)
# 2) JPEG encoder availability (cv2)
try:
    import cv2
    out['cv2_version'] = cv2.__version__
    if rgb is not None:
        ok, buf = cv2.imencode('.jpg', cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
        out['jpeg_encode_ok'] = bool(ok)
        out['jpeg_bytes'] = int(len(buf.tobytes())) if ok else 0
except Exception as e:
    out['cv2_error'] = str(e)
print('PROBE_JSON=' + json.dumps(out, ensure_ascii=False, default=str), flush=True)
'''

def main():
    lease_id = None
    try:
        # health preflight before acquiring lease
        h = request(robot + '/health', timeout=5)
        if not h.get('ok') and not h.get('status') == 'ok':
            raise RuntimeError('robot health not ok: ' + repr(h))
        lease = request(robot + '/lease/acquire', 'POST', {'holder': ex_cfg['lease_holder_prefix'] + '-probe'})
        lease_id = first(lease, 'lease_id', 'id', 'lease')
        if not lease_id:
            raise RuntimeError('lease response missing lease id: ' + repr(lease))
        submitted = request(robot + '/code/execute', 'POST', {'code': CODE, 'timeout': 30}, {'X-Lease-Id': lease_id}, 15)
        eid = first(submitted, 'execution_id', 'id', 'task_id', 'run_id')
        if not eid:
            raise RuntimeError('execute response missing id: ' + repr(submitted))
        deadline = time.monotonic() + 40
        result = None
        while time.monotonic() < deadline:
            d = request(robot + '/code/result/' + eid, timeout=10)
            result = extract_payload(d, 'PROBE_JSON=')
            if result is not None:
                break
            state = str(first(d, 'status', 'state', 'execution_status') or '').lower()
            if state in ('failed', 'error', 'timeout', 'timed_out', 'cancelled'):
                raise RuntimeError('remote execution failed: ' + repr(d))
            time.sleep(float(ex_cfg['result_poll_interval_s']))
        if result is None:
            raise RuntimeError('probe result timeout (stdout may not contain PROBE_JSON)')
        print(json.dumps({'status': 'success', 'probe': result, 'execution_id': eid}, ensure_ascii=False, indent=2))
        return 0
    except Exception as e:
        print(json.dumps({'status': 'failed', 'error': str(e)}, ensure_ascii=False, indent=2))
        return 1
    finally:
        if lease_id:
            try:
                request(robot + '/lease/release', 'POST', {'lease_id': lease_id}, timeout=5)
            except Exception as e:
                print(json.dumps({'warning': 'lease release failed', 'error': str(e)}, ensure_ascii=False), file=sys.stderr)


if __name__ == '__main__':
    raise SystemExit(main())
