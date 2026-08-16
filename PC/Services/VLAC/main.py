"""VLAC Critic Service.

FastAPI backend that receives one image + one reference image and returns
critic results from VLAC.
"""

from __future__ import annotations

import base64
import io
import logging
import math
import os
import time
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

import requests
import uvicorn
import torch
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from PIL import Image
from pydantic import BaseModel, Field

from evo_vlac import GAC_model


SERVICE_VERSION = "0.1.0"
MODEL_PATH = os.getenv("VLAC_MODEL_PATH", "./models")
MODEL_TYPE = os.getenv("VLAC_MODEL_TYPE", "internvl2")

# Initial business thresholds only. They must be calibrated independently
# with real navigation and grasp success/failure samples; neither is a
# universal model probability threshold.
NAVIGATION_DONE_THRESHOLD = float(os.getenv("VLAC_NAVIGATION_DONE_THRESHOLD", "0.8"))
GRASP_PRESENCE_THRESHOLD = float(os.getenv("VLAC_GRASP_PRESENCE_THRESHOLD", "0.35"))
GRASP_VISIBILITY_THRESHOLD = float(os.getenv("VLAC_GRASP_VISIBILITY_THRESHOLD", "0.35"))
GRASP_HOLDING_THRESHOLD = float(
    os.getenv("VLAC_GRASP_HOLDING_THRESHOLD", "0.5")
)

NAVIGATION_MODE = "navigation_arrival"
GRASP_MODE = "grasp_removal"
GRASP_HOLDING_MODE = "grasp_holding"
LOGGER = logging.getLogger("vlac.service")

DEFAULT_NAVIGATION_TASK = """参考图片表示机器人需要到达的目标地点，
当前图片表示机器人导航结束后的视野。

判断当前图片是否已经到达参考图片所示的地点。
主要比较固定建筑结构、固定地标及其相对空间布局。
忽略行人、临时物体、屏幕内容、轻微光照和曝光变化，
以及小范围的相机位置、朝向、裁剪和视角差异。

当固定场景结构及其相对布局与参考图片一致时，
认为机器人已经到达目标地点。"""

GRASP_PRESENCE_TASK = "当前最终图像中，{target_label} 是否仍然位于桌面上？"
GRASP_VISIBILITY_TASK = (
    "当前最终图像中是否存在桌子？"
)
GRASP_DONE_TASK = (
    "机器人成功抓取桌面上的{target_label}，"
    "使{target_label}离开桌面并被机械手稳定抓住。"
)
GRASP_HOLDING_TASK = """
观察当前图像，只判断机器人手爪是否已经夹住{target_label}。

如果{target_label}位于机器人手爪的两个手指之间，
并且两个手指已经合拢在{target_label}两侧形成夹持，
则认为已经夹住{target_label}。

不要根据{target_label}是否离开桌面进行判断。
即使{target_label}仍接触或靠近桌面，只要两个手指已经明确夹在其两侧，
也认为已经夹住。

只有在手爪没有形成对{target_label}的夹持关系时，才认为没有夹住。
"""


def _resolve_vlac_device(env_value: Optional[str]) -> str:
    value = (env_value or "auto").strip().lower()
    if value == "auto":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if value == "cpu":
        return "cpu"
    if value == "cuda":
        return "cuda:0" if torch.cuda.is_available() else "cpu"
    if value.isdigit():
        return f"cuda:{value}" if torch.cuda.is_available() else "cpu"
    if value.startswith("cuda:"):
        return value if torch.cuda.is_available() else "cpu"
    return value


DEVICE_MAP = _resolve_vlac_device(os.getenv("DEVICE") or os.getenv("VLAC_DEVICE"))

CRITIC: Optional[GAC_model] = None
INFER_LOCK = threading.Lock()


