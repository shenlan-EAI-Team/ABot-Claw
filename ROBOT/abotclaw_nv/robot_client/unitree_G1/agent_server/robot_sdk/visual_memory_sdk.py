#!/usr/bin/env python3
"""Create a single-image VPR index for an existing SpatialMemory place.

The robot must already be at the precise pose associated with ``place_id``.
This module captures one D455 RGB frame without moving the robot, uploads it
to the VPR service, and records the visual-index status in SpatialMemory.
"""

import os

import cv2

from robot_sdk.g1_d455_camera import G1D455Camera
from robot_sdk.memory_sdk import MemorySDK
from robot_sdk.vpr_sdk import VPRSDK


IMAGE_DIR = "/tmp/visual_memory"


class VisualMemorySDK:
    def __init__(self, camera=None, vpr=None, memory=None):
        self.vpr = vpr if vpr is not None else VPRSDK()
        self.memory = memory if memory is not None else MemorySDK()
        self._owns_camera = camera is None
        self.camera = camera if camera is not None else G1D455Camera()

        if self._owns_camera and not self.camera.initialize():
            raise RuntimeError("D455 initialize failed")

        os.makedirs(IMAGE_DIR, exist_ok=True)

    @staticmethod
    def _save_rgb(rgb, path):
        image = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if not cv2.imwrite(path, image):
            raise RuntimeError("failed to save D455 RGB image")

    def create_visual_memory(
        self,
        place_id,
        robot_pose=None,
        semantic_note="",
        semantic_tags=None,
    ):
        """Store one RGB frame in Semantic Memory and the VPR index."""
        print("[VisualMemorySDK] start:", place_id)

        try:
            if robot_pose is None:
                place = self.memory.get_place(place_id)
                robot_pose = place.get("target_pose")
                if not isinstance(robot_pose, dict):
                    raise RuntimeError("place has no target_pose")

            rgb, _ = self.camera.get_frame()
            if rgb is None:
                raise RuntimeError("D455 frame unavailable")

            image_path = os.path.join(IMAGE_DIR, f"{place_id}.jpg")
            self._save_rgb(rgb, image_path)
            print("[VisualMemorySDK] saved:", image_path)

            semantic_result = self.memory.ingest_semantic_frame(
                robot_id="g1_001",
                robot_type="humanoid",
                robot_pose=robot_pose,
                image_path=image_path,
                note=semantic_note,
                tags=semantic_tags or [],
                source="camera",
            )
            semantic_memory_id = (
                semantic_result.get("memory_id")
                or semantic_result.get("id")
            )
            print("[VisualMemorySDK] semantic ingest:", semantic_result)

            pending_index = {
                "status": "pending",
                "image_id": place_id,
            }
            self.memory.update_visual_index(
                place_id,
                pending_index,
            )

            try:
                upload_result = self.vpr.upload_image(
                    place_id=place_id,
                    image_path=image_path,
                    image_id=place_id,
                )
            except Exception as upload_error:
                try:
                    self.memory.update_visual_index(
                        place_id,
                        {
                            "status": "failed",
                            "image_id": place_id,
                            "error": str(upload_error)[:500],
                        },
                    )
                except Exception as patch_error:
                    print(
                        "[VisualMemorySDK] failed-status update failed:",
                        patch_error,
                    )
                raise

            print("[VisualMemorySDK] upload:", upload_result)

            visual_index = {
                "status": "indexed",
                "image_id": place_id,
                "backend": "salad",
                "version": "salad_v1",
            }
            memory_result = self.memory.update_visual_index(
                place_id,
                visual_index,
            )
            print("[VisualMemorySDK] memory updated:", memory_result)

            return {
                "place_id": place_id,
                "semantic_memory_id": semantic_memory_id,
                "image_id": place_id,
                "visual_index": visual_index,
            }
        finally:
            if self._owns_camera:
                self.camera.close()
