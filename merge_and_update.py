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


def set_inventory_level(inventory_item_id, location_id, available):
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

    with open(LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\n  Merged log written -> {LOG_FILE}")

    print("\n-> Fetching primary location...")
    location_id = get_primary_location_id(all_results)
    print(f"  Location ID: {location_id}")

    print(f"\n-> Updating Shopify inventory levels for {len(all_results)} SKUs...\n")

    updated, skipped, failed = 0, 0, 0

    for i, item in enumerate(all_results, 1):
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

        if i % 25 == 0:
            print(f"  {i}/{len(all_results)} processed...")
            time.sleep(0.5)

    print(f"\n{'=' * 60}")
    print("Merge + update complete")
    print(f"  Updated: {updated}")
    print(f"  Skipped (not found on supplier): {skipped}")
    print(f"  Failed:  {failed}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
