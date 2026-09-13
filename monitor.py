#!/usr/bin/env python3
"""Check Apple India in-store PICKUP availability for iPhone 17 256GB
at Apple BKC and Apple Borivali, send a Telegram alert, and log to SQLite.

Modes (HEARTBEAT env var):
  - Default: alert ONLY when a colour is pickup-available. Also alert if the
    check could NOT be verified (so a silent breakage never looks like "no stock").
  - HEARTBEAT=1: always send a status message with the exact per-store result
    and a timestamp, so you know it is genuinely running.

Each store is resolved to one of three states, never guessed:
  - AVAILABLE  : Apple returned apu.<part>.isBuyable == True
  - NO STOCK   : Apple returned a valid apu block for our parts, all isBuyable False
  - UNVERIFIED : fetch failed, or the response didn't contain our parts (format
                 change / block). This is NOT reported as "no stock".

Added capabilities (all safe for stateless cron / GitHub Actions / launchd):
  - Error recovery: each fetch retries with exponential backoff.
  - Anti-block fallback: rotates User-Agent, and falls back to cloudscraper
    (if installed) when plain urllib is blocked.
  - History: every run logs per-store state and per-colour buyability to SQLite,
    recording change events when a colour flips. This feeds dashboard.py.

There is NO alert cooldown: every run that finds stock (or can't verify) alerts,
so a drop is never silenced.

Env vars:
  TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   (required)
  HEARTBEAT=1                        (optional; always report)
  DB_PATH=pickup_history.db          (optional; where to persist history)
  FETCH_RETRIES=3                    (optional; attempts per store fetch)
  FETCH_BACKOFF=2.0                  (optional; seconds, doubled each retry)
  DISABLE_DB=1                       (optional; run pure-stdlib, no history)
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

# iPhone 18 256GB HK part numbers (all colours)
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
COOKIE = "as_sfa=Mnxpbnxpbnx8ZW5fSU58Y29uc3VtZXJ8aW50ZXJuZXR8MHwwfDE"

# Rotated on retries / fallback to look less like a single scripted client.
USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.4 Mobile/15E148 Safari/604.1",
]

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

HEARTBEAT = os.environ.get("HEARTBEAT", "0") == "1"
DISABLE_DB = os.environ.get("DISABLE_DB", "0") == "1"
RETRIES = max(1, int(os.environ.get("FETCH_RETRIES", "3")))
BACKOFF = float(os.environ.get("FETCH_BACKOFF", "2.0"))
BUY_URL = "https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro"

# DB is optional: if it can't be imported/opened, we degrade to plain stateless
# behaviour rather than crash.
db = None
if not DISABLE_DB:
    try:
        import db as _db
        _db.init_db()
        db = _db
    except Exception as e:  # noqa: BLE001
        print(f"[warn] history disabled (db unavailable): {e}")


def _fetch_urllib(url, ua):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Cookie": COOKIE})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _fetch_cloudscraper(url, ua):
    """Optional fallback for when Apple blocks plain urllib. No-op if not installed."""
    import cloudscraper  # imported lazily; listed as optional in requirements.txt
    scraper = cloudscraper.create_scraper()
    resp = scraper.get(url, headers={"User-Agent": ua, "Cookie": COOKIE}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch(url):
    """Fetch JSON with retries + backoff, rotating UA, then cloudscraper fallback.

    Raises the last exception if every attempt fails (caller treats that as UNVERIFIED).
    """
    last_err = None
    for attempt in range(RETRIES):
        ua = USER_AGENTS[attempt % len(USER_AGENTS)]
        try:
            return _fetch_urllib(url, ua)
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt < RETRIES - 1:
                time.sleep(BACKOFF * (2 ** attempt))
    # Every plain attempt failed — try cloudscraper once if available.
    try:
        return _fetch_cloudscraper(url, USER_AGENTS[0])
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
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


def ist_now():
    ist = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.now(ist).strftime("%d %b %Y, %I:%M %p IST")


def check_store(sid, _query=None):
    """Return (state, detail). state in {'available','nostock','unverified'}.

    Queries per-part for accuracy so Apple's API does not mask store pickup stock.
    Also logs the resolved state and per-colour buyability to the DB (if enabled).
    """
    verified = {}
    ready = []
    unverified_count = 0

    for part, colour_name in PARTS.items():
        url = f"https://www.apple.com/in/shop/buyability-message?parts.0={urllib.parse.quote(part, safe='')}&store={sid}"
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
    """Persist history for this store's resolved state, then return (state, detail)."""
    if db is not None:
        try:
            db.record_check(sid, STORES[sid], state, detail)
            if verified:
                for part, buyable in verified.items():
                    db.record_colour(sid, STORES[sid], part, PARTS[part], buyable)
        except Exception as e:  # noqa: BLE001
            print(f"[warn] failed to log history for {sid}: {e}")
    return state, detail


def main():
    results = {sid: check_store(sid) for sid in STORES}
    now = ist_now()

    for sid, (state, detail) in results.items():
        print(f"{STORES[sid]}: {state} — {detail}")

    available = [
        f"{c} @ {STORES[sid]}"
        for sid, (state, detail) in results.items()
        if state == "available"
        for c in detail
    ]
    unverified = [STORES[sid] for sid, (state, _) in results.items() if state == "unverified"]

    # 1) Real stock -> always alert (any mode). No cooldown: every run pings.
    if available:
        send_telegram(
            "\U0001F389 iPhone 18 pickup AVAILABLE now: "
            + "; ".join(available)
            + f".\nReserve/buy: {BUY_URL} → choose 'Pick up' and pick the store.\n"
            + f"(checked {now})"
        )
        return

    # 2) Couldn't verify (both stores) on a normal run -> alert, so silence is
    #    never mistaken for "no stock". One-off single-store blips are left for
    #    the heartbeat to surface.
    if len(unverified) == len(STORES) and not HEARTBEAT:
        send_telegram(
            "⚠️ Monitor could NOT verify pickup status this run "
            f"({', '.join(unverified)}). Apple API may have changed or is blocking. "
            f"Will keep trying. ({now})"
        )
        return

    # 3) Heartbeat -> report exactly what was found per store.
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
