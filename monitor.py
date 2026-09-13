#!/usr/bin/env python3
"""Diagnostic Apple HK Pickup Monitor
Fixed indentation issue to directly print store API payload to Telegram.
"""
import datetime
import json
import os
import subprocess
import urllib.parse
import urllib.request

def _load_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

_load_env()

PARTS = {
    "MG2M4ZA/A": "iPhone Air 256GB",
}

STORES = {
    "R409": "IFC Mall 中環",
    "R485": "Causeway Bay 銅鑼灣",
    "R499": "Canton Road 尖沙咀",
    "R610": "Festival Walk 九龍塘",
    "R673": "New Town Plaza 沙田",
    "R712": "apm 觀塘",
}

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        print("[Error] TELEGRAM_TOKEN or TELEGRAM_CHAT_ID is missing!")
        return False
    
    chat_ids = [c.strip() for c in CHAT_ID.replace(";", ",").split(",") if c.strip()]
    success = True
    for cid in chat_ids:
        data = urllib.parse.urlencode({"chat_id": cid, "text": text}).encode()
        url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
        try:
            req = urllib.request.Request(url, data=data)
            with urllib.request.urlopen(req, timeout=10) as r:
                res = json.loads(r.read().decode())
                if not res.get("ok"):
                    print(f"[warn] Telegram API error for {cid}: {res}")
                    success = False
        except Exception as e:
            print(f"[warn] Telegram send failed for {cid}: {e}")
            success = False
    return success

def hkt_now():
    hkt = datetime.timezone(datetime.timedelta(hours=8))
    return datetime.datetime.now(hkt).strftime("%d %b %Y, %I:%M %p HKT")

def check_pickup():
    part_code = "MG2M4ZA/A"
    url = f"https://www.apple.com/hk-zh/shop/fulfillment-messages?pl=true&parts.0={part_code}&location=Hong%20Kong"
    
    print(f"[{hkt_now()}] Fetching raw response for {part_code}...")
    
    curl_cmd = [
        "curl", "-s", "-L",
        "--compressed",
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "-H", "Accept: application/json, text/javascript, */*; q=0.01",
        "-H", "Accept-Language: zh-HK,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
        "-H", "Referer: https://www.apple.com/hk-zh/shop/buy-iphone",
        url
    ]

    try:
        result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=15)
        data = json.loads(result.stdout)
        stores_data = data.get("body", {}).get("content", {}).get("pickupMessage", {}).get("stores", [])

        debug_lines = []
        for store in stores_data:
            store_number = store.get("storeNumber")
            store_name = STORES.get(store_number, store_number)
            parts_availability = store.get("partsAvailability", {})
            part_info = parts_availability.get(part_code, {})

            pickup_display = part_info.get("pickupDisplay")
            pickup_quote = part_info.get("pickupSearchQuote")
            store_pick_eligible = part_info.get("storeSelectionEnabled")

            info_str = f"• {store_name}:\n  display='{pickup_display}'\n  quote='{pickup_quote}'\n  eligible={store_pick_eligible}"
            print(info_str)
            debug_lines.append(info_str)

        if debug_lines:
            report = f"🔍 **Apple API 門市狀態診斷報告 ({part_code})**\n\n" + "\n\n".join(debug_lines) + f"\n\n⏰ {hkt_now()}"
            send_telegram(report)
        else:
            send_telegram("⚠️ 診斷警告：API 未回傳任何門市資料，請檢查 API 網址！")

    except Exception as e:
        err_msg = f"[Error] API fetch failed: {e}"
        print(err_msg)
        send_telegram(f"❌ 診斷失敗: {e}")

if __name__ == "__main__":
    check_pickup()
