"""
merge_and_update.py
====================
Merges all results/results_*.json chunk files into one combined log,
then pushes inventory updates to Shopify, then leaves sync_run_log.json
ready for generate_dashboard.py.

Usage:
    python merge_and_update.py
"""

import json
import os
import time
from pathlib import Path

import requests

SHOPIFY_TOKEN = os.environ.get("SHOPIFY_TOKEN", "")
if not SHOPIFY_TOKEN:
    raise RuntimeError("SHOPIFY_TOKEN environment variable not set.")

SHOP        = "27dkze-zv.myshopify.com"
API_VERSION = "2024-10"
RESULTS_DIR = "results"
LOG_FILE    = "sync_run_log.json"

SHOPIFY_BASE = f"https://{SHOP}/admin/api/{API_VERSION}"
SHOPIFY_HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_TOKEN,
    "Content-Type": "application/json",
}


def get_primary_location_id(products):
    if not products:
        raise RuntimeError("No products to detect location from")
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
            return levels[0]["location_id"]
    raise RuntimeError("Could not auto-detect Shopify location ID")


def set_inventory_level(inventory_item_id, location_id, available, retries=4):
    inventory_item_id = str(inventory_item_id).split("/")[-1]
    payload = {
        "location_id": location_id,
        "inventory_item_id": inventory_item_id,
        "available": available,
    }
    for attempt in range(retries):
        try:
            r = requests.post(
                f"{SHOPIFY_BASE}/inventory_levels/set.json",
                headers=SHOPIFY_HEADERS,
                json=payload,
                timeout=(10, 15),  # (connect_timeout, read_timeout) - explicit, no ambiguity
            )
        except requests.exceptions.RequestException as e:
            print(f"[SHOPIFY UPDATE ERROR] Request exception: {e}")
            return False
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            # Product/variant no longer exists in Shopify (likely deleted) -
            # no point retrying, this will never succeed.
            print(f"[SHOPIFY UPDATE ERROR] 404 - product/inventory item no longer exists, skipping")
            return False
        if r.status_code == 429:
            wait = float(r.headers.get("Retry-After", 2.0))
            print(f"    [429] Rate limited, waiting {wait}s (attempt {attempt+1}/{retries})")
            time.sleep(wait)
            continue
        print(f"[SHOPIFY UPDATE ERROR] {r.status_code} {r.text[:300]}")
        return False
    print(f"[SHOPIFY UPDATE ERROR] Exhausted retries after repeated 429s")
    return False


def get_valid_product_ids():
    """Fetch the set of product_ids that currently exist in Shopify -
    used to filter out stale results for products that have since been
    deleted (e.g. the dirty-SKU cleanup), avoiding pointless 404s."""
    valid_ids = set()
    url = f"{SHOPIFY_BASE}/products.json?limit=250&status=any&fields=id"
    page = 1
    while url:
        print(f"  Fetching valid product ids page {page}...")
        r = requests.get(url, headers=SHOPIFY_HEADERS, timeout=(10, 15))
        print(f"    HTTP {r.status_code}")
        try:
            data = r.json()
        except Exception as e:
            print(f"  [ERROR] Could not parse JSON response: {e}")
            print(f"  [ERROR] Raw response: {r.text[:500]}")
            break
        if "products" not in data:
            print(f"  [ERROR] Unexpected response shape: {data}")
            break
        page_count = len(data["products"])
        print(f"    Got {page_count} products on this page")
        for p in data["products"]:
            valid_ids.add(f"gid://shopify/Product/{p['id']}")
        link = r.headers.get("Link", "")
        next_url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                next_url = part.strip().split(";")[0].strip("<>")
        url = next_url
        page += 1
        time.sleep(0.3)
    return valid_ids


