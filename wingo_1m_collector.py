import os
import time
import threading
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify

API_URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"
SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")
POLL_SECONDS = int(os.environ.get("POLL_SECONDS", "20"))
PAGE_SIZE = int(os.environ.get("PAGE_SIZE", "50"))

app = Flask(__name__)
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (WinGo-1M-Collector)",
    "Accept": "application/json, text/plain, */*",
})

def clean(v):
    return "" if v is None else str(v).strip()

def get_issue(item):
    for k in ("issueNumber", "issue", "period", "id"):
        v = clean(item.get(k))
        if v:
            return v
    return None

def get_number(item):
    for k in ("number", "openNumber", "code", "result"):
        v = clean(item.get(k))
        for ch in v:
            if ch.isdigit():
                n = int(ch)
                if 0 <= n <= 9:
                    return n
    return None

def get_bs(item, number):
    for k in ("bigSmall", "big_small", "bs", "size"):
        v = clean(item.get(k)).lower()
        if v in ("big", "small"):
            return v.title()
    return ("Big" if number >= 5 else "Small") if number is not None else ""

def get_color(item, number):
    for k in ("color", "colour"):
        v = clean(item.get(k))
        if v:
            return v
    if number in (1, 3, 7, 9):
        return "Green"
    if number in (2, 4, 6, 8):
        return "Red"
    if number == 0:
        return "Violet+Red"
    if number == 5:
        return "Violet+Green"
    return ""

def fetch_results():
    ts = int(time.time() * 1000)
    r = session.get(f"{API_URL}?ts={ts}&pageSize={PAGE_SIZE}", timeout=15)
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data", payload)
    if isinstance(data, dict):
        rows = data.get("list") or data.get("records") or data.get("data") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    unique = {}
    for item in rows:
        if not isinstance(item, dict):
            continue
        issue = get_issue(item)
        number = get_number(item)
        if issue is None or number is None:
            continue
        unique[issue] = {
            "issue": issue,
            "number": number,
            "big_small": get_bs(item, number),
            "color": get_color(item, number),
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
    return list(unique.values())

def save_to_supabase(rows):
    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError("SUPABASE_URL / SUPABASE_KEY are missing.")
    if not rows:
        return
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }
    r = session.post(
        f"{SUPABASE_URL}/rest/v1/results",
        headers=headers,
        json=rows,
        timeout=20,
    )
    r.raise_for_status()

def collect_once():
    rows = fetch_results()
    save_to_supabase(rows)
    print(
        f"[OK] fetched={len(rows)} latest={rows[0]['issue'] if rows else 'none'}",
        flush=True,
    )

def collector_loop():
    print("[START] WinGo 1M collector running...", flush=True)
    while True:
        try:
            collect_once()
        except Exception as e:
            print(f"[ERROR] {type(e).__name__}: {e}", flush=True)
        time.sleep(POLL_SECONDS)

@app.get("/")
def home():
    return jsonify({"status": "online", "service": "wingo-1m-collector"})

@app.get("/health")
def health():
    return jsonify({"status": "ok"})

if __name__ == "__main__":
    threading.Thread(target=collector_loop, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
