#!/usr/bin/env python3
"""Build a fresh remix reel from the asset pool, following the TikTok edit.

Structure (about 5 seconds, 1080x1920, 30fps):
  1. The pouring part of one sand clip (up to 2s); the song is started so
     its beat drop lands exactly on the cut out of this clip
  2. Water image (shown whole on black)          1.0s
  3. Second water image (whole on black)         1.0s
  4. Profile screenshot (whole on black)         1.8s
  A 0.5s white flash on every cut, the hook line over the whole video, and
  the newest song in assets/sounds as the only audio (clip sound muted).

Each run picks a combination of clip, two of the water images (in order),
profile screenshot and hook line that is not in remix_log.csv yet.

Assets live in assets/raw (from the Drive folder). Videos are clips (the old
finished trial* reels are skipped); dark images are profile screenshots,
the rest are water images.

Usage:
  python3 remix.py [--out remixes] [--seed N]
"""
import argparse
import array
import csv
import datetime
import itertools
import json
import math
import random
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

W, H, FPS = 1080, 1920, 30
IMAGE_SECS = 1.0
PROFILE_SECS = 1.8
FLASH_HALF = 0.25  # fade to white over this long before a cut, and back after
# Roughly where the current song's beat drop is; the exact bass hit is found
# within half a second of this. Update it when the song changes.
DROP_NEAR_SECS = 3.5
MAX_CLIP_SECS = 2.0
# Proxima Nova (or TikTok Sans) if its file is in assets/fonts, else
# Montserrat SemiBold, the closest free match
FONT_DIR = Path("assets/fonts")
FALLBACK_FONT = "/usr/share/fonts/opentype/montserrat/Montserrat-SemiBold.otf"
TEXT_SQUEEZE = 1.0     # horizontal squeeze of the hook text (1.0 = none)
TEXT_MAX_WIDTH = 0.76  # of the frame, clear of the like/comment buttons
# Assets the user rejected; skipped even if they are in the folder
EXCLUDED = {"IMG_1624.PNG"}
VIDEO_EXTS = {".mov", ".mp4", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac"}
# Seconds into each clip where the pouring starts; windows begin after this
POUR_START = {"IMG_1995.mov": 0.3, "IMG_2059.mov": 0.55}
HOOKS = [
    "POV: How it feels to follow a small creator",
    "POV: How it feels following a small creator",
    "POV: How it feels dropping a follow for a small creator",
    "POV: How it feels supporting a small creator",
    "POV: How it feels subscribing to a small creator",
    "POV: How it feels to follow an underrated creator",
    "POV: How it feels when you follow a small account",
    "POV: How it feels giving a follow to a small creator",
]
LOG_FIELDS = ["created", "output", "clip", "clip_start", "clip_secs",
              "image1", "image2", "profile", "hook", "song", "drop_secs",
              "song_start"]


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    info = json.loads(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    return video, float(info["format"]["duration"])


def classify(raw_dir):
    clips, water, profiles = [], [], []
    for p in sorted(raw_dir.iterdir()):
        ext = p.suffix.lower()
        if p.name in EXCLUDED:
            continue
        if ext in VIDEO_EXTS and not p.name.lower().startswith("trial"):
            clips.append(p)
        elif ext in IMAGE_EXTS:
            # Profile screenshots are mostly the dark app background
            luma = ImageStat.Stat(Image.open(p).convert("L")).mean[0]
            (profiles if luma < 70 else water).append(p)
    return clips, water, profiles


def newest_song(sound_dir):
    songs = [p for p in sound_dir.iterdir() if p.suffix.lower() in AUDIO_EXTS]
    if not songs:
        raise SystemExit(f"No song in {sound_dir}/")
    return max(songs, key=lambda p: p.stat().st_mtime)


def find_drop(song, near=DROP_NEAR_SECS):
    """Time of the first bass hit after a quiet gap, within 0.5s of `near`,
    found from the low-frequency envelope."""
    lo, hi = near - 0.5, near + 0.5
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(song), "-af",
         "lowpass=f=120,lowpass=f=120", "-ac", "1", "-ar", "8000",
         "-f", "s16le", "-"], capture_output=True, check=True).stdout
    pcm, win = array.array("h", raw), 200  # 25ms windows
    env = [20 * math.log10(math.sqrt(
        sum(x * x for x in pcm[i:i + win]) / win) + 1)
        for i in range(0, len(pcm) - win, win)]
    peak = max(env)
    quiet = [e < peak - 25 for e in env]
    for i in range(4, len(env) - 3):
        t = i * win / 8000
        # Onset: leaves a quiet stretch and reaches full strength within 75ms
        if (lo <= t <= hi and not quiet[i] and all(quiet[i - 4:i])
                and max(env[i:i + 4]) > peak - 12):
            return round(t, 3)
    return near


