#!/usr/bin/env python3
"""Apple HK Store Pickup Monitor for iPhone 18 Pro Max
Includes MG2M4ZA/A for live Telegram testing.
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
    # 🧪 測試用型號（確認 Telegram 通知運作）
    "MG2M4ZA/A": "【測試型號】iPhone MG2M4ZA/A",

    # 🎯 iPhone 18 Pro Max 256GB
    "MJXN4ZA/A": "iPhone 18 Pro Max 256GB (顏色 1)",
    "MJXP4ZA/A": "iPhone 18 Pro Max 256GB (顏色 2)",
    "MJXQ4ZA/A": "iPhone 18 Pro Max 256GB (顏色 3)",
    "MJXR4ZA/A": "iPhone 18 Pro Max 256GB (顏色 4)",
    
    # 🎯 iPhone 18 Pro Max 512GB
    "MJXT4ZA/A": "iPhone 18 Pro Max 512GB (顏色 1)",
    "MJXU4ZA/A": "iPhone 18 Pro Max 512GB (顏色 2)",
    "MJXV4ZA/A": "iPhone 18 Pro Max 512GB (顏色 3)",
    "MJXW4ZA/A": "iPhone 18 Pro Max 512GB (顏色 4)",
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
BUY_URL = "https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro"

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
    part_params = "&".join([f"parts.{i}={p}" for i, p in enumerate(PARTS.keys())])
    url = f"https://www.apple.com/hk-zh/shop/fulfillment-messages?pl=true&{part_params}&location=Hong%20Kong"
    
    print(f"[{hkt_now()}] Fetching batch data from Apple HK using curl...")
    
    curl_cmd = [
        "curl", "-s", "-L",
        "--compressed",
        "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "-H", "Accept: application/json, text/javascript, */*; q=0.01",
        "-H", "Accept-Language: zh-HK,zh-TW;q=0.9,zh;q=0.8,en-US;q=0.7,en;q=0.6",
        "-H", "Referer: https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro",
        url
    ]

    try:
        result = subprocess.run(curl_cmd, capture_output=True, text=True, timeout=15)
        if result.returncode != 0:
            raise Exception(f"curl process failed with code {result.returncode}")
        
        data = json.loads(result.stdout)
    except Exception as e:
        print(f"[Error] Failed to fetch Apple API via curl: {e}")
        return

    stores_data = data.get("body", {}).get("content", {}).get("pickupMessage", {}).get("stores", [])
    available_items = []

    for store in stores_data:
        store_number = store.get("storeNumber")
        if store_number not in STORES:
            continue
        
        store_name = STORES[store_number]
        parts_availability = store.get("partsAvailability", {})

        for part_code, part_info in parts_availability.items():
            if part_code in PARTS:
                pickup_display = part_info.get("pickupDisplay")
                # 當門市顯示有現貨可取 (available)
                if pickup_display == "available":
                    item_name = PARTS[part_code]
                    available_items.append(f"📱 **{item_name}**\n📍 門市：{store_name}")
                    print(f"[AVAILABLE] {item_name} -> {store_name}")

    now_str = hkt_now()
    if available_items:
        msg = (
            f"🎉 **Apple Store Pickup 現貨開放通知！**\n\n"
            + "\n-------------------\n".join(available_items)
            + f"\n\n🔗 立即預約/購買: {BUY_URL}\n"
            + f"⏰ 檢查時間: {now_str}"
        )
        print("Stock found! Sending Telegram notification...")
        send_telegram(msg)
    else:
        print("No stock available across all HK stores.")

if __name__ == "__main__":
    check_pickup()
