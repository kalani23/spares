"""
stock_sync_final.py
====================
Daily stock sync: Shopify -> partworks.de -> Shopify

Pulls every SKU from the live Shopify store, looks up live stock status
on partworks.de via direct SKU search, and pushes the result back into
Shopify inventory levels.

Designed to run on GitHub Actions. If partworks.de blocks/strips the
response for this runner's IP (no proxy used), the script:
  1. Saves a checkpoint (everything processed so far + where it stopped)
  2. Exits with code 2
  3. The GitHub Actions workflow catches exit code 2 and triggers a new
     run of itself, which gets a fresh runner with a fresh IP and
     resumes exactly where the last run left off.

No manual intervention needed — it keeps re-triggering itself until the
full SKU list is processed.
"""

import json
import os
import random
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

SHOPIFY_TOKEN = "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"
SHOP          = "27dkze-zv.myshopify.com"
API_VERSION   = "2024-10"

BASE_URL    = "https://partworks.de"
SEARCH_URL  = BASE_URL + "/search/"
REQ_DELAY   = (1.0, 2.0)
MAX_RETRIES = 3

# How many consecutive blocked lookups before we give up on this IP
# and checkpoint/retrigger instead of continuing to burn requests.
BLOCK_THRESHOLD = 5

LOG_FILE        = "sync_run_log.json"
CHECKPOINT_FILE = "checkpoint.json"

SHOPIFY_BASE = f"https://{SHOP}/admin/api/{API_VERSION}"
GRAPHQL_URL  = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"

SHOPIFY_HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_TOKEN,
    "Content-Type": "application/json",
}

# Confirmed-safe header set for partworks.de — do not add Accept-Encoding,
# Sec-Fetch-*, Cache-Control, Connection, or Upgrade-Insecure-Requests.
# Any of those trigger partworks.de's bot filter (stripped page, no data).
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

session = requests.Session()
session.headers.update(HEADERS)


# ── Helpers ───────────────────────────────────────────────────────────────────

def clean_sku(sku) -> str:
    sku = str(sku or "").strip()
    sku = sku.lstrip("'")
    sku = re.sub(r"^Part\s+Works\s+", "", sku, flags=re.IGNORECASE)
    if sku.endswith(".0"):
        sku = sku[:-2]
    return sku.strip()


def gql(query: str, variables: dict = None) -> dict:
    r = requests.post(
        GRAPHQL_URL,
        headers=SHOPIFY_HEADERS,
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    result = r.json()
    if r.status_code >= 400:
        print(result)
        raise RuntimeError(f"Shopify GraphQL HTTP error {r.status_code}")
    if "errors" in result:
        print(result["errors"])
        raise RuntimeError("Shopify GraphQL error")
    return result


def get_shopify_products() -> list[dict]:
    products = []
    cursor = None
    page = 1

    while True:
        print(f"  Fetching Shopify products page {page}...")
        query = """
        query($cursor: String) {
          products(first: 100, after: $cursor) {
            edges {
              cursor
              node {
                id
                title
                variants(first: 100) {
                  edges {
                    node {
                      id
                      sku
                      inventoryItem { id }
                    }
                  }
                }
              }
            }
            pageInfo { hasNextPage }
          }
        }
        """
        result = gql(query, {"cursor": cursor})
        data = result.get("data", {}).get("products", {})
        edges = data.get("edges", [])

        for edge in edges:
            product = edge["node"]
            for v_edge in product.get("variants", {}).get("edges", []):
                variant = v_edge["node"]
                original_sku = variant.get("sku") or ""
                sku = clean_sku(original_sku)
                if not sku:
                    continue
                products.append({
                    "product_id":        product["id"],
                    "product_title":     product["title"],
                    "variant_id":        variant["id"],
                    "inventory_item_id": variant["inventoryItem"]["id"],
                    "sku":               sku,
                    "original_sku":      original_sku,
                })

        if not data.get("pageInfo", {}).get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"] if edges else None
        if not cursor:
            break
        page += 1
        time.sleep(0.5)

    return products


def get_primary_location_id(products: list[dict]) -> int:
    if not products:
        raise RuntimeError("No Shopify products with SKUs found")

    for product in products[:20]:
        iid = str(product.get("inventory_item_id", "")).split("/")[-1]
        if not iid:
            continue
        r = requests.get(
            f"{SHOPIFY_BASE}/inventory_levels.json?inventory_item_ids={iid}",
            headers=SHOPIFY_HEADERS,
            timeout=20,
        )
        data = r.json()
        levels = data.get("inventory_levels", [])
        if levels:
            location_id = levels[0]["location_id"]
            print(f"  Auto-detected location ID: {location_id}")
            return location_id

    raise RuntimeError("Could not auto-detect Shopify location ID")


def lookup_sku_stock(sku: str, retries: int = MAX_RETRIES):
    """
    Returns True (in stock), False (out of stock), or None (lookup failed
    / ambiguous / blocked). No proxy rotation here — if blocked, the
    caller's empty-streak counter handles checkpoint + exit.
    """
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
                return None

            if len(r.text) < 150_000:
                # Soft block — stripped page
                return None

            soup = BeautifulSoup(r.text, "html.parser")
            h1 = soup.select_one("h1.product-title")
            sku_el = soup.select_one('[itemprop="sku"]')
            if not (h1 and sku_el):
                return None

            status_span = soup.select_one(".status-text")
            if not status_span:
                return None

            status_text = status_span.get_text(strip=True)
            return (
                any(p in status_text for p in ["Available", "verfügbar", "sofort"])
                and "Currently unavailable" not in status_text
                and "nicht verfügbar" not in status_text
            )

        except Exception:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
                continue
            return None

    return None


def set_inventory_level(inventory_item_id, location_id, available) -> bool:
    inventory_item_id = str(inventory_item_id).split("/")[-1]
    payload = {
        "location_id": location_id,
        "inventory_item_id": inventory_item_id,
        "available": available,
    }
    r = requests.post(
        f"{SHOPIFY_BASE}/inventory_levels/set.json",
        headers=SHOPIFY_HEADERS,
        json=payload,
        timeout=20,
    )
    if r.status_code != 200:
        print(f"[SHOPIFY UPDATE ERROR] {r.status_code} {r.text[:300]}")
        return False
    return True


def load_checkpoint():
    if Path(CHECKPOINT_FILE).exists():
        try:
            with open(CHECKPOINT_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            print("  [WARN] Checkpoint corrupt — starting fresh")
    return None


def save_checkpoint(products, resume_index, results):
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "products": products,
            "resume_index": resume_index,
            "results": results,
        }, f, default=str)