def split_hook(hook):
    """Two lines, breaking before the final 'a'/'an' as in the original edit."""
    words = hook.split()
    for i in range(len(words) - 1, 0, -1):
        if words[i].lower() in ("a", "an"):
            return " ".join(words[:i]), " ".join(words[i:])
    mid = len(words) // 2
    return " ".join(words[:mid]), " ".join(words[mid:])


def font_path():
    fonts = sorted(p for p in FONT_DIR.glob("*")
                   if p.suffix.lower() in (".otf", ".ttf")
                   and any(k in p.name.lower() for k in ("tiktok", "proxima")))
    fonts.sort(key=lambda p: "proxima" not in p.name.lower())  # Proxima first
    return str(fonts[0]) if fonts else FALLBACK_FONT


def text_overlay(hook, profile, dst, rng):
    """Transparent 1080x1920 PNG: white text with a black outline, squeezed
    horizontally and kept narrow so it stays clear of the side buttons."""
    lines = split_hook(hook)
    size = int(62 * rng.uniform(0.97, 1.03))
    while True:
        font = ImageFont.truetype(font_path(), size)
        widest = max(font.getlength(line) for line in lines)
        if widest * TEXT_SQUEEZE <= TEXT_MAX_WIDTH * W:
            break
        size -= 2
    line_h = int(size * 1.22)
    stroke = max(3, size // 14)
    pad = stroke * 2
    block = Image.new("RGBA", (int(widest) + 2 * pad,
                               line_h * len(lines) + 2 * pad), (0, 0, 0, 0))
    draw = ImageDraw.Draw(block)
    for n, line in enumerate(lines):
        x = pad + (widest - font.getlength(line)) / 2
        draw.text((x, pad + n * line_h), line, font=font, fill="white",
                  stroke_width=stroke, stroke_fill="black")
    block = block.resize((int(block.width * TEXT_SQUEEZE), block.height),
                         Image.LANCZOS)
    # Towards the middle, but sitting just above the profile screenshot (shown
    # whole on black at the end) so it never covers the name or counts
    pw, ph = Image.open(profile).size
    shot_top = (H - min(H, ph * W / pw)) / 2
    bottom = int(shot_top - rng.uniform(20, 40))
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    img.paste(block, ((W - block.width) // 2, bottom - block.height), block)
    img.save(dst)


def still(index, secs, fade_in, fade_out):
    steps = [f"[{index}:v]scale={W}:{H}:force_original_aspect_ratio=decrease",
             f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black", "setsar=1",
             f"fps={FPS}", "format=yuv420p"]
    if fade_in:
        steps.append(f"fade=t=in:st=0:d={FLASH_HALF}:color=white")
    if fade_out:
        steps.append(f"fade=t=out:st={secs - FLASH_HALF}:d={FLASH_HALF}"
                     ":color=white")
    return ",".join(steps)


def render(p, text_png, dst):
    clip_secs = p["clip_secs"]
    total = clip_secs + 2 * IMAGE_SECS + PROFILE_SECS
    video, _ = probe(p["clip"])
    clip = [f"[0:v]trim=start={p['clip_start']}:duration={clip_secs}",
            "setpts=PTS-STARTPTS"]
    if video.get("color_transfer") in ("arib-std-b67", "smpte2084"):
        # iPhone HDR: tone-map to SDR so colours don't come out washed out
        clip += ["zscale=t=linear:npl=100", "format=gbrpf32le",
                 "zscale=p=bt709", "tonemap=hable:desat=0",
                 "zscale=t=bt709:m=bt709:r=tv"]
    clip += [f"fps={FPS}",
             f"scale={W}:{H}:force_original_aspect_ratio=increase",
             f"crop={W}:{H}", "setsar=1", "format=yuv420p",
             f"fade=t=out:st={clip_secs - FLASH_HALF}:d={FLASH_HALF}"
             ":color=white"]
    graph = ";".join([
        ",".join(clip) + "[c]",
        still(1, IMAGE_SECS, True, True) + "[i1]",
        still(2, IMAGE_SECS, True, True) + "[i2]",
        still(3, PROFILE_SECS, True, False) + "[pr]",
        "[c][i1][i2][pr]concat=n=4:v=1:a=0[seq]",
        "[seq][4:v]overlay=0:0:format=auto,format=yuv420p[v]",
        f"[5:a]atrim={p['song_start']}:{p['song_start'] + total},"
        "asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={total - 0.3}:d=0.3[a]",
    ])
    loop = lambda secs, path: ["-loop", "1", "-framerate", str(FPS),
                               "-t", str(secs), "-i", str(path)]
    cmd = (["ffmpeg", "-y", "-v", "error", "-i", str(p["clip"])]
           + loop(IMAGE_SECS, p["image1"]) + loop(IMAGE_SECS, p["image2"])
           + loop(PROFILE_SECS, p["profile"]) + loop(total, text_png)
           + ["-i", str(p["song"]), "-filter_complex", graph,
              "-map", "[v]", "-map", "[a]", "-t", f"{total:.3f}",
              "-c:v", "libx264", "-profile:v", "high", "-preset", "slow",
              "-crf", "17", "-pix_fmt", "yuv420p",
              "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
              "-map_metadata", "-1", "-movflags", "+faststart", str(dst)])
    subprocess.run(cmd, check=True)
    return total


def combo_key(clip, image1, image2, profile, hook):
    return (Path(clip).name, Path(image1).name, Path(image2).name,
            Path(profile).name, hook)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default="assets/raw")
    ap.add_argument("--sounds", default="assets/sounds")
    ap.add_argument("--out", default="remixes")
    ap.add_argument("--log", default="remix_log.csv")
    ap.add_argument("--seed", type=int)
    args = ap.parse_args()
    rng = random.Random(args.seed)

    clips, water, profiles = classify(Path(args.assets))
    if not clips or len(water) < 2 or not profiles:
        raise SystemExit(f"Need 1+ clip, 2+ water images and 1+ profile "
                         f"screenshot; found {len(clips)}, {len(water)}, "
                         f"{len(profiles)}")
    song = newest_song(Path(args.sounds))
    drop = find_drop(song)

    log_path = Path(args.log)
    used, last_hook = set(), None
    if log_path.exists():
        with log_path.open(newline="") as f:
            for row in csv.DictReader(f):
                used.add(combo_key(row["clip"], row["image1"], row["image2"],
                                   row["profile"], row["hook"]))
                last_hook = row["hook"]

    options = [c for c in itertools.product(
        clips, itertools.permutations(water, 2), profiles, HOOKS)
        if combo_key(c[0], *c[1], c[2], c[3]) not in used]
    if not options:
        raise SystemExit("Every combination has been used: add more assets")
    fresh_hook = [c for c in options if c[3] != last_hook]
    clip, (image1, image2), profile, hook = rng.choice(fresh_hook or options)

    # The clip runs until the drop, so the first flash lands on the beat
    # The clip is its whole pouring part (up to MAX_CLIP_SECS), and the song
    # starts early enough that the drop lands on the cut out of the clip
    _, clip_len = probe(clip)
    pour = POUR_START.get(clip.name, 0.3)
    clip_secs = math.floor(min(MAX_CLIP_SECS, clip_len - pour - 0.05)
                           * FPS) / FPS
    clip_start = round(rng.uniform(pour, clip_len - clip_secs - 0.05), 2)
    song_start = round(max(0.0, drop - clip_secs), 3)
    p = {"clip": clip, "clip_start": clip_start, "clip_secs": clip_secs,
         "image1": image1, "image2": image2, "profile": profile,
         "hook": hook, "song": song, "song_start": song_start}

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(used) + 1
    dst = out_dir / f"remix_{n:03d}.mp4"
    text_png = out_dir / f".text_{n:03d}.png"
    text_overlay(hook, profile, text_png, rng)
    total = render(p, text_png, dst)
    text_png.unlink()

    new_log = not log_path.exists()
    with log_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_log:
            writer.writeheader()
        writer.writerow({
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "output": dst.name, "clip": clip.name,
            "clip_start": p["clip_start"], "clip_secs": clip_secs,
            "image1": image1.name, "image2": image2.name,
            "profile": profile.name, "hook": hook, "song": song.name,
            "drop_secs": drop, "song_start": song_start,
        })
    print(f"{dst}  {total:.2f}s  drop={drop}s (song from {song_start}s)"
          f"  clip={clip.name}"
          f"@{p['clip_start']}  images={image1.name},{image2.name}  "
          f"profile={profile.name}\n  hook: {hook}")


if __name__ == "__main__":
    main()
