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

# Render/cloud requests can receive HTTP 403 from the upstream API.
# Use browser-like headers and a few request profiles before giving up.
session = requests.Session()

REQUEST_PROFILES = [
    {
        "User-Agent": (
            "Mozilla/5.0 (Linux; Android 10; K) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Mobile Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://draw.ar-lottery01.com/",
        "Origin": "https://draw.ar-lottery01.com",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    },
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://draw.ar-lottery01.com/",
        "Origin": "https://draw.ar-lottery01.com",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Dest": "empty",
    },
    {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://draw.ar-lottery01.com/",
    },
]

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
    last_error = None

    # Try the normal request first, then a few browser-like profiles.
    # Some upstream deployments reject generic cloud/server user agents.
    for idx, profile in enumerate(REQUEST_PROFILES, start=1):
        try:
            session.headers.clear()
            session.headers.update(profile)

            ts = int(time.time() * 1000)
            params = {"ts": ts, "pageSize": PAGE_SIZE}

            r = session.get(API_URL, params=params, timeout=15)

            if r.status_code == 403:
                print(
                    f"[WARN] upstream HTTP 403 with profile={idx}; "
                    "trying next request profile...",
                    flush=True,
                )
                last_error = RuntimeError("Upstream API returned HTTP 403")
                time.sleep(1)
                continue

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

        except Exception as e:
            last_error = e
            print(
                f"[WARN] fetch attempt {idx} failed: "
                f"{type(e).__name__}: {e}",
                flush=True,
            )
            time.sleep(1)

    raise last_error or RuntimeError("All upstream API attempts failed")


def save_rows_in_batches(rows, batch_size=50):
    # Keep the existing Supabase upsert behavior, but batch larger responses.
    for start in range(0, len(rows), batch_size):
        save_to_supabase(rows[start:start + batch_size])


def collect_once():
    rows = fetch_results()
    save_rows_in_batches(rows)
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
