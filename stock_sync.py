"""
stock_sync.py
=============
Stuttgart Spares — partworks.de stock sync

Flow:
  1. Load checkpoint (if resuming) or start fresh
  2. Discover all subcategories + listing pages (or restore from checkpoint)
  3. Scrape pages SEQUENTIALLY — accurate block detection
  4. On block: save checkpoint → sys.exit(2) → workflow triggers new run
  5. On complete: update Shopify inventory → save log

Exit codes:
  0 = success
  1 = error
  2 = blocked mid-scrape → workflow self-triggers new run
"""

import os
import json
import re
import sys
import time
import random
import logging
from datetime import datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests
from bs4 import BeautifulSoup

# ── Config ────────────────────────────────────────────────────────────────────

SHOPIFY_TOKEN   = os.environ.get("SHOPIFY_TOKEN", "")
SHOP            = "27dkze-zv.myshopify.com"
API_VERSION     = "2024-01"
STOCK_MAP_FILE  = "stock_map.json"
SYNC_LOG_FILE   = "sync_log.json"
CHECKPOINT_FILE = "checkpoint.json"

BASE_URL        = "https://partworks.de"
SHOPIFY_BASE    = f"https://{SHOP}/admin/api/{API_VERSION}"
SHOPIFY_HEADERS = {"X-Shopify-Access-Token": SHOPIFY_TOKEN, "Content-Type": "application/json"}

# Scraping config
DISCOVERY_WORKERS = 6     # workers for discovery phase (parallel is fine)
REQ_DELAY         = (1.2, 2.5)   # seconds between requests — polite
REQ_TIMEOUT       = 12
MAX_RETRIES       = 3
BLOCK_THRESHOLD   = 40    # consecutive empty pages → assume blocked

# ── Category list (German URLs after Nov 2025 site update) ────────────────────

