"""
sync_chunk.py
====================
Processes ONE chunk of SKUs (~200) against partworks.de.

Run as: python sync_chunk.py <chunk_id>
e.g.    python sync_chunk.py 5    -> processes chunks/chunk_05.json

Reads:
    chunks/chunk_{id}.json           -> list of {sku, product_title, ...}
    chunks/checkpoint_{id}.json      -> resume point, if this chunk was
                                         previously blocked partway through

Writes:
    results/results_{id}.json        -> final/partial results for this chunk
    chunks/checkpoint_{id}.json       -> only written if blocked (exit 2)

Exit codes:
    0 -> chunk fully processed (clean or with some not-found SKUs, both fine)
    2 -> genuinely blocked (stripped bot-filter page), checkpoint saved,
         caller (the GitHub Actions workflow) should retrigger this chunk
"""

import json
import random
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL    = "https://partworks.de"
SEARCH_URL  = BASE_URL + "/search/"
REQ_DELAY   = (1.0, 2.0)
MAX_RETRIES = 3
BLOCK_THRESHOLD = 5

CHUNK_DIR   = "chunks"
RESULTS_DIR = "results"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)


def clean_sku(sku) -> str:
    sku = str(sku or "").strip()
    sku = sku.lstrip("'")
    sku = re.sub(r"^Part\s+Works\s+", "", sku, flags=re.IGNORECASE)
    if sku.endswith(".0"):
        sku = sku[:-2]
    return sku.strip()


def lookup_sku_stock(sku: str, retries: int = MAX_RETRIES):
    sku = clean_sku(sku)
    url = f"{SEARCH_URL}?qs={sku}"

    for attempt in range(retries):
        try:
            time.sleep(random.uniform(*REQ_DELAY))
            r = session.get(url, timeout=20, allow_redirects=True)

            if r.status_code != 200:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, True

            if len(r.text) < 150_000:
                if attempt < retries - 1:
                    time.sleep(2 ** attempt)
                    continue
                return None, True

            soup = BeautifulSoup(r.text, "html.parser")
            h1 = soup.select_one("h1.product-title")
            sku_el = soup.select_one('[itemprop="sku"]')
            if not (h1 and sku_el):
                return None, False

            status_span = soup.select_one(".status-text")
            if not status_span:
                return None, False

            status_text = status_span.get_text(strip=True)
            in_stock = (
                any(p in status_text for p in ["Available", "verfügbar", "sofort"])
                and "Currently unavailable" not in status_text
                and "nicht verfügbar" not in status_text
            )
            return in_stock, False

        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return None, True

    return None, True


def main():
    if len(sys.argv) < 2:
        print("Usage: python sync_chunk.py <chunk_id>")
        sys.exit(1)

    chunk_id = int(sys.argv[1])
    chunk_id_str = f"{chunk_id:02d}"

    chunk_file      = Path(CHUNK_DIR) / f"chunk_{chunk_id_str}.json"
    checkpoint_file = Path(CHUNK_DIR) / f"checkpoint_{chunk_id_str}.json"
    results_file    = Path(RESULTS_DIR) / f"results_{chunk_id_str}.json"

    Path(RESULTS_DIR).mkdir(exist_ok=True)

    if not chunk_file.exists():
        print(f"[ERROR] {chunk_file} not found")
        sys.exit(1)

    with open(chunk_file, encoding="utf-8") as f:
        products = json.load(f)

    start_index = 0
    results = []
    if checkpoint_file.exists():
        try:
            with open(checkpoint_file, encoding="utf-8") as f:
                cp = json.load(f)
            start_index = cp.get("resume_index", 0)
            results = cp.get("results", [])
            print(f"[chunk {chunk_id_str}] Resuming from checkpoint at index {start_index}/{len(products)}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"[chunk {chunk_id_str}] Checkpoint corrupt — starting fresh")

    print(f"[chunk {chunk_id_str}] Processing {len(products)} SKUs (starting at {start_index})")

    block_streak = 0

    for i in range(start_index, len(products)):
        item = products[i]
        sku = clean_sku(item["sku"])
        in_stock, was_blocked = lookup_sku_stock(sku)

        if was_blocked:
            label = "BLOCKED"
            block_streak += 1
        elif in_stock is None:
            label = "NOT FOUND"
            block_streak = 0
        elif in_stock:
            label = "IN STOCK"
            block_streak = 0
        else:
            label = "OUT OF STOCK"
            block_streak = 0

        print(f"  [chunk {chunk_id_str}] [{i+1}/{len(products)}] {sku:<15} {label:<12} {item['product_title'][:40]}")
        results.append({**item, "sku": sku, "in_stock": in_stock})

        if block_streak >= BLOCK_THRESHOLD:
            print(f"\n[chunk {chunk_id_str}] BLOCKED after {block_streak} consecutive failures.")
            resume_from = max(0, i + 1 - BLOCK_THRESHOLD)
            checkpoint = {
                "resume_index": resume_from,
                "results": results[:resume_from],
            }
            with open(checkpoint_file, "w", encoding="utf-8") as f:
                json.dump(checkpoint, f, default=str)
            with open(results_file, "w", encoding="utf-8") as f:
                json.dump(results, f, default=str)
            print(f"[chunk {chunk_id_str}] Checkpoint saved at index {resume_from}. Exiting 2.")
            sys.exit(2)

    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, default=str)
    if checkpoint_file.exists():
        checkpoint_file.unlink()

    in_stock_n = sum(1 for r in results if r["in_stock"] is True)
    out_n      = sum(1 for r in results if r["in_stock"] is False)
    notfound_n = sum(1 for r in results if r["in_stock"] is None)

    print(f"\n[chunk {chunk_id_str}] Done. In stock: {in_stock_n} | "
          f"Out of stock: {out_n} | Not found: {notfound_n}")
    sys.exit(0)


if __name__ == "__main__":
    main()
