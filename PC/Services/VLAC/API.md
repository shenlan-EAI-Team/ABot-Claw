# VLAC HTTP API

VLAC is a stateless visual-evaluation service. It does not access spatial memory,
control a robot, acquire a lease, capture camera images, or decide retries.

## Start the service

Standalone:

```bash
cd /home/sl/abotclaw_sl/PC/Services/VLAC
PORT=8014 DEVICE=auto VLAC_MODEL_PATH="$PWD/models" .venv/bin/python main.py
```

All-in-one container:

```bash
cd /home/sl/abotclaw_sl/PC/Services
docker compose up -d
```

Optional initial business thresholds:

```bash
export VLAC_NAVIGATION_DONE_THRESHOLD=0.8
export VLAC_GRASP_PRESENCE_THRESHOLD=0.35
export VLAC_GRASP_VISIBILITY_THRESHOLD=0.35
```

These thresholds must be calibrated separately with real navigation and grasp
samples. VLAC scores are not calibrated probabilities.

## Image inputs

Every HTTP image field is a string accepted by the existing parser:

- raw Base64;
- a `data:image/...;base64,...` URI;
- an HTTP or HTTPS URL;
- a local path visible to the VLAC server process.

The parser returns an RGB `PIL.Image`. Image bytes and complete Base64 strings
are never logged.

## `GET /health`

Returns the existing health fields plus a backward-compatible `capabilities`
list.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "device": "cuda:0",
  "model_type": "internvl2",
  "model_path": "/services/VLAC/models",
  "model_loaded": true,
  "capabilities": ["critic", "navigation_verify", "grasp_verify"]
}
```

## `POST /critic` (existing)

This endpoint is unchanged. It evaluates directional progress from
`reference_image` (Image-1) to `image` (Image-2).

```json
{
  "image": "<base64-or-path-or-url>",
  "reference_image": "<base64-or-path-or-url>",
  "task_description": "Scoop the rice into the rice cooker.",
  "batch_num": 1,
  "rich": false
}
```

## `POST /navigation/verify`

Uses `GAC_model.get_trajectory_done()` with:

```python
image_list=[current_image]
goal_image=reference_image
ref_image_list=None
```

The images enter the model in `[reference_image, current_image]` order.

Request:

```json
{
  "current_image": "<base64-or-path-or-url>",
  "reference_image": "<base64-or-path-or-url>",
  "done_threshold": 0.8,
  "rich": true
}
```

Response:

```json
{
  "mode": "navigation_arrival",
  "method": "get_trajectory_done",
  "done_score": 0.91,
  "visual_done": true,
  "done_threshold": 0.8,
  "score_in_expected_range": true,
  "effective_task_description": "...",
  "raw_result": [0.91],
  "warning": null,
  "latency_ms": 312.5
}
```

The score is not clamped or normalized. If it is outside `[0, 1]`,
`visual_done` is forced to `false` and `warning` is populated. The orchestration
layer, not VLAC, should combine evidence as:

```python
navigation_arrived = pose_ok and visual_done
```

## `POST /grasp/verify`

Uses two `GAC_model.get_trajectory_done()` calls on the final image only. The
first asks whether the target remains on the table; the second asks whether the
table target area is sufficiently visible. Both calls use:

```python
image_list=[after_image]
goal_image=None
apply_threshold=False
```

`before_image` remains required and is decoded for audit purposes, but it is
not passed to the model and is never treated as a goal image.

Request:

```json
{
  "before_image": "<base64-or-path-or-url>",
  "after_image": "<base64-or-path-or-url>",
  "target_label": "bottle",
  "rich": false
}
```

Response:

```json
{
  "mode": "grasp_removal",
  "method": "trajectory_done_presence",
  "target_label": "bottle",
  "target_present_score": 0.0,
  "presence_threshold": 0.35,
  "table_visible_score": 1.0,
  "visibility_threshold": 0.35,
  "decision": "REMOVED",
  "removal_confirmed": true,
  "evidence_status": "REMOVAL_CONFIRMED",
  "warning": null
}
```

The decision is `STILL_PRESENT` when target presence reaches its threshold,
`REMOVED` only when target presence is low and table visibility reaches its
threshold, and `UNCERTAIN` otherwise.

## Error semantics

- `400 INPUT_ERROR`: invalid image or business parameter.
- `422`: Pydantic request-schema validation failure.
- `503 MODEL_UNAVAILABLE`: the shared model has not loaded.
- `500 INFERENCE_ERROR`: GPU/model inference failed.
- `502 PROTOCOL_ERROR`: the model returned a missing, empty, nonnumeric, or
  non-finite result.

Structured service errors have this form:

```json
{
  "error_type": "PROTOCOL_ERROR",
  "detail": "done_result cannot be empty",
  "mode": "navigation_arrival"
}
```

A valid score below its business threshold is a normal HTTP 200 visual result,
not a service or protocol error.

## Live integration test

```bash
cd /home/sl/abotclaw_sl/PC/Services/VLAC
.venv/bin/python scripts/test_vlac_endpoints.py \
  --base-url http://127.0.0.1:8014 \
  --navigation-reference /path/place_reference.jpg \
  --navigation-current /path/place_current.jpg \
  --grasp-before /path/grasp_before.jpg \
  --grasp-after /path/grasp_after.jpg \
  --target-label bottle
```
