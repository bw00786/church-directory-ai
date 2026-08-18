"""Detector abstractions and lightweight person detection."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

try:
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None  # type: ignore
    np = None  # type: ignore

from .models import DetectorObject, DetectorResult


class Detector(ABC):
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def process(self, frame: Any, timestamp: float, camera_id: int) -> DetectorResult:
        raise NotImplementedError

    async def shutdown(self) -> None:
        pass


class PersonDetector(Detector):
    def __init__(self, confidence_threshold: float = 0.5, model_path: str | None = None):
        self.confidence_threshold = confidence_threshold
        self.model_path = model_path
        self.hog = None
        self.net = None
        self.model_loaded = False

    async def initialize(self) -> None:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for person detection")
        if self.model_path:
            self.net = cv2.dnn.readNet(self.model_path)
            self.model_loaded = True
        else:
            self.hog = cv2.HOGDescriptor()
            self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            self.model_loaded = True

    async def process(self, frame: Any, timestamp: float, camera_id: int) -> DetectorResult:
        if cv2 is None:
            raise RuntimeError("opencv-python is required for person detection")

        results: list[DetectorObject] = []
        height, width = frame.shape[:2]

        if self.net is not None:
            blob = cv2.dnn.blobFromImage(frame, 1 / 255.0, (416, 416), swapRB=True, crop=False)
            self.net.setInput(blob)
            layer_outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
            boxes, confidences = [], []
            for output in layer_outputs:
                for detection in output:
                    scores = detection[5:]
                    class_id = int(scores.argmax())
                    confidence = float(scores[class_id])
                    if confidence >= self.confidence_threshold and class_id == 0:
                        center_x = int(detection[0] * width)
                        center_y = int(detection[1] * height)
                        w = int(detection[2] * width)
                        h = int(detection[3] * height)
                        x = max(0, center_x - w // 2)
                        y = max(0, center_y - h // 2)
                        boxes.append([x, y, w, h])
                        confidences.append(confidence)
            indices = cv2.dnn.NMSBoxes(boxes, confidences, self.confidence_threshold, 0.4)
            for i in indices.flatten().tolist():
                results.append(
                    DetectorObject(
                        class_name="person",
                        confidence=confidences[i],
                        bbox=[int(boxes[i][0]), int(boxes[i][1]), int(boxes[i][2]), int(boxes[i][3])],
                    )
                )
        else:
            rects, weights = self.hog.detectMultiScale(frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
            for rect, weight in zip(rects, weights):
                confidence = float(weight[0]) if isinstance(weight, (tuple, list, np.ndarray)) else float(weight)
                if confidence >= self.confidence_threshold:
                    x, y, w, h = rect
                    results.append(
                        DetectorObject(
                            class_name="person",
                            confidence=confidence,
                            bbox=[int(x), int(y), int(w), int(h)],
                        )
                    )

        return DetectorResult(
            detector="person",
            timestamp=timestamp,
            camera_id=camera_id,
            objects=results,
            metadata={"frame_width": width, "frame_height": height},
        )
