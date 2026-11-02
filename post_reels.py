#!/usr/bin/env python3
"""Post the next unposted variant in ./variants/ as an Instagram trial reel.

Each run posts exactly one video, so a scheduler runs it once per slot
(9am, 12pm and 3pm). The caption is always the same, and trial reels are
always set to manual graduation: they stay trials until graduated by hand
in the Instagram app.

Picks the oldest unposted variant, preferring a different original than the
last post so the same video doesn't go up twice in a row. Every post is
appended to posted_log.csv and a variant is never posted twice.

Needs IG_ACCESS_TOKEN in the environment: a never-expiring system-user
token from the Povhenson business portfolio (Trial Reels Poster app) with
instagram_basic and instagram_content_publish. IG_USER_ID can override the
Instagram business account ID, which defaults to @pov_henson.

Usage:
  python3 post_reels.py [--variants variants] [--dry-run]
"""
import argparse
import csv
import datetime
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CAPTION = "For example ahh creator\U0001F494\U0001F494"
TRIAL_PARAMS = {"graduation_strategy": "MANUAL"}
GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "v24.0")
GRAPH = f"https://graph.facebook.com/{GRAPH_VERSION}"
RUPLOAD = f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}"
DEFAULT_IG_USER_ID = "17841477461100385"  # @pov_henson
LOG_FIELDS = ["posted", "variant", "original", "container_id", "media_id"]
STATUS_TIMEOUT_SECS = 600


def request(method, url, data=None, headers=None, body=None):
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        # Meta puts the useful reason in the body; never echo the token
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"{method} {url.split('?')[0]} failed "
                         f"({e.code}): {detail}") from None


def original_of(variant_name):
    return variant_name.rsplit("_v", 1)[0]


def pick_next(variants_dir, log_path):
    posted, last_original = set(), None
    if log_path.exists():
        with log_path.open(newline="") as f:
            for row in csv.DictReader(f):
                posted.add(row["variant"])
                last_original = row["original"]

    candidates = sorted(
        (p for p in variants_dir.glob("*.mp4") if p.name not in posted),
        key=lambda p: p.stat().st_mtime,
    )
    for p in candidates:
        if original_of(p.name) != last_original:
            return p
    return candidates[0] if candidates else None


def publish(video, token, user_id):
    # 1. Create a resumable-upload reel container, marked as a trial reel
    container = request("POST", f"{GRAPH}/{user_id}/media", data={
        "media_type": "REELS",
        "upload_type": "resumable",
        "caption": CAPTION,
        "trial_params": json.dumps(TRIAL_PARAMS),
        "access_token": token,
    })
    container_id = container["id"]
    print(f"container {container_id} created")

    # 2. Upload the video bytes
    data = video.read_bytes()
    request("POST", f"{RUPLOAD}/{container_id}", body=data, headers={
        "Authorization": f"OAuth {token}",
        "offset": "0",
        "file_size": str(len(data)),
    })
    print(f"uploaded {len(data) / 1e6:.1f} MB")

    # 3. Wait for Instagram to finish processing
    deadline = time.monotonic() + STATUS_TIMEOUT_SECS
    while True:
        status = request("GET", f"{GRAPH}/{container_id}?" + urllib.parse
                         .urlencode({"fields": "status_code,status",
                                     "access_token": token}))
        code = status.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise SystemExit(f"processing failed: {status}")
        if time.monotonic() > deadline:
            raise SystemExit(f"processing timed out: {status}")
        time.sleep(10)

    # 4. Publish
    result = request("POST", f"{GRAPH}/{user_id}/media_publish", data={
        "creation_id": container_id,
        "access_token": token,
    })
    return container_id, result["id"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="variants")
    ap.add_argument("--log", default="posted_log.csv")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be posted without posting")
    args = ap.parse_args()

    log_path = Path(args.log)
    video = pick_next(Path(args.variants), log_path)
    if video is None:
        raise SystemExit("Nothing left to post: run make_variants.py first")

    print(f"next: {video.name}")
    print(f"caption: {CAPTION}")
    print(f"trial: {TRIAL_PARAMS}")
    if args.dry_run:
        return

    token = os.environ.get("IG_ACCESS_TOKEN")
    user_id = os.environ.get("IG_USER_ID", DEFAULT_IG_USER_ID)
    if not token:
        raise SystemExit("Set IG_ACCESS_TOKEN first")
    token = token.strip()

    container_id, media_id = publish(video, token, user_id)
    new_log = not log_path.exists()
    with log_path.open("a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        if new_log:
            writer.writeheader()
        writer.writerow({
            "posted": datetime.datetime.now().isoformat(timespec="seconds"),
            "variant": video.name, "original": original_of(video.name),
            "container_id": container_id, "media_id": media_id,
        })
    print(f"posted as trial reel: media {media_id}")


if __name__ == "__main__":
    main()
