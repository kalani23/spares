"""
merge_and_update.py
====================
Merges all results/results_*.json chunk files into one combined log,
then pushes inventory updates to Shopify one by one, then writes
sync_run_log.json + history.json for the dashboard.

No pre-validation of SKUs against Shopify - just attempts every update
directly. 404s (deleted products) fail fast and get skipped; everything
else updates normally.

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
GRAPHQL_URL  = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
SHOPIFY_HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_TOKEN,
    "Content-Type": "application/json",
}

# Shopify location IDs
CORNWALL_LOCATION_ID = 113368236375   # Cornwall warehouse — UK Stock (24hr dispatch)
EURO_LOCATION_ID     = 121881526615   # Test location — Euro Stock (5 day dispatch)


def set_euro_stock_metafield(product_id, in_stock, retries=4):
    """Write custom.euro_stock metafield so Liquid theme can read it."""
    mutation = """
    mutation setMeta($metafields: [MetafieldsSetInput!]!) {
      metafieldsSet(metafields: $metafields) {
        metafields { id }
        userErrors { field message }
      }
    }
    """
    variables = {
        "metafields": [{
            "ownerId": product_id,
            "namespace": "custom",
            "key": "euro_stock",
            "type": "number_integer",
            "value": "2" if in_stock else "0",
        }]
    }
    for attempt in range(retries):
        try:
            r = requests.post(
                GRAPHQL_URL,
                headers=SHOPIFY_HEADERS,
                json={"query": mutation, "variables": variables},
                timeout=(10, 15),
            )
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", 2.0))
                time.sleep(wait)
                continue
            result = r.json()
            errors = result.get("data", {}).get("metafieldsSet", {}).get("userErrors", [])
            if errors:
                print(f"    [META ERROR] {errors}")
                return False
            return True
        except Exception as e:
            print(f"    [META EXCEPTION] {e}")
            if attempt < retries - 1:
                time.sleep(2)
            continue
    return False


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
                timeout=(10, 15),
            )
        except requests.exceptions.RequestException as e:
            print(f"[SHOPIFY UPDATE ERROR] Request exception: {e}")
            return False
        if r.status_code == 200:
            return True
        if r.status_code == 404:
            print(f"[SHOPIFY UPDATE ERROR] 404 - skipping")
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

    # Diff against previous run for history tracking
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

    print(f"\n-> Updating Shopify inventory levels for {len(all_results)} SKUs...")
    print(f"   Writing Euro stock to 'Test location' ({EURO_LOCATION_ID})")
    print(f"   Cornwall warehouse ({CORNWALL_LOCATION_ID}) is managed manually by Paul\n")

    updated, skipped, failed = 0, 0, 0

    for i, item in enumerate(all_results, 1):
        if item.get("in_stock") is None:
            skipped += 1
            continue

        # Write partworks.de stock status to Euro Stock (Test location)
        # Cornwall warehouse is intentionally NOT touched by the sync —
        # Paul manages his own physical UK stock manually via Shopify admin
        euro_qty = 2 if item["in_stock"] else 0
        ok = set_inventory_level(item["inventory_item_id"], EURO_LOCATION_ID, euro_qty)

        # Also write euro_stock as a metafield so Liquid theme can read it
        if item.get("product_id"):
            set_euro_stock_metafield(item["product_id"], item["in_stock"])

        if ok:
            updated += 1
        else:
            failed += 1
            print(f"  [FAILED] {item['sku']} - could not update Euro stock")

        time.sleep(0.75)

        if i % 25 == 0:
            print(f"  {i}/{len(all_results)} processed...")

    print(f"\n{'=' * 60}")
    print("Merge + update complete")
    print(f"  Updated: {updated}")
    print(f"  Skipped (not found on supplier): {skipped}")
    print(f"  Failed:  {failed}")
    print(f"{'=' * 60}")

    # Append this run to history.json for the dashboard
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
        ][:50],
        "newly_out_of_stock_skus": [
            {"sku": r["sku"], "title": r["product_title"]} for r in newly_out_of_stock
        ][:50],
    }
    history.append(run_record)
    history = history[-90:]

    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2, default=str)
    print(f"\n  Run history updated -> {history_file} ({len(history)} runs kept)")


if __name__ == "__main__":
    main()