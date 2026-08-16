#!/usr/bin/env python3
"""Submit fixed-pose, single-frame visual-memory creation to the robot.

The on-robot script captures one D455 RGB frame without moving the robot and
reuses that JPEG for SpatialMemory Semantic Frame ingest and VPR indexing.
"""
from __future__ import annotations
from pathlib import Path
import argparse, json, sys, time, urllib.request, urllib.error, yaml

ROOT = Path(__file__).resolve().parents[3]
cfg = yaml.safe_load((ROOT / 'config/deployment.yaml').read_text())
robot = cfg['robot']['base_url'].rstrip('/')
ex_cfg = cfg['execution']
VM_SCRIPT = Path(__file__).resolve().parent / 'remember_visual_location.py'


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


def extract_result(d):
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
            if line.startswith('VISUAL_MEMORY_JSON='):
                try:
                    return json.loads(line.split('=', 1)[1])
                except Exception:
                    pass
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--place-id', required=True)
    ap.add_argument('--semantic-note', default='')
    ap.add_argument('--semantic-tag', action='append', default=[], dest='semantic_tags')
    a = ap.parse_args()

    # Deterministic on-robot script body (import robot_sdk on the robot side).
    # The script ships with its own `if __name__ == "__main__":` argparse block;
    # running that block here would fail with a missing --place-id. Strip it and
    # append a clean entry that calls main(place_id) directly.
    src = VM_SCRIPT.read_text(encoding='utf-8')
    marker = 'if __name__ == "__main__":'
    idx = src.find(marker)
    if idx != -1:
        src = src[:idx]
    code = (
        src
        + '\n\nif __name__ == "__main__":\n'
        + '    r = main(\n'
        + f'        {json.dumps(a.place_id)},\n'
        + f'        semantic_note={json.dumps(a.semantic_note)},\n'
        + f'        semantic_tags={json.dumps(a.semantic_tags, ensure_ascii=False)},\n'
        + '    )\n'
        + '    import json as _j\n'
        + '    print("VISUAL_MEMORY_JSON=" + _j.dumps(r, ensure_ascii=False, default=str), flush=True)\n'
    )

    lease_id = None
    started = time.monotonic()
    try:
        request(robot + '/health', timeout=5)
        lease = request(robot + '/lease/acquire', 'POST', {'holder': ex_cfg['lease_holder_prefix'] + '-vmem'})
        lease_id = first(lease, 'lease_id', 'id', 'lease')
        if not lease_id:
            raise RuntimeError('lease response missing lease id: ' + repr(lease))

        timeout_s = 60.0
        submitted = request(robot + '/code/execute', 'POST', {'code': code, 'timeout': timeout_s}, {'X-Lease-Id': lease_id}, 15)
        eid = first(submitted, 'execution_id', 'id', 'task_id', 'run_id')
        if not eid:
            raise RuntimeError('execute response missing id: ' + repr(submitted))

        deadline = time.monotonic() + timeout_s + 30
        result = None
        while time.monotonic() < deadline:
            d = request(robot + '/code/result/' + eid, timeout=10)
            result = extract_result(d)
            if result is not None:
                break
            state = str(first(d, 'status', 'state', 'execution_status') or '').lower()
            if state in ('failed', 'error', 'timeout', 'timed_out', 'cancelled'):
                raise RuntimeError('remote visual memory failed: ' + repr(d))
            time.sleep(float(ex_cfg['result_poll_interval_s']))
        if result is None:
            raise RuntimeError('visual memory result timeout')

        out = {
            'status': 'success',
            'place_id': a.place_id,
            'result': result,
            'duration_s': round(time.monotonic() - started, 2),
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0
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
