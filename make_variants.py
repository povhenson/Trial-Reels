#!/usr/bin/env python3
"""Make K subtly different variants of every video in ./originals/.

Each variant gets fresh random values for:
  - One near-black frame (random RGB 0-8) at the very start and very end,
    with silence padded into the audio so it stays in sync
  - Zoom of 105-112%, cropped back to 1080x1920 (no letterboxing)
  - A small reposition of the zoomed frame that keeps the centre in view
  - On about half the variants, a 1-2 degree rotation; the zoom is raised
    if needed so no black corners ever show
  - A light colour adjustment: contrast, saturation, warmth, sharpness

No mirroring, no AI upscaling, no other effects.

Export: H.264 High, 1080x1920, original frame rate, CRF 16 (capped at
20 Mbps), AAC 320k, +faststart. Every variant's settings are appended to
variants_log.csv, and a combination is never repeated for the same original.

Usage:
  python3 make_variants.py [-k 3] [--originals originals] [--out variants]
"""
import argparse
import csv
import datetime
import json
import math
import random
import subprocess
from pathlib import Path

W, H = 1080, 1920
VIDEO_EXTS = {".mp4", ".mov", ".m4v"}
COMMON_FPS = [23.976, 24, 25, 29.97, 30, 50, 59.94, 60]
AUDIO_RATE = 48000
CRF = 16
AUDIO_BITRATE = "320k"
LOG_FIELDS = [
    "created", "original", "variant", "fps", "zoom", "rotation_deg",
    "offset_x", "offset_y", "contrast", "saturation", "temperature",
    "sharpness", "start_rgb", "end_rgb", "crf", "audio_bitrate",
]
# Fields that together define a "combination" for the no-repeat rule
COMBO_FIELDS = [
    "zoom", "rotation_deg", "offset_x", "offset_y", "contrast",
    "saturation", "temperature", "sharpness", "start_rgb", "end_rgb",
]


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    streams = json.loads(out)["streams"]
    video = next(s for s in streams if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in streams)
    return video, has_audio


def parse_rate(rate):
    num, _, den = rate.partition("/")
    return float(num) / float(den or 1) if float(den or 1) else 0.0


def source_fps(video):
    """Original frame rate, snapped to a standard rate when it's close."""
    fps = parse_rate(video.get("avg_frame_rate", "0/0")) \
        or parse_rate(video["r_frame_rate"])
    nearest = min(COMMON_FPS, key=lambda f: abs(f - fps))
    if abs(nearest - fps) / nearest < 0.01:
        fps = nearest
    return {23.976: "24000/1001", 29.97: "30000/1001",
            59.94: "60000/1001"}.get(fps, f"{fps:g}")


def fits(zoom, angle_deg, dx, dy):
    """True if a crop offset by (dx, dy) shows no rotated-in corners.

    The frame is rotated about its centre, then scaled by `zoom`, then a
    WxH window is cropped with its centre offset by (dx, dy) pixels. Every
    window corner, mapped back through the zoom and rotation, must land
    inside the original WxH frame.
    """
    a = math.radians(angle_deg)
    cos_a, sin_a = math.cos(a), math.sin(a)
    for sx in (-1, 1):
        for sy in (-1, 1):
            x = (dx + sx * W / 2) / zoom
            y = (dy + sy * H / 2) / zoom
            xr = x * cos_a + y * sin_a
            yr = -x * sin_a + y * cos_a
            if abs(xr) > W / 2 + 1e-6 or abs(yr) > H / 2 + 1e-6:
                return False
    return True


def min_zoom_for_rotation(angle_deg):
    a = math.radians(abs(angle_deg))
    return math.cos(a) + (H / W) * math.sin(a)


def pick_params(rng):
    zoom = rng.uniform(1.05, 1.12)
    angle = 0.0
    if rng.random() < 0.5:
        angle = rng.uniform(1.0, 2.0) * rng.choice([-1, 1])
        # Raise the zoom if needed, with headroom left for the reposition
        zoom = max(zoom, min_zoom_for_rotation(angle) + 0.01)

    # Reposition: at most 3% of the frame each way so the centre stays put,
    # shrunk until no corners show
    fx, fy = rng.uniform(-1, 1), rng.uniform(-1, 1)
    max_dx = min((zoom - 1) * W / 2, 0.03 * W)
    max_dy = min((zoom - 1) * H / 2, 0.03 * H)
    scale = 1.0
    while scale > 0 and not fits(zoom, angle, fx * max_dx * scale,
                                 fy * max_dy * scale):
        scale -= 0.05
    scale = max(scale, 0.0)

    def near_black():
        return "%02x%02x%02x" % tuple(rng.randint(0, 8) for _ in range(3))

    return {
        "zoom": round(zoom, 3),
        "rotation_deg": round(angle, 2),
        "offset_x": round(fx * max_dx * scale),
        "offset_y": round(fy * max_dy * scale),
        # ~75%-strength filter: small nudges, not a visible style change
        "contrast": round(rng.uniform(1.01, 1.06), 3),
        "saturation": round(rng.uniform(1.02, 1.10), 3),
        "temperature": rng.randint(6100, 6900),  # 6500 = neutral
        "sharpness": round(rng.uniform(0.15, 0.45), 2),
        "start_rgb": near_black(),
        "end_rgb": near_black(),
    }


