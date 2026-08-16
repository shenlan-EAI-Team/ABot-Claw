#!/usr/bin/env python3
"""Call the live VLAC HTTP endpoints with user-provided images."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any, Dict

import requests


def image_to_base64(path_value: str) -> str:
    path = Path(path_value)
    if not path.is_file():
        raise FileNotFoundError(f"Image does not exist: {path}")
    data = path.read_bytes()
    if not data:
        raise ValueError(f"Image is empty: {path}")
    return base64.b64encode(data).decode("ascii")


def call_json(method: str, url: str, timeout: float, **kwargs: Any) -> Dict[str, Any]:
    response = requests.request(method, url, timeout=timeout, **kwargs)
    print(f"{method} {url} -> HTTP {response.status_code}")
    try:
        body: Any = response.json()
        print(json.dumps(body, ensure_ascii=False, indent=2))
    except ValueError:
        body = response.text
        print(body)
    if not response.ok:
        raise RuntimeError(f"Request failed with HTTP {response.status_code}")
    if not isinstance(body, dict):
        raise RuntimeError("Expected a JSON object response")
    return body


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8014")
    parser.add_argument("--navigation-reference", required=True)
    parser.add_argument("--navigation-current", required=True)
    parser.add_argument("--grasp-before", required=True)
    parser.add_argument("--grasp-after", required=True)
    parser.add_argument("--target-label", required=True)
    parser.add_argument("--navigation-threshold", type=float, default=0.8)
    parser.add_argument("--grasp-threshold", type=float, default=35.0)
    parser.add_argument("--timeout", type=float, default=180.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base_url = args.base_url.rstrip("/")
    try:
        call_json("GET", f"{base_url}/health", args.timeout)
        call_json(
            "POST",
            f"{base_url}/navigation/verify",
            args.timeout,
            json={
                "current_image": image_to_base64(args.navigation_current),
                "reference_image": image_to_base64(args.navigation_reference),
                "done_threshold": args.navigation_threshold,
                "rich": True,
            },
        )
        call_json(
            "POST",
            f"{base_url}/grasp/verify",
            args.timeout,
            json={
                "before_image": image_to_base64(args.grasp_before),
                "after_image": image_to_base64(args.grasp_after),
                "target_label": args.target_label,
                "completion_threshold": args.grasp_threshold,
                "rich": False,
            },
        )
    except (OSError, ValueError, requests.RequestException, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
