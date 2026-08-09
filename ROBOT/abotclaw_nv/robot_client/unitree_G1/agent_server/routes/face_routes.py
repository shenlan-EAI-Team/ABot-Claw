"""HTTP 封装：``face_sdk.FaceSDK``（人脸识别服务）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/face", tags=["G1 Face Recognition"])


class FaceHealthResponse(BaseModel):
    status: str
    version: str | None = None
    model_loaded: bool | None = None
    people_count: int | None = None
    message: str | None = None


class FacePeopleResponse(BaseModel):
    people: list[str]


class FaceEnrollRequest(BaseModel):
    name: str = Field(..., description="人员名称")
    images: list[str] = Field(..., description="图像列表 (base64/路径/URL)")


class FaceEnrollResponse(BaseModel):
    success: bool
    message: str
    name: str | None = None


class FaceRecognizeRequest(BaseModel):
    image: str = Field(..., description="图像 (base64/路径/URL)")
    threshold: float | None = Field(default=None, description="相似度阈值")
    include_annotated_image: bool = Field(default=False, description="返回标注图")


class FaceRecognizeResponse(BaseModel):
    recognized: bool
    name: str | None = None
    confidence: float | None = None
    annotated_image: str | None = None


@router.get("/health", response_model=FaceHealthResponse)
async def face_health():
    """获取人脸识别服务健康状态。"""
    from robot_sdk import FaceSDK

    try:
        face = FaceSDK()
        health = face.health()
        return FaceHealthResponse(
            status=health.get("status", "ok"),
            version=health.get("version"),
            model_loaded=health.get("model_loaded"),
            people_count=health.get("people_count"),
            message=health.get("message"),
        )
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/people", response_model=FacePeopleResponse)
async def list_people():
    """读取当前人脸库中的人员列表。"""
    from robot_sdk import FaceSDK

    try:
        face = FaceSDK()
        people = face.list_people()
        return FacePeopleResponse(people=people)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/enroll", response_model=FaceEnrollResponse)
async def enroll_person(body: FaceEnrollRequest):
    """录入单个人员到人脸库。"""
    from robot_sdk import FaceSDK

    try:
        face = FaceSDK()
        result = face.enroll(body.name, body.images)
        return FaceEnrollResponse(
            success=result.get("success", True),
            message=result.get("message", "Enrolled successfully"),
            name=body.name,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/recognize", response_model=FaceRecognizeResponse)
async def recognize_face(body: FaceRecognizeRequest):
    """对单张图片执行人脸识别。"""
    from robot_sdk import FaceSDK

    try:
        face = FaceSDK()
        result = face.recognize(
            image=body.image,
            threshold=body.threshold,
            include_annotated_image=body.include_annotated_image,
        )
        matches = result.get("results", [])
        annotated = result.get("annotated_image") if body.include_annotated_image else None
        if matches:
            best = matches[0]
            return FaceRecognizeResponse(
                recognized=True,
                name=best.get("name"),
                confidence=best.get("match_score"),
                annotated_image=annotated,
            )
        return FaceRecognizeResponse(recognized=False, annotated_image=annotated)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



