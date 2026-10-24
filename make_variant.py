#!/usr/bin/env python3
"""Make a slightly altered copy of a reel, mirroring the manual CapCut steps.

Steps applied (each variant gets fresh random values):
  1. One black frame added at the start (audio delayed to match)
  2. Small rotation (about 0.5-1.2 degrees, either direction)
  3. Zoom in about 5-9% (also hides the rotated corners)
  4. Light colour filter: contrast / saturation / brightness / warmth
  5. Re-encoded at 1080x1920, 30fps, H.264 + AAC, metadata stripped

Usage:
  python3 make_variant.py input.mp4 output.mp4 [--seed N]
"""
import argparse
import json
import random
import subprocess
import sys

W, H, FPS = 1080, 1920, 30


def pick_params(rng):
    angle = rng.uniform(0.5, 1.2) * rng.choice([-1, 1])
    return {
        "angle_deg": round(angle, 2),
        "zoom": round(rng.uniform(1.05, 1.09), 3),
        "contrast": round(rng.uniform(1.02, 1.08), 3),
        "saturation": round(rng.uniform(1.03, 1.15), 3),
        "brightness": round(rng.uniform(-0.02, 0.03), 3),
        "temperature": rng.randint(6000, 7200),  # 6500 = neutral
    }


def has_audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", path],
        capture_output=True, text=True, check=True,
    ).stdout
    return bool(out.strip())


def build_filter(p):
    zw, zh = int(W * p["zoom"]) // 2 * 2, int(H * p["zoom"]) // 2 * 2
    return ",".join([
        # Fit to 9:16, cropping any overflow
        f"scale={W}:{H}:force_original_aspect_ratio=increase",
        f"crop={W}:{H}",
        f"fps={FPS}",
        f"rotate={p['angle_deg']}*PI/180:fillcolor=black",
        f"scale={zw}:{zh}",
        f"crop={W}:{H}",
        f"eq=contrast={p['contrast']}:saturation={p['saturation']}"
        f":brightness={p['brightness']}",
        f"colortemperature=temperature={p['temperature']}",
        "setsar=1",
        # The one black frame at the start
        "tpad=start=1:start_mode=add:color=black",
        "format=yuv420p",
    ])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--seed", type=int)
    args = ap.parse_args()

    p = pick_params(random.Random(args.seed))
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", args.input,
           "-vf", build_filter(p), "-map", "0:v:0"]
    if has_audio(args.input):
        delay_ms = round(1000 / FPS)
        cmd += ["-map", "0:a:0", "-af", f"adelay={delay_ms}:all=1",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "19",
            "-profile:v", "high", "-map_metadata", "-1",
            "-movflags", "+faststart", args.output]

    subprocess.run(cmd, check=True)
    json.dump(p, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
