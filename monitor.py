#!/usr/bin/env python3
"""Check Apple Hong Kong in-store PICKUP availability for iPhone 18 Pro Max
at HK Apple Stores, send a Telegram alert, and log to SQLite.
"""
import datetime
import json
import os
import time
import urllib.parse
import urllib.request

def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        env_path = ".env"
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

# iPhone 18 Pro Max HK part numbers (all colours)
PARTS = {
    # 256GB 全四色
    "MJXN4ZA/A": "iPhone 18 Pro Max 256GB (顏色 1)",
    "MJXP4ZA/A": "iPhone 18 Pro Max 256GB (顏色 2)",
    "MJXQ4ZA/A": "iPhone 18 Pro Max 256GB (顏色 3)",
    "MJXR4ZA/A": "iPhone 18 Pro Max 256GB (顏色 4)",

    # 512GB 全四色
    "MJXT4ZA/A": "iPhone 18 Pro Max 512GB (顏色 1)",
    "MJXU4ZA/A": "iPhone 18 Pro Max 512GB (顏色 2)",
    "MJXV4ZA/A": "iPhone 18 Pro Max 512GB (顏色 3)",
    "MJXW4ZA/A": "iPhone 18 Pro Max 512GB (顏色 4)",
}

STORES = {
    "R409": "IFC Mall",
    "R485": "Causeway Bay",
    "R499": "Canton Road",
    "R610": "Festival Walk",
    "R673": "New Town Plaza",
    "R712": "apm",
}

COOKIE = "as_sfa=Mnxoa3xoa3x8emhfSEt8Y29uc3VtZXJ8aW50ZXJuZXR8MHwwfDE"

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_6_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1",
]

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HEARTBEAT = os.environ.get("HEARTBEAT", "0") == "1"
DISABLE_DB = os.environ.get("DISABLE_DB", "0") == "1"
RETRIES = max(1, int(os.environ.get("FETCH_RETRIES", "3")))
BACKOFF = float(os.environ.get("FETCH_BACKOFF", "2.0"))
BUY_URL = "https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro"

db = None
if not DISABLE_DB:
    try:
        import db as _db
        _db.init_db()
        db = _db
    except Exception as e:
        print(f"[warn] history disabled (db unavailable): {e}")


def _fetch_urllib(url, ua):
    headers = {
        "User-Agent": ua,
        "Cookie": COOKIE,
        "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
        "Referer": "https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro"
    }
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _fetch_cloudscraper(url, ua):
    import cloudscraper
    scraper = cloudscraper.create_scraper()
    headers = {
        "User-Agent": ua,
        "Cookie": COOKIE,
        "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
        "Referer": "https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro"
    }
    resp = scraper.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch(url):
    last_err = None
    for attempt in range(RETRIES):
        ua = USER_AGENTS[attempt % len(USER_AGENTS)]
        try:
            return _fetch_urllib(url, ua)
        except Exception as e:
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF * (2 ** attempt))
    try:
        return _fetch_cloudscraper(url, USER_AGENTS[0])
    except ImportError:
        pass
    except Exception as e:
        last_err = e
    raise last_err


def send_telegram(text):
    chat_str = os.environ.get("TELEGRAM_CHAT_ID", CHAT_ID)
    if not TOKEN or not chat_str:
        return
    chat_ids = [c.strip() for c in chat_str.replace(";", ",").split(",") if c.strip()]
    for cid in chat_ids:
        data = urllib.parse.urlencode({"chat_id": cid, "text": text}).encode()
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=30) as r:
                print(f"Telegram ({cid}):", r.read().decode()[:200])
        except Exception as e:
            print(f"[warn] Telegram send to {cid} failed: {e}")


def hkt_now():
    hkt = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(hkt).strftime("%d %b %Y, %I:%M %p HKT")


def check_store(sid, _query=None):
    verified = {}
    ready = []
    unverified_count = 0

    for part, colour_name in PARTS.items():
        # 指向香港地區的 buyability 接口
        url = f"https://www.apple.com/hk-zh/shop/buyability-message?parts.0={urllib.parse.quote(part, safe='')}&store={sid}"
        try:
            data = fetch(url)
            apu = data["body"]["content"]["buyabilityMessage"]["apu"]
            is_buyable = bool(apu.get(part, {}).get("isBuyable") is True)
            verified[part] = is_buyable
            if is_buyable:
                ready.append(colour_name)
        except Exception:
            unverified_count += 1

    if unverified_count == len(PARTS):
        return _finish(sid, "unverified", "fetch failed for all parts after retries", None)

    if ready:
        return _finish(sid, "available", ready, verified)

    return _finish(sid, "nostock", f"{len(verified)}/{len(PARTS)} colours confirmed, none buyable", verified)


def _finish(sid, state, detail, verified):
    if db is not None:
        try:
            db.record_check(sid, STORES[sid], state, detail)
            if verified:
                for part, buyable in verified.items():
                    db.record_colour(sid, STORES[sid], part, PARTS[part], buyable)
        except Exception as e:
            print(f"[warn] failed to log history for {sid}: {e}")
    return state, detail


def main():
    results = {sid: check_store(sid) for sid in STORES}
    now = hkt_now()

    for sid, (state, detail) in results.items():
        print(f"{STORES[sid]}: {state} — {detail}")

    available = [
        f"{c} @ {STORES[sid]}"
        for sid, (state, detail) in results.items()
        if state == "available"
        for c in detail
    ]
    unverified = [STORES[sid] for sid, (state, _) in results.items() if state == "unverified"]

    if available:
        send_telegram(
            "🎉 iPhone 18 Pro Max pickup AVAILABLE now: "
            + "; ".join(available)
            + f".\nReserve/buy: {BUY_URL} → choose 'Pick up' and pick the store.\n"
            + f"(checked {now})"
        )
        return

    if len(unverified) == len(STORES) and not HEARTBEAT:
        send_telegram(
            "⚠️ Monitor could NOT verify pickup status this run "
            f"({', '.join(unverified)}). Apple API may have changed or is blocking. "
            f"Will keep trying. ({now})"
        )
        return

    if HEARTBEAT:
        lines = []
        for sid, (state, detail) in results.items():
            if state == "nostock":
                lines.append(f"• {STORES[sid]}: no pickup stock (verified live ✓)")
            elif state == "unverified":
                lines.append(f"• {STORES[sid]}: ⚠️ could not verify — {detail}")
            elif state == "available":
                lines.append(f"• {STORES[sid]}: ✅ AVAILABLE — {', '.join(detail)}")
        send_telegram(
            "✅ Monitor is running. Live check just now:\n"
            + "\n".join(lines)
            + f"\nLast checked: {now}"
        )


if __name__ == "__main__":
    main()