class CriticRequest(BaseModel):
    image: str = Field(..., description="Current image (base64/data-uri/path/url)")
    reference_image: str = Field(..., description="Reference image (base64/data-uri/path/url)")
    task_description: str = Field(..., description="Task description for critic evaluation")
    batch_num: int = Field(1, ge=1, le=32, description="Batch size for critic generation")
    rich: bool = Field(False, description="Whether to return decimal-rich outputs")


class CriticResponse(BaseModel):
    critic_list: List[float]
    value_list: List[float]
    latency_ms: float


class NavigationVerifyRequest(BaseModel):
    current_image: str
    reference_image: str
    task_description: Optional[str] = None
    done_threshold: Optional[float] = None
    rich: bool = True


class NavigationVerifyResponse(BaseModel):
    mode: str
    method: str
    done_score: float
    visual_done: bool
    done_threshold: float
    score_in_expected_range: bool
    effective_task_description: str
    raw_result: Any
    warning: Optional[str]
    latency_ms: float


class GraspVerifyRequest(BaseModel):
    before_image: str
    after_image: str
    target_label: str
    task_description: Optional[str] = None
    done_threshold: Optional[float] = None
    rich: bool = False


class GraspVerifyResponse(BaseModel):
    mode: str
    method: str
    target_label: str
    target_present_score: float
    presence_threshold: float
    table_visible_score: float
    visibility_threshold: float
    decision: Literal["STILL_PRESENT", "REMOVED", "UNCERTAIN"]
    removal_confirmed: bool
    evidence_status: Literal[
        "TARGET_STILL_PRESENT", "REMOVAL_CONFIRMED", "VISUAL_EVIDENCE_UNCERTAIN"
    ]
    warning: Optional[str]


class GraspDoneRequest(BaseModel):
    after_image: str
    target_label: str
    done_threshold: Optional[float] = None
    rich: bool = False


class GraspDoneResponse(BaseModel):
    mode: str
    method: str
    target_label: str
    effective_task_description: str
    done_score: float
    done_threshold: float
    grasp_success: bool
    raw_result: Any
    warning: Optional[str]
    latency_ms: float


class GraspHoldingRequest(BaseModel):
    after_image: str
    target_label: str
    holding_threshold: Optional[float] = None
    rich: bool = False


class GraspHoldingResponse(BaseModel):
    mode: str
    method: str
    target_label: str
    effective_task_description: str
    holding_score: float
    holding_threshold: float
    grasp_success: bool
    raw_result: Any
    warning: Optional[str]
    latency_ms: float


class ServiceError(Exception):
    def __init__(self, status_code: int, error_type: str, detail: str, mode: str):
        self.status_code = status_code
        self.error_type = error_type
        self.detail = detail
        self.mode = mode
        super().__init__(detail)


def _normalize_image_input(image_input: str) -> Image.Image:
    if not isinstance(image_input, str) or not image_input.strip():
        raise HTTPException(status_code=400, detail="Image input cannot be empty")

    payload = image_input.strip()
    try:
        if payload.startswith(("http://", "https://")):
            response = requests.get(payload, timeout=15)
            response.raise_for_status()
            if not response.content:
                raise ValueError("Downloaded image is empty")
            image = Image.open(io.BytesIO(response.content))
        else:
            if payload.startswith("data:image"):
                if "," not in payload:
                    raise ValueError("Malformed image data URI")
                payload = payload.split(",", 1)[1]
            try:
                image_bytes = base64.b64decode(payload, validate=True)
                if not image_bytes:
                    raise ValueError("Decoded image is empty")
                image = Image.open(io.BytesIO(image_bytes))
            except Exception as base64_exc:
                path = Path(payload)
                if path.exists() and path.is_file():
                    image = Image.open(path)
                else:
                    raise ValueError(
                        "Image is neither valid Base64 nor an existing file path"
                    ) from base64_exc

        image.load()
        if image.width <= 0 or image.height <= 0:
            raise ValueError("Image dimensions must be positive")
        return image.convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image payload: {exc}")


def _effective_text(value: Optional[str], default: str) -> str:
    return value.strip() if value and value.strip() else default


