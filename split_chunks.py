"""
split_chunks.py
====================
Pulls every SKU from Shopify (once) and splits them into chunks of
CHUNK_SIZE for parallel processing across GitHub Actions matrix jobs.

Writes:
    chunks/chunk_00.json, chunks/chunk_01.json, ...
    chunks/manifest.json   -> {"chunk_count": N, "total_skus": M}

Usage:
    python split_chunks.py
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
CHUNK_SIZE  = 200
CHUNK_DIR   = "chunks"

GRAPHQL_URL = f"https://{SHOP}/admin/api/{API_VERSION}/graphql.json"
SHOPIFY_HEADERS = {
    "X-Shopify-Access-Token": SHOPIFY_TOKEN,
    "Content-Type": "application/json",
}


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
                sku = (variant.get("sku") or "").strip()
                if not sku:
                    continue
                products.append({
                    "product_id":        product["id"],
                    "product_title":     product["title"],
                    "variant_id":        variant["id"],
                    "inventory_item_id": variant["inventoryItem"]["id"],
                    "sku":               sku,
                })

        if not data.get("pageInfo", {}).get("hasNextPage"):
            break
        cursor = edges[-1]["cursor"] if edges else None
        if not cursor:
            break
        page += 1
        time.sleep(0.5)

    return products


def main():
    print("=" * 60)
    print("Splitting Shopify SKUs into chunks for parallel sync")
    print("=" * 60)

    print("\n-> Pulling all SKUs from Shopify...")
    products = get_shopify_products()
    print(f"  Found {len(products)} variants with SKUs")

    Path(CHUNK_DIR).mkdir(exist_ok=True)

    chunk_count = (len(products) + CHUNK_SIZE - 1) // CHUNK_SIZE
    print(f"\n-> Splitting into {chunk_count} chunks of up to {CHUNK_SIZE} SKUs each")

    for i in range(chunk_count):
        chunk = products[i * CHUNK_SIZE : (i + 1) * CHUNK_SIZE]
        chunk_file = CHUNKS_DIR / f"chunk_{i:02d}.json"
        with open(chunk_file, "w", encoding="utf-8") as f:
            json.dump(chunk, f, default=str)
        print(f"  {chunk_file} -> {len(chunk)} SKUs")

    manifest = {
        "chunk_count": chunk_count,
        "total_skus": len(products),
        "chunk_size": CHUNK_SIZE,
    }
    with open(Path(CHUNK_DIR) / "manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f)

    print(f"\nDone. {chunk_count} chunks written to {CHUNK_DIR}/")
    # Print chunk ids as a JSON array for the workflow matrix to consume
    chunk_ids = list(range(chunk_count))
    print(f"CHUNK_IDS_JSON={json.dumps(chunk_ids)}")


if __name__ == "__main__":
    main()
