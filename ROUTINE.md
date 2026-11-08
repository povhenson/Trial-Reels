# Scheduled trial reel run

Each scheduled run makes one new remix reel and posts it to Instagram
(@pov_henson) as a trial reel through Zernio. Follow these steps in order.
If any step fails, stop: do not post, and say what failed.

## 1. Sync the assets from Google Drive

The source folder is **Trial Reels Automation** (Drive folder id
`1oOImWNHkA-DpPZ7gF9lIyFLq9I3MHJ5R`). List it with the Google Drive
connector (`parentId = '<id>'`, page through all results).

Download with `download_file_content`. The result is saved to a JSON file;
decode it with `jq -r .content <file> | base64 -d > <dest>`.

| Files in the folder | Save to |
|---|---|
| Videos (`.mov`, `.mp4`) **except** names starting with `trial` | `assets/raw/<title>` |
| Images (`.png`, `.jpg`, `.jpeg`, `.webp`) | `assets/raw/<title>` |
| The **newest** audio file (`.mp3`, `.m4a`) only | `assets/sounds/song.mp3` |
| Fonts (`.ttf`, `.otf`) | `assets/fonts/<title>` |

`remix.py` sorts clips, water images and profile screenshots itself and
skips rejected files (`EXCLUDED`). Files over about 4.5MB cannot come
through the connector: skip them and mention it.

## 2. Make the reel

```
python3 remix.py
```

It picks a combination (clip, two water images, profile screenshot, hook)
that is not in `remix_log.csv`, lines the song's beat drop up with the cut
out of the clip, and writes `remixes/remix_NNN.mp4`. Check the file exists
and is 4 to 7 seconds long (`ffprobe`).

## 3. Upload it to Zernio

1. Zernio `call_tool` → `media_get_media_presigned_url` with the file name
   and `content_type: video/mp4`.
2. `curl -sS -X PUT -H "Content-Type: video/mp4" --upload-file <file> '<uploadUrl>'`
   must return HTTP 200.

## 4. Post it as a trial reel

Zernio `call_tool` → `posts_create_post` with:

```json
{
  "content": "For example ahh creator💔💔",
  "media_items": [{"type": "video", "url": "<publicUrl>", "mimeType": "video/mp4"}],
  "platforms": [{
    "platform": "instagram",
    "accountId": "6aea75c699c42a39770ab55b",
    "platformSpecificData": {"trialParams": {"graduationStrategy": "MANUAL"}}
  }],
  "publish_now": true
}
```

The response must show `isTrialReel: True`.

## 5. Record it

Append a row to `zernio_posts.csv` (`posted,output,zernio_post_id`), then
commit `remix_log.csv` and `zernio_posts.csv` and push to `master`, so the
next run never repeats this combination.
