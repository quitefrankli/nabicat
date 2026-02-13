#!/usr/bin/env python3
"""
YouTube cookie session keepalive script.
Run via cron every 2-3 days to prevent session expiry.

Example crontab entry:
  0 4 */2 * * /path/to/python /path/to/cookie_keepalive.py

Cookie file format: Netscape (exported via browser extension or yt-dlp)
"""

import http.cookiejar
import urllib.request
import sys
from pathlib import Path


COOKIE_PATH = Path.home() / ".nabicat" / "data" / "cookies.txt"
YOUTUBE_URL = "https://www.youtube.com/feed/subscriptions"


def load_cookies(cookie_path: Path) -> http.cookiejar.MozillaCookieJar:
    jar = http.cookiejar.MozillaCookieJar(cookie_path)
    jar.load(ignore_discard=True, ignore_expires=True)
    return jar


def keepalive(cookie_path: Path) -> bool:
    if not cookie_path.exists():
        print(f"Cookie file not found: {cookie_path}", file=sys.stderr)
        return False

    jar = load_cookies(cookie_path)
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    ]

    try:
        response = opener.open(YOUTUBE_URL, timeout=30)
        # Save updated cookies (session tokens may be refreshed)
        jar.save(ignore_discard=True, ignore_expires=True)
        print(f"Keepalive OK - status {response.status}")
        return True
    except Exception as e:
        print(f"Keepalive failed: {e}", file=sys.stderr)
        return False


if __name__ == "__main__":
    cookie_path = Path(sys.argv[1]) if len(sys.argv) > 1 else COOKIE_PATH
    success = keepalive(cookie_path)
    sys.exit(0 if success else 1)