def _finite_threshold(
    value: Optional[float], default: float, field_name: str, mode: str, *, unit_interval: bool = False
) -> float:
    threshold = default if value is None else float(value)
    if not math.isfinite(threshold):
        raise ServiceError(400, "INPUT_ERROR", f"{field_name} must be finite", mode)
    if unit_interval and not 0.0 <= threshold <= 1.0:
        raise ServiceError(400, "INPUT_ERROR", f"{field_name} must be between 0 and 1", mode)
    return threshold


def _read_image(value: str, field_name: str, mode: str) -> Image.Image:
    try:
        return _normalize_image_input(value)
    except HTTPException as exc:
        raise ServiceError(400, "INPUT_ERROR", f"{field_name}: {exc.detail}", mode) from exc


def _image_metadata(image: Image.Image) -> Dict[str, Any]:
    return {
        "format": image.format,
        "width": image.width,
        "height": image.height,
        "mode": image.mode,
    }


def _float_list(value: Any, field_name: str, mode: str, *, nonempty: bool = True) -> List[float]:
    if not isinstance(value, (list, tuple)):
        raise ServiceError(502, "PROTOCOL_ERROR", f"{field_name} must be a list", mode)
    if nonempty and not value:
        raise ServiceError(502, "PROTOCOL_ERROR", f"{field_name} cannot be empty", mode)
    result: List[float] = []
    for index, item in enumerate(value):
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise ServiceError(
                502, "PROTOCOL_ERROR", f"{field_name}[{index}] is not numeric", mode
            ) from exc
        if not math.isfinite(number):
            raise ServiceError(
                502, "PROTOCOL_ERROR", f"{field_name}[{index}] must be finite", mode
            )
        result.append(number)
    return result


def _validate_grasp_result_consistency(
    *,
    target_present_score: float,
    presence_threshold: float,
    table_visible_score: float,
    visibility_threshold: float,
    decision: str,
    removal_confirmed: bool,
    evidence_status: str,
) -> None:
    if target_present_score >= presence_threshold:
        expected = ("STILL_PRESENT", False, "TARGET_STILL_PRESENT")
    elif table_visible_score >= visibility_threshold:
        expected = ("REMOVED", True, "REMOVAL_CONFIRMED")
    else:
        expected = ("UNCERTAIN", False, "VISUAL_EVIDENCE_UNCERTAIN")
    if (decision, removal_confirmed, evidence_status) != expected:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            "grasp decision fields are inconsistent with presence and visibility scores",
            GRASP_MODE,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CRITIC
    critic = GAC_model(tag="critic")
    critic.init_model(model_path=MODEL_PATH, model_type=MODEL_TYPE, device_map=DEVICE_MAP)
    critic.temperature = 0.5
    critic.top_k = 1
    critic.set_config()
    critic.set_system_prompt()
    CRITIC = critic
    yield
    CRITIC = None


app = FastAPI(
    title="AbotClaw VLAC Critic Service",
    version=SERVICE_VERSION,
    description="Run VLAC pair-wise critic on one image and one reference image.",
    lifespan=lifespan,
)


@app.exception_handler(ServiceError)
async def service_error_handler(request: Request, exc: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error_type": exc.error_type, "detail": exc.detail, "mode": exc.mode},
    )


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "version": SERVICE_VERSION,
        "device": DEVICE_MAP,
        "model_type": MODEL_TYPE,
        "model_path": MODEL_PATH,
        "model_loaded": CRITIC is not None,
        "capabilities": ["critic", "navigation_verify", "grasp_verify", "grasp_holding"],
    }