def build_video_filter(p, fps, video):
    zw = math.ceil(W * p["zoom"] / 2) * 2
    zh = math.ceil(H * p["zoom"] / 2) * 2
    crop_x = min(max((zw - W) // 2 + p["offset_x"], 0), zw - W)
    crop_y = min(max((zh - H) // 2 + p["offset_y"], 0), zh - H)

    steps = []
    if video.get("color_transfer") in ("arib-std-b67", "smpte2084"):
        # iPhone HDR: tone-map to SDR so colours don't come out washed out
        steps += [
            "zscale=t=linear:npl=100", "format=gbrpf32le",
            "zscale=p=bt709", "tonemap=hable:desat=0",
            "zscale=t=bt709:m=bt709:r=tv",
        ]
    steps += [
        f"fps={fps}",
        f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos",
        f"crop={W}:{H}",
    ]
    if p["rotation_deg"]:
        steps.append(f"rotate={p['rotation_deg']}*PI/180:fillcolor=black")
    steps += [
        f"scale={zw}:{zh}:flags=lanczos",
        f"crop={W}:{H}:{crop_x}:{crop_y}",
        f"eq=contrast={p['contrast']}:saturation={p['saturation']}",
        f"colortemperature=temperature={p['temperature']}",
        f"unsharp=5:5:{p['sharpness']}",
        "setsar=1",
        f"tpad=start=1:start_mode=add:color=0x{p['start_rgb']}",
        f"tpad=stop=1:stop_mode=add:color=0x{p['end_rgb']}",
        "format=yuv420p",
    ]
    return ",".join(steps)


def render(src, dst, p, fps, video, has_audio):
    frame_secs = 1 / parse_rate(fps)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-vf", build_video_filter(p, fps, video), "-map", "0:v:0"]
    if has_audio:
        # Trim audio to the video's length, delay it by the lead-in frame,
        # then pad with silence; -shortest ends it with the last frame
        pad = round(AUDIO_RATE * frame_secs)
        trim = f"atrim=end={video['duration']}," if "duration" in video else ""
        cmd += ["-map", "0:a:0",
                "-af", f"{trim}aresample={AUDIO_RATE},adelay={pad}S:all=1,"
                       "apad",
                "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(AUDIO_RATE)]
    cmd += ["-c:v", "libx264", "-profile:v", "high", "-preset", "slow",
            "-crf", str(CRF), "-maxrate", "20M", "-bufsize", "40M",
            "-pix_fmt", "yuv420p", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-colorspace", "bt709",
            "-map_metadata", "-1", "-movflags", "+faststart", "-shortest",
            str(dst)]
    subprocess.run(cmd, check=True)


def combo_key(row):
    return tuple(str(row[f]) for f in COMBO_FIELDS)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", type=int, default=3, help="variants per original")
    ap.add_argument("--originals", default="originals")
    ap.add_argument("--out", default="variants")
    ap.add_argument("--log", default="variants_log.csv")
    ap.add_argument("--seed", type=int)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(args.log)

    used = {}
    if log_path.exists():
        with log_path.open(newline="") as f:
            for row in csv.DictReader(f):
                used.setdefault(row["original"], set()).add(combo_key(row))

    sources = sorted(p for p in Path(args.originals).iterdir()
                     if p.suffix.lower() in VIDEO_EXTS)
    if not sources:
        raise SystemExit(f"No videos found in {args.originals}/")

    new_log = not log_path.exists()
    with log_path.open("a", newline="") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=LOG_FIELDS)
        if new_log:
            writer.writeheader()

        for src in sources:
            video, has_audio = probe(src)
            fps = source_fps(video)
            seen = used.setdefault(src.name, set())
            # Continue numbering after any variants made on earlier runs
            n = len(seen)
            for _ in range(args.k):
                while True:
                    p = pick_params(rng)
                    if combo_key(p) not in seen:
                        break
                n += 1
                dst = out_dir / f"{src.stem}_v{n}.mp4"
                render(src, dst, p, fps, video, has_audio)
                seen.add(combo_key(p))
                writer.writerow({
                    "created": datetime.datetime.now().isoformat(
                        timespec="seconds"),
                    "original": src.name, "variant": dst.name, "fps": fps,
                    **p, "crf": CRF, "audio_bitrate": AUDIO_BITRATE,
                })
                log_file.flush()
                print(f"{dst}  zoom={p['zoom']} rot={p['rotation_deg']} "
                      f"offset=({p['offset_x']},{p['offset_y']})")


if __name__ == "__main__":
    main()