ALL_CATEGORIES = [
    "/Porsche/356-Ersatzteile",
    "/Porsche/911-F-Modell-Ersatzteile",
    "/Porsche/912-Ersatzteile",
    "/Porsche/911-G-Modell-Ersatzteile",
    "/Porsche/964-Ersatzteile",
    "/Porsche/993-Ersatzteile",
    "/Porsche/996-Ersatzteile",
    "/Porsche/997-Ersatzteile",
    "/Porsche/991-Ersatzteile",
    "/Porsche/914-Ersatzteile",
    "/Porsche/944-Ersatzteile",
    "/Porsche/924-Ersatzteile",
    "/Porsche/968-Ersatzteile",
    "/Porsche/928-Ersatzteile",
    "/Porsche/Boxster-986-Ersatzteile",
    "/Porsche/Boxster-Cayman-987-Ersatzteile",
    "/Porsche/Boxster-Cayman-981-Ersatzteile",
    "/Porsche/Cayenne-955-Ersatzteile",
    "/Porsche/Cayenne-957-Ersatzteile",
    "/Porsche/Cayenne-958-Ersatzteile",
    "/Porsche/Panamera-970-Ersatzteile",
    "/Porsche/Panamera-970FL-Ersatzteile",
    "/Porsche/Macan-95B-Ersatzteile",
    "/Porsche/Ersatzteile-Neuere-Modelle",
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("stock_sync")

print_lock = Lock()
def tprint(*args):
    with print_lock:
        print(*args, flush=True)

# ── HTTP session (one per thread) ─────────────────────────────────────────────

import threading
_local = threading.local()

def get_session() -> requests.Session:
    if not hasattr(_local, "s"):
        s = requests.Session()
        s.headers.update({
            "User-Agent":      random.choice(USER_AGENTS),
            "Accept-Language": "en-GB,en;q=0.9,de;q=0.8",
            "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer":         "https://partworks.de/",
            "DNT":             "1",
        })
        _local.s = s
    return _local.s

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch(url: str) -> BeautifulSoup | None:
    for attempt in range(MAX_RETRIES):
        try:
            time.sleep(random.uniform(*REQ_DELAY))
            r = get_session().get(url, timeout=REQ_TIMEOUT)

            if r.status_code == 200:
                # Soft block: small page with block phrases
                if len(r.text) < 5000:
                    body = r.text.lower()
                    if any(x in body for x in ["captcha", "access denied", "too many requests", "rate limit"]):
                        tprint(f"  [SOFT BLOCK] {url[:80]}")
                        return None
                return BeautifulSoup(r.text, "html.parser")

            elif r.status_code == 429:
                wait = 30 * (attempt + 1)
                tprint(f"  [429] Rate limited — sleeping {wait}s")
                time.sleep(wait)

            elif r.status_code in (403, 503):
                tprint(f"  [HTTP {r.status_code}] Hard block — {url[:80]}")
                return None

            else:
                log.warning(f"HTTP {r.status_code}: {url}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)

        except requests.exceptions.ConnectTimeout:
            tprint(f"  [TIMEOUT] attempt {attempt+1}/{MAX_RETRIES} — {url[:80]}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(3 ** attempt)

        except Exception as e:
            log.warning(f"Error: {url} — {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(2 ** attempt)

    return None

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 1 — DISCOVERY
# ═════════════════════════════════════════════════════════════════════════════

def get_subcats(category: str) -> list[str]:
    soup = fetch(BASE_URL + category)
    if not soup:
        return [BASE_URL + category]

    # Exact selector confirmed from DOM inspection
    urls, seen = [], set()
    for a in soup.select("a.et-sub-category-link-wrapper"):
        href = a.get("href", "")
        if href and href not in seen:
            seen.add(href)
            full = href if href.startswith("http") else BASE_URL + href
            urls.append(full)

    if urls:
        return urls

    # No subcategories — this category goes straight to products
    return [BASE_URL + category]

def discover_all_pages() -> list[str]:
    """Discover all subcategories and their listing pages."""

    tprint("\n── Phase 1: Discovering subcategories ──────────────────────")
    all_subcats, seen, lock = [], set(), Lock()

    def fetch_cat(cat):
        urls = get_subcats(cat)
        with lock:
            new = [u for u in urls if u not in seen]
            seen.update(new)
            all_subcats.extend(new)
            tprint(f"  {cat:<55} → {len(new)} subcats")

    with ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as ex:
        list(as_completed([ex.submit(fetch_cat, c) for c in ALL_CATEGORIES]))

    tprint(f"\n  Total subcategories: {len(all_subcats)}")

    tprint("\n── Phase 2: Collecting listing pages ───────────────────────")
    all_pages, seen2, lock2, done = [], set(), Lock(), [0]

    def collect(url):
        soup = fetch(url)
        pages = [url]
        if soup:
            for a in soup.select("ul.pagination a.page-link"):
                href = a.get("href", "").split("#")[0]
                if href:
                    full = href if href.startswith("http") else BASE_URL + href
                    if full not in pages:
                        pages.append(full)
        with lock2:
            for p in pages:
                if p not in seen2:
                    seen2.add(p)
                    all_pages.append(p)
            done[0] += 1
            if done[0] % 100 == 0:
                tprint(f"  {done[0]}/{len(all_subcats)} subcats | {len(all_pages)} pages")

    with ThreadPoolExecutor(max_workers=DISCOVERY_WORKERS) as ex:
        list(as_completed([ex.submit(collect, u) for u in all_subcats]))

    tprint(f"  Total listing pages: {len(all_pages)}")
    return all_pages

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 2 — SCRAPE (sequential for accurate checkpoint)
# ═════════════════════════════════════════════════════════════════════════════

def scrape_page(url: str) -> dict[str, bool]:
    soup = fetch(url)
    if not soup:
        return {}

    results = {}
    for card in soup.select(".productbox.et-item-list"):
        item_num = None
        sku_el   = card.select_one('[itemprop="sku"]')
        if sku_el:
            item_num = sku_el.get_text(strip=True)
        if not item_num:
            for dd in card.select(".item-detail-dd"):
                text = dd.get_text(" ", strip=True)
                if "Item number:" in text:
                    item_num = re.sub(r".*Item number:\s*", "", text).strip()
                    break
        if not item_num:
            continue
        item_num = item_num.lstrip("'").strip()
        if not re.match(r"^\d{4,8}$", item_num):
            continue

        in_stock    = False
        status_span = card.select_one(".delivery-status .status")
        if status_span:
            in_stock = "status-2" in status_span.get("class", [])
        else:
            avail = card.select_one('link[itemprop="availability"]')
            if avail:
                in_stock = "InStock" in avail.get("href", "")

        results[item_num] = in_stock

    return results

def save_checkpoint(all_pages: list, resume_index: int, stock_map: dict):
    """Save progress so next run resumes from resume_index."""
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump({
            "resume_index": resume_index,
            "all_pages":    all_pages,
            "stock_map":    stock_map,
            "saved_at":     datetime.now().isoformat(),
        }, f)
    tprint(f"  💾 Checkpoint saved — resume from page {resume_index}/{len(all_pages)}")

def load_checkpoint() -> dict | None:
    if Path(CHECKPOINT_FILE).exists():
        with open(CHECKPOINT_FILE, encoding="utf-8") as f:
            data = json.load(f)
        tprint(f"  📂 Checkpoint found — resuming from page {data['resume_index']}/{len(data['all_pages'])} "
               f"({len(data['stock_map'])} SKUs already collected)")
        return data
    return None

def scrape_stock(all_pages: list, start_index: int, stock_map: dict) -> tuple[dict, bool]:
    """
    Scrape pages sequentially starting from start_index.
    Returns (stock_map, was_blocked).
    On block: saves checkpoint and returns was_blocked=True.
    """
    total       = len(all_pages)
    empty_streak = 0

    tprint(f"\n── Phase 3: Scraping stock ──────────────────────────────────")
    tprint(f"  Pages: {start_index} → {total} ({total - start_index} remaining)")
    tprint(f"  Delay: {REQ_DELAY[0]}-{REQ_DELAY[1]}s | Block threshold: {BLOCK_THRESHOLD} empty pages")

    for i in range(start_index, total):
        url    = all_pages[i]
        result = scrape_page(url)
        stock_map.update(result)

        if len(result) == 0:
            empty_streak += 1
        else:
            empty_streak = 0

        if i % 50 == 0:
            tprint(f"  {i}/{total} pages | {len(stock_map)} SKUs | empty_streak={empty_streak}")

        # ── Block detected ────────────────────────────────────────
        if empty_streak >= BLOCK_THRESHOLD:
            tprint(f"\n  ⛔ BLOCKED: {empty_streak} consecutive empty pages at index {i}")
            tprint(f"  SKUs collected so far: {len(stock_map)}")

            # Rewind by BLOCK_THRESHOLD so blocked pages get re-scraped on fresh IP
            resume_from = max(0, i - BLOCK_THRESHOLD + 1)
            save_checkpoint(all_pages, resume_from, stock_map)

            # Save partial stock map so Shopify update still runs with what we have
            with open(STOCK_MAP_FILE, "w", encoding="utf-8") as f:
                json.dump(stock_map, f)

            return stock_map, True   # was_blocked = True

    # Clean finish
    tprint(f"\n  ✓ Scrape complete: {len(stock_map)} SKUs from {total} pages")
    if Path(CHECKPOINT_FILE).exists():
        Path(CHECKPOINT_FILE).unlink()

    return stock_map, False   # was_blocked = False

# ═════════════════════════════════════════════════════════════════════════════
# PHASE 3 — SHOPIFY UPDATE
# ═════════════════════════════════════════════════════════════════════════════

def get_shopify_products() -> list[dict]:
    tprint("\n── Fetching Shopify products ────────────────────────────────")
    products = []
    url = f"{SHOPIFY_BASE}/products.json?limit=250&fields=id,variants&status=active"
    while url:
        r    = requests.get(url, headers=SHOPIFY_HEADERS, timeout=20)
        data = r.json()
        for product in data.get("products", []):
            for variant in product.get("variants", []):
                sku = (variant.get("sku") or "").strip().lstrip("'")
                if not re.match(r"^\d{4,8}$", sku):
                    continue
                products.append({
                    "product_id":        product["id"],
                    "variant_id":        variant["id"],
                    "sku":               sku,
                    "inventory_item_id": variant.get("inventory_item_id"),
                    "current_qty":       variant.get("inventory_quantity", 0),
                })
        link = r.headers.get("Link", "")
        url  = None
        for part in link.split(","):
            if 'rel="next"' in part:
                url = part.strip().split(";")[0].strip("<>")
    tprint(f"  Found {len(products)} products")
    return products

def get_location_id(products: list) -> int | None:
    for p in products[:10]:
        iid = p.get("inventory_item_id")
        if not iid:
            continue
        r      = requests.get(f"{SHOPIFY_BASE}/inventory_levels.json?inventory_item_ids={iid}",
                               headers=SHOPIFY_HEADERS, timeout=20)
        levels = r.json().get("inventory_levels", [])
        if levels:
            lid = levels[0]["location_id"]
            tprint(f"  Location ID: {lid}")
            return lid
    return None

def ensure_tracking(variant_id: int):
    requests.put(
        f"{SHOPIFY_BASE}/variants/{variant_id}.json",
        headers=SHOPIFY_HEADERS,
        json={"variant": {"id": variant_id, "inventory_management": "shopify"}},
        timeout=20
    )

def set_inventory(inventory_item_id: int, location_id: int, qty: int) -> bool:
    r = requests.post(
        f"{SHOPIFY_BASE}/inventory_levels/set.json",
        headers=SHOPIFY_HEADERS,
        json={"location_id": location_id, "inventory_item_id": inventory_item_id, "available": qty},
        timeout=20
    )
    return r.status_code == 200

def update_shopify(stock_map: dict, products: list, location_id: int) -> dict:
    tprint("\n── Updating Shopify inventory ───────────────────────────────")
    changes, no_change, not_found, updated, errors = [], 0, 0, 0, 0

    for product in products:
        sku               = product["sku"]
        current_qty       = product["current_qty"]
        variant_id        = product["variant_id"]
        inventory_item_id = product["inventory_item_id"]

        if sku not in stock_map:
            not_found += 1
            continue

        target_qty = 2 if stock_map[sku] else 0
        if current_qty == target_qty:
            no_change += 1
            continue

        direction = "↑ IN" if stock_map[sku] else "↓ OUT"
        tprint(f"  SKU {sku:<8} {current_qty} → {target_qty}  [{direction}]")

        changes.append({
            "sku":      sku,
            "from_qty": current_qty,
            "to_qty":   target_qty,
            "in_stock": stock_map[sku],
        })

        ensure_tracking(variant_id)
        time.sleep(0.2)
        if set_inventory(inventory_item_id, location_id, target_qty):
            updated += 1
        else:
            errors += 1
        time.sleep(0.4)

    return {
        "changes":    changes,
        "no_change":  no_change,
        "not_found":  not_found,
        "updated":    updated,
        "errors":     errors,
    }

# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def check_site_reachable() -> bool:
    """Quick check if partworks.de is reachable from this IP."""
    try:
        r = requests.get(BASE_URL, timeout=10)
        return r.status_code == 200
    except:
        return False

def main():
    start = time.time()

    tprint("=" * 65)
    tprint("Stuttgart Spares — Stock Sync")
    tprint(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    tprint("=" * 65)

    # ── Quick IP check before doing anything ─────────────────────
    tprint("\n  Checking if partworks.de is reachable from this runner...")
    if not check_site_reachable():
        tprint("  ✗ Site unreachable — this runner likely has a non-EU IP.")
        tprint("  Exiting with code 2 → workflow will trigger new run with fresh IP.")
        sys.exit(2)

    # ── Load checkpoint or discover fresh ────────────────────────────
    checkpoint = load_checkpoint()

    if checkpoint:
        all_pages   = checkpoint["all_pages"]
        start_index = checkpoint["resume_index"]
        stock_map   = checkpoint["stock_map"]
        tprint(f"\n  Resuming run — {len(all_pages)} pages total, starting at {start_index}")
    else:
        tprint("\n  Fresh run — discovering all pages...")
        all_pages   = discover_all_pages()
        start_index = 0
        stock_map   = {}

    # ── Scrape (sequential) ───────────────────────────────────────────
    stock_map, was_blocked = scrape_stock(all_pages, start_index, stock_map)

    # Save stock map regardless
    with open(STOCK_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(stock_map, f, indent=2)
    tprint(f"\n  Stock map saved → {STOCK_MAP_FILE} ({len(stock_map)} SKUs)")

    # ── If blocked → exit(2) so workflow triggers new run ─────────────
    if was_blocked:
        tprint("\n  Exiting with code 2 → workflow will trigger a new run to resume")
        sys.exit(2)

    # ── Full run complete → update Shopify ────────────────────────────
    products    = get_shopify_products()
    location_id = get_location_id(products)

    if not location_id:
        tprint("  [ERROR] Could not detect location ID")
        sys.exit(1)

    result  = update_shopify(stock_map, products, location_id)
    elapsed = time.time() - start

    # ── Save log ──────────────────────────────────────────────────────
    run_record = {
        "run_at":           datetime.now().isoformat(),
        "elapsed_sec":      round(elapsed, 1),
        "partworks_skus":   len(stock_map),
        "shopify_products": len(products),
        **result,
    }
    log_path = Path(SYNC_LOG_FILE)
    all_logs = json.loads(log_path.read_text(encoding="utf-8")) if log_path.exists() else []
    all_logs.append(run_record)
    log_path.write_text(json.dumps(all_logs[-30:], indent=2, ensure_ascii=False), encoding="utf-8")

    # ── Summary ───────────────────────────────────────────────────────
    tprint(f"\n{'=' * 65}")
    tprint("SYNC COMPLETE")
    tprint(f"  Elapsed:          {elapsed:.0f}s ({elapsed/60:.1f} min)")
    tprint(f"  Partworks SKUs:   {len(stock_map)}")
    tprint(f"  Shopify products: {len(products)}")
    tprint(f"  Updated:          {result['updated']}")
    tprint(f"  No change:        {result['no_change']}")
    tprint(f"  Not on partworks: {result['not_found']}")
    if result["errors"]:
        tprint(f"  Errors:           {result['errors']}")
    tprint("=" * 65)
    sys.exit(0)


if __name__ == "__main__":
    main()
