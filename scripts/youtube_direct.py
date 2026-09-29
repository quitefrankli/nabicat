"""Experimental direct audio download: python -m scripts.youtube_direct --help."""

import argparse
import http.cookiejar
import json
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from web_app.config import ConfigManager


def video_id(value: str) -> str:
    if re.fullmatch(r"[\w-]{11}", value, re.ASCII):
        return value
    parsed = urllib.parse.urlsplit(value)
    if parsed.hostname == "youtu.be":
        candidate = parsed.path.strip("/")
    elif parsed.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        candidate = (
            parsed.path.removeprefix("/shorts/").rstrip("/")
            if parsed.path.startswith("/shorts/")
            else urllib.parse.parse_qs(parsed.query).get("v", [""])[0]
        )
    else:
        raise ValueError("Expected a YouTube URL or video ID")
    if not re.fullmatch(r"[\w-]{11}", candidate, re.ASCII):
        raise ValueError("Invalid YouTube video ID")
    return candidate


def extract_player(page: str) -> dict:
    for match in re.finditer(r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*', page):
        try:
            player, _ = json.JSONDecoder().raw_decode(page[match.end():])
        except json.JSONDecodeError:
            continue
        if isinstance(player, dict):
            return player
    raise ValueError("No player response in page; consent or a browser challenge may be required")


def download(value: str, output: Path, opener) -> dict:
    if output.exists():
        raise FileExistsError("Output already exists")
    config = ConfigManager()
    url = config.tubio.youtube_watch_url_template.format(video_id=video_id(value))
    with opener.open(urllib.request.Request(url), timeout=config.youtube_direct_timeout_s) as response:
        page = response.read(config.youtube_direct_max_page_bytes + 1)
    if len(page) > config.youtube_direct_max_page_bytes:
        raise ValueError("Player page exceeds size limit")
    player = extract_player(page.decode("utf-8"))
    status = player.get("playabilityStatus", {})
    if status.get("status") != "OK":
        raise ValueError(f"YouTube playback blocked ({status.get('status', 'unknown')}): {status.get('reason', 'no reason supplied')}")
    formats = player.get("streamingData", {}).get("adaptiveFormats", [])
    audio = [fmt for fmt in formats if fmt.get("mimeType", "").startswith("audio/") and fmt.get("url")]
    if not audio:
        raise ValueError("No direct audio URL available; player may require signature cipher decoding or a playback token")
    selected = max(audio, key=lambda fmt: (fmt.get("mimeType", "").startswith("audio/mp4"), fmt.get("bitrate", 0)))
    media_url = urllib.parse.urlsplit(selected["url"])
    if media_url.scheme != "https" or not (media_url.hostname or "").endswith(".googlevideo.com"):
        raise ValueError("Unexpected media host")
    expected = int(selected.get("contentLength", 0))
    if expected > config.youtube_direct_max_media_bytes:
        raise ValueError("Media exceeds size limit")
    # Exclusive creation prevents overwriting either media or the cookie file.
    with output.open("xb") as destination:
        try:
            with opener.open(urllib.request.Request(selected["url"]), timeout=config.youtube_direct_timeout_s) as response:
                if response.status != 200:
                    raise ValueError("Unexpected partial media response")
                if not response.headers.get("Content-Type", "").startswith(("audio/", "video/", "application/octet-stream")):
                    raise ValueError("Server returned non-media content")
                count = 0
                while chunk := response.read(config.youtube_direct_chunk_bytes):
                    count += len(chunk)
                    if count > config.youtube_direct_max_media_bytes:
                        raise ValueError("Media exceeds size limit")
                    destination.write(chunk)
                if not count or (expected and count != expected):
                    raise ValueError("Incomplete media response")
        except BaseException:
            output.unlink(missing_ok=True)
            raise
    return {"bytes": count, "mime_type": selected["mimeType"], "format": selected.get("itag")}


def main() -> int:
    parser = argparse.ArgumentParser(description="Try direct YouTube audio URLs without yt-dlp; no signature deciphering or transcoding.")
    parser.add_argument("url", help="YouTube watch/Shorts URL or video ID")
    parser.add_argument("--cookies", type=Path, help="Netscape cookie file, read only; never saved")
    parser.add_argument("--output", type=Path, required=True, help="New output file (original audio container)")
    args = parser.parse_args()
    try:
        jar = http.cookiejar.MozillaCookieJar()
        if args.cookies:
            jar.load(str(args.cookies), ignore_discard=True)
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.addheaders = [("User-Agent", ConfigManager().tubio.cookie_keepalive_user_agent)]
        result = download(args.url, args.output, opener)
    except urllib.error.HTTPError as error:
        print(f"Download failed: HTTP {error.code}", file=sys.stderr)
        return 1
    except (ValueError, OSError, urllib.error.URLError) as error:
        # Do not print signed media URLs, cookie contents, or response bodies.
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        print(f"Download failed: {message}", file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