@app.post("/critic", response_model=CriticResponse)
def critic(req: CriticRequest) -> CriticResponse:
    if CRITIC is None:
        raise HTTPException(status_code=503, detail="Model not initialized")
    if not req.task_description.strip():
        raise HTTPException(status_code=400, detail="task_description cannot be empty")

    start_t = time.time()
    try:
        current_image = _normalize_image_input(req.image)
        reference_image = _normalize_image_input(req.reference_image)
        with INFER_LOCK:
            critic_list, value_list = CRITIC.get_trajectory_critic(
                task=req.task_description,
                image_list=[reference_image, current_image],
                ref_image_list=None,
                batch_num=req.batch_num,
                rich=req.rich,
                reverse_eval=False,
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Critic inference failed: {exc}")

    latency_ms = (time.time() - start_t) * 1000.0

    try:
        critic_values = [float(item) for item in critic_list]
        value_values = [float(item) for item in value_list]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Result parsing failed: {exc}")

    return CriticResponse(
        critic_list=critic_values,
        value_list=value_values,
        latency_ms=latency_ms,
    )


@app.post("/navigation/verify", response_model=NavigationVerifyResponse)
def navigation_verify(req: NavigationVerifyRequest) -> NavigationVerifyResponse:
    if CRITIC is None:
        raise ServiceError(503, "MODEL_UNAVAILABLE", "VLAC model is not initialized", NAVIGATION_MODE)

    threshold = _finite_threshold(
        req.done_threshold,
        NAVIGATION_DONE_THRESHOLD,
        "done_threshold",
        NAVIGATION_MODE,
        unit_interval=True,
    )
    effective_task = _effective_text(req.task_description, DEFAULT_NAVIGATION_TASK)
    current_image = _read_image(req.current_image, "current_image", NAVIGATION_MODE)
    reference_image = _read_image(req.reference_image, "reference_image", NAVIGATION_MODE)

    LOGGER.info(
        "VLAC request mode=%s task_description=%r reference_image=%s current_image=%s "
        "method=get_trajectory_done params=%s",
        NAVIGATION_MODE,
        effective_task,
        _image_metadata(reference_image),
        _image_metadata(current_image),
        {
            "image_list": ["current_image"],
            "ref_image_list": None,
            "batch_num": 1,
            "rich": req.rich,
            "threshold": 0.0,
            "skip": 1,
            "goal_image": "reference_image",
        },
    )

    start_t = time.time()
    try:
        with INFER_LOCK:
            raw_result = CRITIC.get_trajectory_done(
                task=effective_task,
                image_list=[current_image],
                ref_image_list=None,
                batch_num=1,
                rich=req.rich,
                threshold=0.0,
                skip=1,
                goal_image=reference_image,
            )
    except Exception as exc:
        LOGGER.exception("VLAC inference failed mode=%s method=get_trajectory_done", NAVIGATION_MODE)
        raise ServiceError(500, "INFERENCE_ERROR", f"VLAC done inference failed: {exc}", NAVIGATION_MODE) from exc

    latency_ms = (time.time() - start_t) * 1000.0
    done_values = _float_list(raw_result, "done_result", NAVIGATION_MODE)
    if len(done_values) != 1:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            f"done_result must contain exactly one value, got {len(done_values)}",
            NAVIGATION_MODE,
        )
    done_score = done_values[0]
    score_in_expected_range = 0.0 <= done_score <= 1.0
    warning = None
    if not score_in_expected_range:
        visual_done = False
        warning = "done_score is outside the expected [0, 1] range; visual_done forced to false"
    else:
        visual_done = done_score > threshold

    LOGGER.info(
        "VLAC result mode=%s raw_result=%r done_score=%s visual_done=%s latency_ms=%.3f",
        NAVIGATION_MODE,
        raw_result,
        done_score,
        visual_done,
        latency_ms,
    )
    return NavigationVerifyResponse(
        mode=NAVIGATION_MODE,
        method="get_trajectory_done",
        done_score=done_score,
        visual_done=visual_done,
        done_threshold=threshold,
        score_in_expected_range=score_in_expected_range,
        effective_task_description=effective_task,
        raw_result=raw_result,
        warning=warning,
        latency_ms=latency_ms,
    )


