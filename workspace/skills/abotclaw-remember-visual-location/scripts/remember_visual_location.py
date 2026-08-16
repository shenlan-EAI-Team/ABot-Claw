#!/usr/bin/env python3
"""Create Semantic Frame and VPR memory from one fixed-pose D455 image."""

import argparse

from robot_sdk.visual_memory_sdk import VisualMemorySDK


def main(place_id, semantic_note="", semantic_tags=None):
    return VisualMemorySDK().create_visual_memory(
        place_id=place_id,
        semantic_note=semantic_note,
        semantic_tags=semantic_tags or [],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create single-image semantic and VPR memory"
    )
    parser.add_argument(
        "--place-id",
        required=True,
        help="Existing SpatialMemory place_id",
    )
    parser.add_argument("--semantic-note", default="")
    parser.add_argument(
        "--semantic-tag",
        action="append",
        default=[],
        dest="semantic_tags",
    )
    args = parser.parse_args()
    result = main(
        args.place_id,
        semantic_note=args.semantic_note,
        semantic_tags=args.semantic_tags,
    )
    print(result)
