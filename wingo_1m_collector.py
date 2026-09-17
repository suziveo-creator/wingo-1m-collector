import os
import time
import requests
from datetime import datetime, timezone

# ============================================================
# WinGo 1M -> Supabase 24/7 Collector
# File: wingo_1m_collector.py
#
# Purpose:
#   Fetch WinGo 1 Minute history continuously and save new
#   results to Supabase. No prediction logic is included here.
# ============================================================

API_URL = "https://draw.ar-lottery01.com/WinGo/WinGo_1M/GetHistoryIssuePage.json"

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

POLL_SECONDS = 15
PAGE_SIZE = 50
REQUEST_TIMEOUT = 20

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError("SUPABASE_URL and SUPABASE_KEY environment variables are required.")

session = requests.Session()

# Browser-like headers. These are only normal HTTP headers; no proxy/bypass is used.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 13; K) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://draw.ar-lottery01.com/",
    "Origin": "https://draw.ar-lottery01.com",
    "Connection": "keep-alive",
}

def supabase_headers():
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates,return=minimal",
    }

def parse_rows(payload):
    """Handle the common response shapes used by the WinGo history endpoint."""
    data = payload.get("data", payload)

    if isinstance(data, dict):
        rows = data.get("list") or data.get("records") or data.get("rows") or []
    elif isinstance(data, list):
        rows = data
    else:
        rows = []

    parsed = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        issue = (
            row.get("issueNumber")
            or row.get("issue")
            or row.get("period")
            or row.get("periodNumber")
            or row.get("id")
        )

        number = (
            row.get("number")
            if row.get("number") is not None
            else row.get("openNumber")
        )

        if issue is None or number is None:
            continue

        try:
            issue = str(issue)
            number = int(str(number).strip())
        except (ValueError, TypeError):
            continue

        if not 0 <= number <= 9:
            continue

        big_small = "Big" if number >= 5 else "Small"

        # API formats can differ; preserve a supplied color when present.
        color = row.get("color")
        if color is None:
            code = str(row.get("code", "")).lower()
            if "red" in code:
                color = "Red"
            elif "green" in code:
                color = "Green"
            elif "violet" in code or "purple" in code:
                color = "Violet"
            else:
                color = ""

        parsed.append({
            "issue": issue,
            "number": number,
            "big_small": big_small,
            "color": str(color),
        })

    # newest/duplicate-safe order
    seen = set()
    out = []
    for r in parsed:
        if r["issue"] not in seen:
            seen.add(r["issue"])
            out.append(r)
    return out

def fetch_history():
    """
    Try a few ordinary URL/header combinations.

    If Render's outbound IP is blocked by the upstream WAF, all variants
    may still return 403. In that case the log clearly reports it.
    """
    ts = int(time.time() * 1000)

    attempts = [
        (
            f"{API_URL}?ts={ts}&pageSize={PAGE_SIZE}",
            HEADERS,
        ),
        (
            f"{API_URL}?ts={ts}",
            {
                **HEADERS,
                "Referer": "https://draw.ar-lottery01.com/WinGo/WinGo_1M/",
            },
        ),
        (
            f"{API_URL}?pageNo=1&pageSize={PAGE_SIZE}&ts={ts}",
            HEADERS,
        ),
    ]

    last_error = None

    for url, headers in attempts:
        try:
            response = session.get(
                url,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:
                return response.json()

            body = response.text.replace("\n", " ").strip()
            body = body[:350]

            if response.status_code == 403:
                print(f"[WARN] Upstream returned HTTP 403. Body: {body}")
            else:
                print(f"[WARN] Upstream HTTP {response.status_code}. Body: {body}")

            last_error = RuntimeError(f"HTTP {response.status_code}")

        except requests.RequestException as exc:
            last_error = exc
            print(f"[WARN] Request failed: {exc}")
        except ValueError as exc:
            last_error = exc
            print(f"[WARN] Invalid JSON response: {exc}")

    if last_error:
        raise last_error

    raise RuntimeError("No usable response from WinGo API.")

def save_to_supabase(rows):
    if not rows:
        return 0

    url = f"{SUPABASE_URL}/rest/v1/results"

    payload = [
        {
            "issue": r["issue"],
            "number": r["number"],
            "big_small": r["big_small"],
            "color": r["color"],
        }
        for r in rows
    ]

    response = session.post(
        url,
        headers=supabase_headers(),
        json=payload,
        timeout=REQUEST_TIMEOUT,
    )

    if response.status_code not in (200, 201, 204):
        body = response.text.replace("\n", " ").strip()[:500]
        raise RuntimeError(
            f"Supabase HTTP {response.status_code}: {body}"
        )

    return len(rows)

def count_remote_results():
    url = f"{SUPABASE_URL}/rest/v1/results"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Prefer": "count=exact",
        "Range": "0-0",
    }

    response = session.get(url, headers=headers, timeout=REQUEST_TIMEOUT)

    if response.status_code not in (200, 206):
        return "?"

    content_range = response.headers.get("Content-Range", "")
    if "/" in content_range:
        return content_range.split("/")[-1]

    return "?"

def collect_once():
    payload = fetch_history()
    rows = parse_rows(payload)

    if not rows:
        print("[INFO] API responded, but no valid result rows were found.")
        return

    # Send all fetched rows with upsert/merge-duplicates.
    # Existing issues are harmless because results.issue is the primary key.
    sent = save_to_supabase(rows)

    newest = rows[0]
    remote_count = count_remote_results()

    print(
        f"[OK] Latest: {newest['issue']} -> {newest['number']} "
        f"({newest['big_small']}) | "
        f"Fetched: {len(rows)} | Sent: {sent} | "
        f"Supabase results: {remote_count}"
    )

def main():
    print("=" * 64)
    print("WinGo 1M Online Collector")
    print("Supabase: connected")
    print(f"Poll interval: {POLL_SECONDS}s")
    print("=" * 64)

    consecutive_errors = 0

    while True:
        try:
            collect_once()
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            print(
                f"[ERROR] {type(exc).__name__}: {exc} "
                f"| retry #{consecutive_errors}"
            )

        # Keep the worker alive even when the upstream temporarily blocks/fails.
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