@app.post("/grasp/verify", response_model=GraspVerifyResponse)
def grasp_verify(req: GraspVerifyRequest) -> GraspVerifyResponse:
    if CRITIC is None:
        raise ServiceError(503, "MODEL_UNAVAILABLE", "VLAC model is not initialized", GRASP_MODE)
    target_label = req.target_label.strip()
    if not target_label:
        raise ServiceError(400, "INPUT_ERROR", "target_label cannot be empty", GRASP_MODE)

    presence_threshold = _finite_threshold(
        None,
        GRASP_PRESENCE_THRESHOLD,
        "presence_threshold",
        GRASP_MODE,
        unit_interval=True,
    )
    visibility_threshold = _finite_threshold(
        None,
        GRASP_VISIBILITY_THRESHOLD,
        "visibility_threshold",
        GRASP_MODE,
        unit_interval=True,
    )
    presence_task = GRASP_PRESENCE_TASK.format(target_label=target_label)
    visibility_task = GRASP_VISIBILITY_TASK
    before_image = _read_image(req.before_image, "before_image", GRASP_MODE)
    after_image = _read_image(req.after_image, "after_image", GRASP_MODE)

    LOGGER.info(
        "VLAC request mode=%s target_label=%r before_image=%s after_image=%s "
        "method=get_trajectory_done queries=%s",
        GRASP_MODE,
        target_label,
        _image_metadata(before_image),
        _image_metadata(after_image),
        [
            {
                "name": "target_presence",
                "task": presence_task,
                "image_list": ["after_image"],
                "goal_image": None,
                "apply_threshold": False,
            },
            {
                "name": "table_visibility",
                "task": visibility_task,
                "image_list": ["after_image"],
                "goal_image": None,
                "apply_threshold": False,
            },
        ],
    )

    start_t = time.time()
    try:
        with INFER_LOCK:
            presence_raw_result = CRITIC.get_trajectory_done(
                task=presence_task,
                image_list=[after_image],
                ref_image_list=None,
                batch_num=1,
                rich=req.rich,
                threshold=0.0,
                apply_threshold=False,
                skip=1,
                goal_image=None,
            )
            visibility_raw_result = CRITIC.get_trajectory_done(
                task=visibility_task,
                image_list=[after_image],
                ref_image_list=None,
                batch_num=1,
                rich=req.rich,
                threshold=0.0,
                apply_threshold=False,
                skip=1,
                goal_image=None,
            )
    except Exception as exc:
        LOGGER.exception("VLAC inference failed mode=%s method=get_trajectory_done", GRASP_MODE)
        raise ServiceError(500, "INFERENCE_ERROR", f"VLAC done inference failed: {exc}", GRASP_MODE) from exc

    latency_ms = (time.time() - start_t) * 1000.0
    presence_values = _float_list(presence_raw_result, "target_present_result", GRASP_MODE)
    visibility_values = _float_list(visibility_raw_result, "table_visible_result", GRASP_MODE)
    if len(presence_values) != 1:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            "target_present_result must contain exactly one after_image value, "
            f"got {len(presence_values)}",
            GRASP_MODE,
        )
    if len(visibility_values) != 1:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            "table_visible_result must contain exactly one after_image value, "
            f"got {len(visibility_values)}",
            GRASP_MODE,
        )
    target_present_score = presence_values[0]
    table_visible_score = visibility_values[0]
    for field_name, score in (
        ("target_present_score", target_present_score),
        ("table_visible_score", table_visible_score),
    ):
        if not 0.0 <= score <= 1.0:
            raise ServiceError(
                502,
                "PROTOCOL_ERROR",
                f"{field_name} must be between 0 and 1, got {score}",
                GRASP_MODE,
            )

    if target_present_score >= presence_threshold:
        decision = "STILL_PRESENT"
        removal_confirmed = False
        evidence_status = "TARGET_STILL_PRESENT"
    elif table_visible_score >= visibility_threshold:
        decision = "REMOVED"
        removal_confirmed = True
        evidence_status = "REMOVAL_CONFIRMED"
    else:
        decision = "UNCERTAIN"
        removal_confirmed = False
        evidence_status = "VISUAL_EVIDENCE_UNCERTAIN"
    warning = None

    _validate_grasp_result_consistency(
        target_present_score=target_present_score,
        presence_threshold=presence_threshold,
        table_visible_score=table_visible_score,
        visibility_threshold=visibility_threshold,
        decision=decision,
        removal_confirmed=removal_confirmed,
        evidence_status=evidence_status,
    )

    LOGGER.info(
        "VLAC result mode=%s target_present_raw=%r target_present_score=%s "
        "table_visible_raw=%r table_visible_score=%s decision=%s "
        "removal_confirmed=%s latency_ms=%.3f",
        GRASP_MODE,
        presence_raw_result,
        target_present_score,
        visibility_raw_result,
        table_visible_score,
        decision,
        removal_confirmed,
        latency_ms,
    )
    return GraspVerifyResponse(
        mode=GRASP_MODE,
        method="trajectory_done_presence",
        target_label=target_label,
        target_present_score=target_present_score,
        presence_threshold=presence_threshold,
        table_visible_score=table_visible_score,
        visibility_threshold=visibility_threshold,
        decision=decision,
        removal_confirmed=removal_confirmed,
        evidence_status=evidence_status,
        warning=warning,
    )


