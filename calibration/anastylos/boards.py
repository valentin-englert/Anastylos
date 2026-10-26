"""OpenCV ArUco / ChArUco boards built only from the calibration config."""

from __future__ import annotations

import cv2
import numpy as np


def dictionary(name: str):
    const = getattr(cv2.aruco, name)
    return cv2.aruco.getPredefinedDictionary(const)


def detector_parameters(cfg: dict) -> cv2.aruco.DetectorParameters:
    params = cv2.aruco.DetectorParameters()
    method = cfg["detection"]["corner_refinement"]
    params.cornerRefinementMethod = getattr(cv2.aruco, method)
    return params


def marker_mask(dict_name: str, marker_id: int, modules: int) -> np.ndarray:
    """1 where the module is black. `modules` includes the 1-module border."""
    image = cv2.aruco.generateImageMarker(dictionary(dict_name), int(marker_id), int(modules), borderBits=1)
    if image.shape != (modules, modules):
        raise RuntimeError(f"OpenCV returned marker size {image.shape}, expected {(modules, modules)}")
    if not set(np.unique(image)).issubset({0, 255}):
        raise RuntimeError("marker bitmap is not binary")
    return image == 0


def charuco_board(cfg: dict) -> cv2.aruco.CharucoBoard:
    spec = cfg["charuco"]
    board = cv2.aruco.CharucoBoard(
        (int(spec["squares_x"]), int(spec["squares_y"])),
        float(spec["square_length_mm"]),
        float(spec["marker_length_mm"]),
        dictionary(spec["dictionary"]),
        np.array(spec["marker_ids"], dtype=np.int32),
    )
    board.setLegacyPattern(bool(spec["legacy_pattern"]))
    return board


def charuco_mask(cfg: dict) -> tuple[np.ndarray, float]:
    """Black-module mask of the whole board at 1 pixel = 1 module, plus module size in mm."""
    spec = cfg["charuco"]
    bits = int(cfg["dictionaries"][spec["dictionary"]]["marker_bits"])
    unit = float(spec["marker_length_mm"]) / (bits + 2)
    squares_px = float(spec["square_length_mm"]) / unit
    if abs(squares_px - round(squares_px)) > 1e-9:
        raise RuntimeError("ChArUco square length is not an integer number of modules")
    side = int(round(squares_px))
    width = int(spec["squares_x"]) * side
    height = int(spec["squares_y"]) * side
    image = charuco_board(cfg).generateImage((width, height), marginSize=0, borderBits=1)
    if image.shape != (height, width):
        raise RuntimeError(f"ChArUco image shape {image.shape} != {(height, width)}")
    if not set(np.unique(image)).issubset({0, 255}):
        raise RuntimeError("ChArUco bitmap is not binary")
    return image == 0, unit
