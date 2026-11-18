#!/usr/bin/env python3
"""Tamaddun Hub: Blogger -> Telegram channel auto-poster (GitHub Actions version).

Runs on GitHub's servers every 10 minutes. Reads the bot token from the
TELEGRAM_BOT_TOKEN environment variable (GitHub Actions secret — never put
the token in this repo). Tracks posted entries in state.json next to this
script; the workflow commits state.json back after each run.
Local use: create config.json {"bot_token": "...", "channel": "@tamaddunhub"}
and run: python3 bot_post.py [--init | --test]
"""
import json
import os
import re
import sys
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(BASE, "config.json")
STATE = os.path.join(BASE, "state.json")
LOG = os.path.join(BASE, "bot.log")
FEED = "https://tamaddunhub.blogspot.com/feeds/posts/default?alt=json&max-results=5"
SITE = "https://tamaddunhub.blogspot.com/"
EMOJI = {"ইসলামিক": "🕌", "ক্যালেন্ডার": "📅", "ফন্ট": "🔤", "ব্যানার": "🎨",
         "সার্টিফিকেট": "📜", "অ্যাপ": "📱", "টেমপ্লেট": "📋", "পিএসডি": "🖼️"}


def log(msg):
    print(msg, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except OSError:
        pass


def load_config():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    channel = os.environ.get("TELEGRAM_CHANNEL", "")
    if token:
        return {"bot_token": token, "channel": channel or "@tamaddunhub"}
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)


def load_state():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"posted": []}


def save_state(state):
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def fetch_feed():
    req = urllib.request.Request(FEED, headers={"User-Agent": "TamaddunHubBot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def first_img(html_text):
    m = re.search(r'<img[^>]+src="([^"]+)"', html_text or "")
    return m.group(1) if m else None


def plain_text(html_text, limit=160):
    text = re.sub(r"<[^>]+>", " ", html_text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_entry(e):
    post_id = e.get("id", {}).get("$t", "")
    title_full = e.get("title", {}).get("$t", "")
    # SEO title is "Bangla title - English ... PLP File Download": keep the Bangla part.
    title = title_full.split(" - ")[0].strip() or title_full
    url = ""
    for link in e.get("link", []):
        if link.get("rel") == "alternate":
            url = link.get("href", "")
            break
    labels = [c.get("term", "") for c in e.get("category", [])]
    emoji = "📦"
    for lab in labels:
        if lab in EMOJI:
            emoji = EMOJI[lab]
            break
    content = e.get("content", {}).get("$t", "") or e.get("summary", {}).get("$t", "")
    desc = plain_text(content)
    caption = (f"{emoji} {title} — ফ্রি PLP ফাইল!\n{desc}\n\n"
               f"📥 ডাউনলোড: {url}\n🌐 আরও ডিজাইন: {SITE}")
    return {"id": post_id, "title": title, "url": url, "img": first_img(content),
            "caption": caption}


def tg_call(token, method, payload):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}", data=data,
        headers={"Content-Type": "application/json", "User-Agent": "TamaddunHubBot/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def post_entry(cfg, entry):
    if entry["img"]:
        res = tg_call(cfg["bot_token"], "sendPhoto",
                      {"chat_id": cfg["channel"], "photo": entry["img"],
                       "caption": entry["caption"]})
    else:
        res = tg_call(cfg["bot_token"], "sendMessage",
                      {"chat_id": cfg["channel"], "text": entry["caption"],
                       "disable_web_page_preview": False})
    return res.get("ok", False), res


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    cfg = load_config()
    if mode == "--test":
        me = tg_call(cfg["bot_token"], "getMe", {})
        log(f"getMe: {me}")
        if not me.get("ok"):
            log("ERROR: bot token invalid")
            return 1
        res = tg_call(cfg["bot_token"], "sendMessage",
                      {"chat_id": cfg["channel"],
                       "text": "✅ Tamaddun Hub অটো-পোস্ট চালু হলো! এখন থেকে সাইটে নতুন ডিজাইন আপলোড হলেই এখানে নিজে নিজে চলে আসবে। 🌐 https://tamaddunhub.blogspot.com/"})
        log(f"test message: ok={res.get('ok')}")
        return 0 if res.get("ok") else 1

    feed = fetch_feed()
    entries = [parse_entry(e) for e in feed.get("feed", {}).get("entry", [])]
    entries.reverse()  # oldest first
    state = load_state()
    posted = set(state.get("posted", []))
    new = [e for e in entries if e["id"] and e["id"] not in posted]
    if mode == "--init":
        for e in entries:
            posted.add(e["id"])
        state["posted"] = sorted(posted)
        save_state(state)
        log(f"init: marked {len(entries)} existing posts as posted, sent nothing")
        return 0
    if not new:
        log("run: no new posts")
        return 0
    for e in new:
        ok, res = post_entry(cfg, e)
        log(f"posted {e['title']}: ok={ok} msg_id={(res.get('result') or {}).get('message_id')}")
        if ok:
            posted.add(e["id"])
            state["posted"] = sorted(posted)
            save_state(state)
        else:
            log(f"ERROR response: {res}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