@app.post("/grasp/done", response_model=GraspDoneResponse)
def grasp_done(req: GraspDoneRequest) -> GraspDoneResponse:
    if CRITIC is None:
        raise ServiceError(503, "MODEL_UNAVAILABLE", "VLAC model is not initialized", GRASP_MODE)
    target_label = req.target_label.strip()
    if not target_label:
        raise ServiceError(400, "INPUT_ERROR", "target_label cannot be empty", GRASP_MODE)

    done_threshold = _finite_threshold(
        req.done_threshold,
        0.5,
        "done_threshold",
        GRASP_MODE,
        unit_interval=True,
    )
    task = GRASP_DONE_TASK.format(target_label=target_label)
    after_image = _read_image(req.after_image, "after_image", GRASP_MODE)

    LOGGER.info(
        "VLAC request mode=%s target_label=%r effective_task=%r after_image=%s "
        "method=get_trajectory_done params=%s",
        GRASP_MODE,
        target_label,
        task,
        _image_metadata(after_image),
        {
            "image_list": ["after_image"],
            "ref_image_list": None,
            "batch_num": 1,
            "rich": req.rich,
            "threshold": 0.0,
            "apply_threshold": False,
            "skip": 1,
            "goal_image": None,
        },
    )

    start_t = time.time()
    try:
        with INFER_LOCK:
            raw_result = CRITIC.get_trajectory_done(
                task=task,
                image_list=[after_image],
                ref_image_list=None,
                batch_num=1,
                rich=req.rich,
                threshold=0.0,
                apply_threshold=False,
                skip=1,
                goal_image=None,
            )
    except Exception as exc:
        LOGGER.exception("VLAC inference failed mode=%s method=get_trajectory_done", GRASP_MODE)
        raise ServiceError(
            500,
            "INFERENCE_ERROR",
            f"VLAC done inference failed: {exc}",
            GRASP_MODE,
        ) from exc

    latency_ms = (time.time() - start_t) * 1000.0
    done_values = _float_list(raw_result, "done_result", GRASP_MODE)
    if len(done_values) != 1:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            f"done_result must contain exactly one value, got {len(done_values)}",
            GRASP_MODE,
        )
    done_score = done_values[0]
    if not 0.0 <= done_score <= 1.0:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            f"done_score must be between 0 and 1, got {done_score}",
            GRASP_MODE,
        )
    grasp_success = done_score >= done_threshold

    LOGGER.info(
        "VLAC result mode=%s target_label=%r effective_task=%r raw_result=%r "
        "done_score=%s done_threshold=%s grasp_success=%s latency_ms=%.3f",
        GRASP_MODE,
        target_label,
        task,
        raw_result,
        done_score,
        done_threshold,
        grasp_success,
        latency_ms,
    )
    return GraspDoneResponse(
        mode=GRASP_MODE,
        method="trajectory_done",
        target_label=target_label,
        effective_task_description=task,
        done_score=done_score,
        done_threshold=done_threshold,
        grasp_success=grasp_success,
        raw_result=raw_result,
        warning=None,
        latency_ms=latency_ms,
    )


