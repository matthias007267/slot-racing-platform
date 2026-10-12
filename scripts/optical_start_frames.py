"""Find a red lamp in a video. This is not part of the race and not a proof of sync.

The reported time is the frame in which a region became red enough. The change
happened somewhere in that frame's exposure, not at a single instant. Devices
in different rows of a rolling shutter are not simultaneous. The uncertainty
is at least one frame period.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Region:
    name: str
    left: float
    top: float
    width: float
    height: float


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Erste deutliche Rotphase je Bildbereich")
    parser.add_argument("video")
    parser.add_argument(
        "--roi", action="append", required=True, help="name=links,oben,breite,hoehe"
    )
    parser.add_argument("--threshold", type=float, default=0.02)
    parser.add_argument("--exposure-ms", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        import cv2
    except ImportError:
        print("OpenCV fehlt. Das Rennen braucht dieses Skript nicht.", file=sys.stderr)
        return 2
    regions = tuple(_region(text) for text in args.roi)
    capture = cv2.VideoCapture(args.video)
    if not capture.isOpened():
        print("Video lässt sich nicht öffnen.", file=sys.stderr)
        return 1
    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    frame_ms = 1000.0 / fps if fps > 1 else None
    pending = {region.name: region for region in regions}
    index = 0
    while pending:
        ok, frame = capture.read()
        if not ok:
            break
        height, width = frame.shape[:2]
        for name, region in list(pending.items()):
            if _red_ratio(frame, region, width, height) >= args.threshold:
                _report(name, index, frame_ms, args.exposure_ms)
                del pending[name]
        index += 1
    capture.release()
    for name in pending:
        print(f"{name}: kein deutlicher Rotwechsel in {index} Bildern")
    if frame_ms is None:
        print("Bildrate unbekannt. Zeiten sind Bildnummern, keine Millisekunden.")
    else:
        extra = f" zuzüglich {args.exposure_ms:.1f} ms Belichtung" if args.exposure_ms else ""
        print(f"Unsicherheit mindestens ein Bild, {frame_ms:.1f} ms{extra}.")
        print("Rolling Shutter und Display-Auffrischung kommen dazu.")
    return 0


def _region(text: str) -> Region:
    name, _, numbers = text.partition("=")
    parts = tuple(float(item) for item in numbers.split(","))
    if len(parts) != 4 or not name:
        raise SystemExit("ROI erwartet name=links,oben,breite,hoehe")
    left, top, width, height = parts
    if min(left, top, width, height) < 0 or left + width > 1 or top + height > 1:
        raise SystemExit("ROI muss im Bild liegen, als Anteil von 0 bis 1")
    return Region(name, left, top, width, height)


def _red_ratio(frame: object, region: Region, width: int, height: int) -> float:
    x0 = int(region.left * width)
    y0 = int(region.top * height)
    x1 = int((region.left + region.width) * width)
    y1 = int((region.top + region.height) * height)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    crop = frame[y0:y1, x0:x1]
    blue = crop[:, :, 0].astype("int16")
    green = crop[:, :, 1].astype("int16")
    red = crop[:, :, 2].astype("int16")
    mask = (red > 80) & (red > green * 1.4) & (red > blue * 1.4)
    return float(mask.mean())


def _report(name: str, index: int, frame_ms: float | None, exposure_ms: float) -> None:
    if frame_ms is None:
        print(f"{name}: Bild {index}")
        return
    start = index * frame_ms
    end = start + frame_ms + exposure_ms
    print(f"{name}: Bild {index}, Wechsel irgendwo zwischen {start:.1f} und {end:.1f} ms")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
