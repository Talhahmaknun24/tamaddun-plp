#!/usr/bin/env python3
"""Tamaddun Hub scheduled posts — queue + Friday Jummah greeting.

What it does:
  - Posts due items from schedule.json (queue) to @tamaddunhub via the bot.
  - Every Friday posts the Jummah greeting with the EXISTING repo image
    (previews/jumma-mubarak-post-preview.jpg — the live non-AI preview;
    no image is ever generated or AI-processed here).
  - Marks posted items done in schedule.json and remembers the last
    Jummah post date in schedule_state.json, so nothing posts twice.

Usage:
  python3 scheduled_posts.py            # dry-run (default): prints what WOULD post
  python3 scheduled_posts.py --live     # actually posts, marks done, reacts ❤️
  python3 scheduled_posts.py --via-admin
          # post through the Hub admin panel API instead of the bot directly.
          # Needs TAMADDUN_ADMIN_PASS in the environment (never in a file).

Token: read from TELEGRAM_BOT_TOKEN env, else config.json next to this
script (GitHub Actions version: env only). The channel is @tamaddunhub.
The script never posts in dry-run mode and never touches the channel
photo. A bot may set only ONE reaction per message, so it adds a single ❤️.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
    DHAKA = ZoneInfo("Asia/Dhaka")
except Exception:
    from datetime import timezone
    DHAKA = timezone.utc

BASE = os.path.dirname(os.path.abspath(__file__))
SCHEDULE = os.path.join(BASE, "schedule.json")
STATE = os.path.join(BASE, "schedule_state.json")
ADMIN_API = "https://tamaddun-hub-admin.vercel.app/api/telegram"
REACTION = "❤️"


def log(msg):
    print(msg, flush=True)


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel = os.environ.get("TELEGRAM_CHANNEL", "") or "@tamaddunhub"
    if token:
        return {"bot_token": token, "channel": channel}
    cfg_path = os.path.join(BASE, "config.json")
    with open(cfg_path, encoding="utf-8") as f:
        return json.load(f)


def tg_json(token, method, payload, timeout=60):
    """Bot API JSON call. The token is never included in error messages."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=data,
        headers={"Content-Type": "application/json",
                 "User-Agent": "TamaddunHubBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} failed: HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(f"{method} failed: {type(e).__name__}")


def tg_send_photo_file(token, chat_id, photo_path, caption, timeout=90):
    """sendPhoto with a local file (multipart upload)."""
    boundary = "----TamaddunHubBoundary" + os.urandom(8).hex()
    with open(photo_path, "rb") as f:
        photo_bytes = f.read()
    filename = os.path.basename(photo_path)
    ctype = "image/png" if filename.lower().endswith(".png") else "image/jpeg"
    body = b""
    for name, value in (("chat_id", chat_id), ("caption", caption),
                        ("parse_mode", "HTML")):
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        body += f"{value}\r\n".encode()
    body += f"--{boundary}\r\n".encode()
    body += (f'Content-Disposition: form-data; name="photo"; '
             f'filename="{filename}"\r\n').encode()
    body += f"Content-Type: {ctype}\r\n\r\n".encode()
    body += photo_bytes
    body += f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendPhoto", data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "User-Agent": "TamaddunHubBot/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"sendPhoto failed: HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(f"sendPhoto failed: {type(e).__name__}")


def post_via_bot(cfg, image, caption):
    """Send one item via the bot. Returns (ok, message_id or '').
    image may be a URL (sent as-is) or a local file path (uploaded)."""
    chat_id = cfg["channel"]
    if image.startswith("http://") or image.startswith("https://"):
        res = tg_json(cfg["bot_token"], "sendPhoto",
                      {"chat_id": chat_id, "photo": image, "caption": caption,
                       "parse_mode": "HTML"})
    elif os.path.isfile(image):
        res = tg_send_photo_file(cfg["bot_token"], chat_id, image, caption)
    elif not image:
        res = tg_json(cfg["bot_token"], "sendMessage",
                      {"chat_id": chat_id, "text": caption,
                       "parse_mode": "HTML", "disable_web_page_preview": False})
    else:
        raise RuntimeError(f"image not found: {image}")
    if not res.get("ok"):
        raise RuntimeError(f"Telegram send not ok: {res.get('description', '')}")
    msg = res.get("result") or {}
    return True, msg.get("message_id", "")


def react(cfg, chat_id, message_id):
    try:
        res = tg_json(cfg["bot_token"], "setMessageReaction",
                      {"chat_id": chat_id, "message_id": message_id,
                       "reaction": [{"type": "emoji", "emoji": REACTION}]})
        return bool(res.get("ok"))
    except Exception:
        return False


def post_via_admin(caption, image):
    """Post through the Hub admin panel API. Needs TAMADDUN_ADMIN_PASS
    in the environment. Untested without the passcode (held by the user)."""
    passwd = os.environ.get("TAMADDUN_ADMIN_PASS", "")
    if not passwd:
        raise RuntimeError("TAMADDUN_ADMIN_PASS env var not set — "
                           "cannot post via the admin panel API")
    payload = {"passcode": passwd, "text": caption}
    if image:
        payload["photo"] = image
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        ADMIN_API, data=data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            res = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"admin API failed: HTTP {e.code}")
    except Exception as e:
        raise RuntimeError(f"admin API failed: {type(e).__name__}")
    if not res.get("ok"):
        raise RuntimeError(f"admin API not ok: {res.get('error', '')}")
    return True, ""  # admin API does not return the message id