def clear_checkpoint():
    if Path(CHECKPOINT_FILE).exists():
        Path(CHECKPOINT_FILE).unlink()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Stuttgart Spares — Stock Sync (Shopify -> partworks.de -> Shopify)")
    print("=" * 60)

    checkpoint = load_checkpoint()

    if checkpoint:
        print("\n-> Resuming from checkpoint...")
        products     = checkpoint["products"]
        start_index  = checkpoint["resume_index"]
        results      = checkpoint["results"]
        print(f"  Resuming at {start_index}/{len(products)}")
    else:
        print("\n-> Step 1: Pulling existing SKUs from Shopify...")
        products = get_shopify_products()
        print(f"  Found {len(products)} variants with SKUs")
        start_index = 0
        results = []

    print("\n-> Fetching primary location...")
    location_id = get_primary_location_id(products)
    print(f"  Location ID: {location_id}")

    print(f"\n-> Step 2: Checking stock on partworks.de "
          f"({start_index}/{len(products)} done so far)...\n")

    empty_streak = 0
    in_stock_count     = sum(1 for r in results if r.get("in_stock") is True)
    out_of_stock_count = sum(1 for r in results if r.get("in_stock") is False)
    unknown_count      = sum(1 for r in results if r.get("in_stock") is None)

    for i in range(start_index, len(products)):
        item = products[i]
        sku = clean_sku(item["sku"])
        in_stock = lookup_sku_stock(sku)

        if in_stock is None:
            status_label = "UNKNOWN"
            unknown_count += 1
            empty_streak += 1
        elif in_stock:
            status_label = "IN STOCK"
            in_stock_count += 1
            empty_streak = 0
        else:
            status_label = "OUT OF STOCK"
            out_of_stock_count += 1
            empty_streak = 0

        print(f"  [{i+1}/{len(products)}] {sku:<15} {status_label:<14} {item['product_title'][:45]}")
        results.append({**item, "sku": sku, "in_stock": in_stock})

        # ── Blocked: save checkpoint and exit with code 2 ───────────────────
        if empty_streak >= BLOCK_THRESHOLD:
            print(f"\n  BLOCKED: {empty_streak} consecutive unknown lookups.")
            print(f"  Saving checkpoint at index {i+1} and exiting (code 2)")
            print(f"  to trigger a fresh-IP retry.")
            # Rewind a little so the blocked ones get retried on fresh IP
            resume_from = max(0, i + 1 - BLOCK_THRESHOLD)
            save_checkpoint(products, resume_from, results[:resume_from])
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, default=str)
            sys.exit(2)

        if (i + 1) % 25 == 0:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=2, default=str)

    # ── Finished all products cleanly ───────────────────────────────────────
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, default=str)
    clear_checkpoint()

    print(f"\n  In stock:     {in_stock_count}")
    print(f"  Out of stock: {out_of_stock_count}")
    print(f"  Unknown:      {unknown_count}")

    print("\n-> Step 3: Updating Shopify inventory levels...\n")

    updated, skipped, failed = 0, 0, 0

    for i, item in enumerate(results, 1):
        if item["in_stock"] is None:
            skipped += 1
            continue

        available = 10 if item["in_stock"] else 0
        ok = set_inventory_level(item["inventory_item_id"], location_id, available)

        if ok:
            updated += 1
        else:
            failed += 1
            print(f"  [FAILED] {item['sku']} - could not update inventory")

        if i % 25 == 0:
            print(f"  {i}/{len(results)} processed...")
            time.sleep(0.5)

    print(f"\n{'=' * 60}")
    print("Sync complete")
    print(f"  Updated: {updated}")
    print(f"  Skipped unknown: {skipped}")
    print(f"  Failed: {failed}")
    print(f"  Full log: {LOG_FILE}")
    print(f"{'=' * 60}")
    sys.exit(0)


if __name__ == "__main__":
    main()