@app.post("/grasp/holding", response_model=GraspHoldingResponse)
def grasp_holding(req: GraspHoldingRequest) -> GraspHoldingResponse:
    if CRITIC is None:
        raise ServiceError(
            503,
            "MODEL_UNAVAILABLE",
            "VLAC model is not initialized",
            GRASP_HOLDING_MODE,
        )
    target_label = req.target_label.strip()
    if not target_label:
        raise ServiceError(
            400,
            "INPUT_ERROR",
            "target_label cannot be empty",
            GRASP_HOLDING_MODE,
        )

    holding_threshold = _finite_threshold(
        req.holding_threshold,
        GRASP_HOLDING_THRESHOLD,
        "holding_threshold",
        GRASP_HOLDING_MODE,
        unit_interval=True,
    )
    task = GRASP_HOLDING_TASK.format(target_label=target_label)
    after_image = _read_image(req.after_image, "after_image", GRASP_HOLDING_MODE)

    LOGGER.info(
        "VLAC request mode=%s target_label=%r effective_task=%r after_image=%s "
        "method=get_trajectory_done params=%s",
        GRASP_HOLDING_MODE,
        target_label,
        task,
        _image_metadata(after_image),
        {
            "image_list": ["after_image"],
            "ref_image_list": None,
            "batch_num": 1,
            "rich": req.rich,
            "threshold": 0.0,
            "apply_threshold": False,
            "skip": 1,
            "goal_image": None,
        },
    )

    start_t = time.time()
    try:
        with INFER_LOCK:
            raw_result = CRITIC.get_trajectory_done(
                task=task,
                image_list=[after_image],
                ref_image_list=None,
                batch_num=1,
                rich=req.rich,
                threshold=0.0,
                apply_threshold=False,
                skip=1,
                goal_image=None,
            )
    except Exception as exc:
        LOGGER.exception(
            "VLAC inference failed mode=%s method=get_trajectory_done",
            GRASP_HOLDING_MODE,
        )
        raise ServiceError(
            500,
            "INFERENCE_ERROR",
            f"VLAC done inference failed: {exc}",
            GRASP_HOLDING_MODE,
        ) from exc

    latency_ms = (time.time() - start_t) * 1000.0
    holding_values = _float_list(raw_result, "holding_result", GRASP_HOLDING_MODE)
    if len(holding_values) != 1:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            f"holding_result must contain exactly one value, got {len(holding_values)}",
            GRASP_HOLDING_MODE,
        )
    holding_score = holding_values[0]
    if not 0.0 <= holding_score <= 1.0:
        raise ServiceError(
            502,
            "PROTOCOL_ERROR",
            f"holding_score must be between 0 and 1, got {holding_score}",
            GRASP_HOLDING_MODE,
        )
    grasp_success = holding_score >= holding_threshold

    LOGGER.info(
        "VLAC result mode=%s target_label=%r effective_task=%r "
        "method=get_trajectory_done raw_result=%r holding_score=%s "
        "holding_threshold=%s grasp_success=%s latency_ms=%.3f",
        GRASP_HOLDING_MODE,
        target_label,
        task,
        raw_result,
        holding_score,
        holding_threshold,
        grasp_success,
        latency_ms,
    )
    return GraspHoldingResponse(
        mode=GRASP_HOLDING_MODE,
        method="get_trajectory_done_single_image_holding",
        target_label=target_label,
        effective_task_description=task,
        holding_score=holding_score,
        holding_threshold=holding_threshold,
        grasp_success=grasp_success,
        raw_result=raw_result,
        warning=None,
        latency_ms=latency_ms,
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8014"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