def main():
    ap = argparse.ArgumentParser(description="Tamaddun Hub scheduled posts (dry-run by default)")
    ap.add_argument("--live", action="store_true", help="actually post (default: dry-run)")
    ap.add_argument("--via-admin", action="store_true",
                    help="post through the admin panel API (needs TAMADDUN_ADMIN_PASS)")
    ap.add_argument("--date", help="pretend today is YYYY-MM-DD (testing only)")
    args = ap.parse_args()

    today = args.date or datetime.now(DHAKA).strftime("%Y-%m-%d")
    weekday = datetime.strptime(today, "%Y-%m-%d").strftime("%A")
    schedule = load_json(SCHEDULE, {"jummah": {}, "queue": []})
    state = load_json(STATE, {"last_jummah_posted": "", "posted": []})
    log(f"today: {today} ({weekday}); mode: {'LIVE' if args.live else 'DRY-RUN (nothing will be posted)'}")

    due = []  # (kind, ref, image, caption)
    # 1. Queue items due today (or earlier) and not done.
    for item in schedule.get("queue", []):
        if item.get("done"):
            continue
        if item.get("date", "9999") <= today:
            due.append(("queue", item.get("date", "?"),
                        item.get("image", ""), item.get("caption", "")))
    # 2. Friday Jummah greeting, once per Friday.
    j = schedule.get("jummah", {}) or {}
    if j.get("enabled") and weekday == "Friday" and \
            state.get("last_jummah_posted") != today:
        due.append(("jummah", today, j.get("image", ""), j.get("caption", "")))

    if not due:
        log("nothing due today.")
        return 0

    cfg = None if args.via_admin else load_config()
    posted_any = False
    for kind, ref, image, caption in due:
        short = (caption or "")[:80].replace("\n", " ")
        if not args.live:
            log(f"WOULD POST [{kind} {ref}]: image={image or '(none)'} caption='{short}…'")
            continue
        try:
            if args.via_admin:
                _, mid = post_via_admin(caption, image)
            else:
                _, mid = post_via_bot(cfg, image, caption)
                if mid:
                    react(cfg, cfg["channel"], mid)
            log(f"POSTED [{kind} {ref}] message_id={mid or '?'}")
            posted_any = True
            if kind == "queue":
                for item in schedule.get("queue", []):
                    if not item.get("done") and item.get("date") == ref and \
                            item.get("caption") == caption:
                        item["done"] = True
                        item["posted_at"] = today
                        if mid:
                            item["posted_message_id"] = mid
                        break
            else:
                state["last_jummah_posted"] = today
        except Exception as e:
            log(f"ERROR posting [{kind} {ref}]: {e}")
            save_json(SCHEDULE, schedule)
            save_json(STATE, state)
            return 1
    if args.live and posted_any:
        save_json(SCHEDULE, schedule)
        save_json(STATE, state)
        log("schedule.json + schedule_state.json updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