def main():
    print("=" * 60)
    print("Merging chunk results and updating Shopify")
    print("=" * 60)

    results_dir = Path(RESULTS_DIR)
    chunk_files = sorted(results_dir.glob("results_*.json"))

    if not chunk_files:
        print(f"[ERROR] No result files found in {RESULTS_DIR}/")
        return

    print(f"\n-> Found {len(chunk_files)} chunk result files")

    all_results = []
    for cf in chunk_files:
        with open(cf, encoding="utf-8") as f:
            chunk_results = json.load(f)
        all_results.extend(chunk_results)
        print(f"  {cf.name}: {len(chunk_results)} SKUs")

    print(f"\n  Total merged SKUs: {len(all_results)}")

    in_stock     = [r for r in all_results if r.get("in_stock") is True]
    out_of_stock = [r for r in all_results if r.get("in_stock") is False]
    not_found    = [r for r in all_results if r.get("in_stock") is None]

    print(f"  In stock:     {len(in_stock)}")
    print(f"  Out of stock: {len(out_of_stock)}")
    print(f"  Not found:    {len(not_found)}")

    # ── Diff against the previous run to find what actually changed ────────
    newly_in_stock, newly_out_of_stock = [], []
    if Path(LOG_FILE).exists():
        try:
            with open(LOG_FILE, encoding="utf-8") as f:
                previous_results = json.load(f)
            previous_status = {r["sku"]: r.get("in_stock") for r in previous_results}
            for r in all_results:
                prev = previous_status.get(r["sku"])
                curr = r.get("in_stock")
                if curr is True and prev is not True:
                    newly_in_stock.append(r)
                elif curr is False and prev is not False:
                    newly_out_of_stock.append(r)
            print(f"\n  Newly in stock since last run:     {len(newly_in_stock)}")
            print(f"  Newly out of stock since last run: {len(newly_out_of_stock)}")
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
            print("  [WARN] Could not diff against previous run log")

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Merged log written -> {LOG_FILE}")

    print("\n-> Fetching primary location...")
    location_id = get_primary_location_id(all_results)
    print(f"  Location ID: {location_id}")

    print("\n-> Checking which products still exist in Shopify...")
    valid_product_ids = get_valid_product_ids()
    print(f"  {len(valid_product_ids)} products currently exist")

    stale = [r for r in all_results if r.get("product_id") not in valid_product_ids]
    if stale:
        print(f"  Skipping {len(stale)} results for products no longer in Shopify "
              f"(likely deleted, e.g. dirty-SKU cleanup)")

    update_candidates = [r for r in all_results if r.get("product_id") in valid_product_ids]

    print(f"\n-> Updating Shopify inventory levels for {len(update_candidates)} SKUs "
          f"({len(stale)} stale skipped)...\n")

    updated, skipped, failed = 0, 0, 0

    for i, item in enumerate(update_candidates, 1):
        if item.get("in_stock") is None:
            skipped += 1
            continue

        available = 10 if item["in_stock"] else 0
        ok = set_inventory_level(item["inventory_item_id"], location_id, available)

        if ok:
            updated += 1
        else:
            failed += 1
            print(f"  [FAILED] {item['sku']} - could not update inventory")

        time.sleep(0.55)  # stay safely under Shopify's 2 calls/sec limit

        if i % 25 == 0:
            print(f"  {i}/{len(update_candidates)} processed...")

    print(f"\n{'=' * 60}")
    print("Merge + update complete")
    print(f"  Updated: {updated}")
    print(f"  Skipped (not found on supplier): {skipped}")
    print(f"  Failed:  {failed}")
    print(f"{'=' * 60}")

    # ── Append this run to history.json for the dashboard ──────────────────
    history_file = Path("history.json")
    history = []
    if history_file.exists():
        try:
            with open(history_file, encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError):
            history = []

    run_record = {
        "timestamp": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat(),
        "total_skus": len(all_results),
        "in_stock": len(in_stock),
        "out_of_stock": len(out_of_stock),
        "not_found": len(not_found),
        "newly_in_stock": len(newly_in_stock),
        "newly_out_of_stock": len(newly_out_of_stock),
        "updated": updated,
        "skipped": skipped,
        "failed": failed,
        "newly_in_stock_skus": [
            {"sku": r["sku"], "title": r["product_title"]} for r in newly_in_stock
        ][:50],  # cap to keep history.json from growing unbounded
        "newly_out_of_stock_skus": [
            {"sku": r["sku"], "title": r["product_title"]} for r in newly_out_of_stock
        ][:50],
    }
    history.append(run_record)
    history = history[-90:]  # keep last 90 days

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, default=str)
    print(f"\n  Run history updated -> {history_file} ({len(history)} runs kept)")


if __name__ == "__main__":
    main()
