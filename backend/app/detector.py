"""Optional local YOLOv8 video-stream worker.

Imports are deliberately lazy: the normal dashboard works with no CV/GPU
dependencies, while a configured camera can update one venue zone in real
time.  Each worker samples frames rather than attempting to infer every frame.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from threading import Event, Lock, Thread
from typing import Callable
import time


@dataclass
class Feed:
    zone_id: int
    stream_url: str
    model_path: str
    sample_seconds: float
    source_type: str = "manual"  # webcam | rtsp | upload | manual (generic IP-camera URL, the original use case)
    stop: Event = field(default_factory=Event)
    thread: Thread | None = None
    last_count: int | None = None
    last_updated: float | None = None
    error: str | None = None


class CrowdDetector:
    def __init__(self):
        self._feeds: dict[int, Feed] = {}
        self._lock = Lock()

    def status(self):
        with self._lock:
            return [{
                "zone_id": f.zone_id, "stream_url": f.stream_url, "source_type": f.source_type,
                "running": bool(f.thread and f.thread.is_alive()),
                "last_count": f.last_count, "last_updated": f.last_updated,
                "error": f.error,
            } for f in self._feeds.values()]

    def start(self, zone_id: int, stream_url: str, model_path: str, sample_seconds: float,
              on_count: Callable[[int, int], None], source_type: str = "manual"):
        self.stop(zone_id)
        feed = Feed(zone_id, stream_url, model_path, max(sample_seconds, 0.2), source_type=source_type)
        feed.thread = Thread(target=self._run, args=(feed, on_count), daemon=True, name=f"yolo-zone-{zone_id}")
        with self._lock:
            self._feeds[zone_id] = feed
        feed.thread.start()
        return feed

    def stop(self, zone_id: int):
        with self._lock:
            feed = self._feeds.get(zone_id)
        if feed:
            feed.stop.set()
        return feed is not None

    def _run(self, feed: Feed, on_count: Callable[[int, int], None]):
        try:
            import cv2
            from ultralytics import YOLO
        except ImportError:
            feed.error = "YOLO dependencies missing. Install opencv-python and ultralytics."
            return
        source = int(feed.stream_url) if feed.stream_url.isdigit() else feed.stream_url
        # Bounded connect/read timeouts -- an unreachable RTSP host otherwise
        # leaves OpenCV/FFmpeg's own internal timeout to decide (observed:
        # 30s+, with the feed just sitting at running=true/no error the whole
        # time), which reads as a hang rather than a graceful failure for an
        # operator watching the Command Centre. 8s is a demo-reasonable bound;
        # harmless for local sources (webcam/file), which open near-instantly
        # regardless.
        capture = cv2.VideoCapture()
        capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000)
        capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000)
        capture.open(source)
        if not capture.isOpened():
            feed.error = {
                "webcam": "Could not open the backend machine's webcam. Check the device index and that no other app is using it.",
                "rtsp": "Could not connect to the RTSP/CCTV stream. Check the URL, credentials, and network access.",
                "upload": "Could not open the uploaded video file.",
            }.get(feed.source_type, "Could not open the camera stream. Check its URL and network access.")
            return
        model = YOLO(feed.model_path)
        next_sample = 0.0
        try:
            while not feed.stop.is_set():
                ok, frame = capture.read()
                if not ok:
                    feed.error = (
                        "Video finished." if feed.source_type == "upload"
                        else "Camera stream disconnected or stopped sending frames."
                    )
                    break
                now = time.monotonic()
                if now < next_sample:
                    continue
                result = model(frame, classes=[0], verbose=False)[0]  # COCO class 0 = person
                count = len(result.boxes) if result.boxes is not None else 0
                on_count(feed.zone_id, count)
                feed.last_count, feed.last_updated, feed.error = count, time.time(), None
                next_sample = now + feed.sample_seconds
        except Exception as exc:
            feed.error = str(exc)
        finally:
            capture.release()


detector = CrowdDetector()
