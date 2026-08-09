import base64
import json
from pathlib import Path

import requests

URL = "http://127.0.0.1:8016/face/recognize"
TEST_IMAGE = "test.jpg"


def image_to_b64(image_path: str) -> str:
    raw = Path(image_path).read_bytes()
    return base64.b64encode(raw).decode("utf-8")


def test_recognize() -> None:
    path = Path(TEST_IMAGE)
    if not path.exists():
        print(f"Test image not found: {path}. Skipping request.")
        return

    payload = {
        "image": image_to_b64(str(path)),
        "threshold": 0.45,
        "include_annotated_image": False,
    }

    response = requests.post(URL, json=payload, timeout=60)
    response.raise_for_status()
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    test_recognize()
