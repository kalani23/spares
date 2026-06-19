"""
generate_dashboard.py
=======================
Reads sync_run_log.json (produced by stock_sync_final.py) and
generates a static dashboard.html showing:
  - Last sync time
  - In stock / out of stock / unknown counts
  - Full searchable table of every SKU with status and product title

Designed to be committed + pushed to GitHub Pages so Kalani/Paul can
just open a URL and see the latest sync results without touching logs.

Usage:
    python generate_dashboard.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = "sync_run_log.json"
OUTPUT_FILE = "index.html"


def main():
    if not Path(LOG_FILE).exists():
        print(f"[ERROR] {LOG_FILE} not found — run stock_sync_final.py first")
        return

    with open(LOG_FILE, encoding="utf-8") as f:
        results = json.load(f)

    in_stock = [r for r in results if r.get("in_stock") is True]
    out_of_stock = [r for r in results if r.get("in_stock") is False]
    unknown = [r for r in results if r.get("in_stock") is None]

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def row(r):
        sku = r.get("sku", "")
        title = (r.get("product_title", "") or "").replace("<", "&lt;")
        status = r.get("in_stock")
        if status is True:
            badge = '<span class="badge badge-in">IN STOCK</span>'
        elif status is False:
            badge = '<span class="badge badge-out">OUT OF STOCK</span>'
        else:
            badge = '<span class="badge badge-unknown">UNKNOWN</span>'
        return f'<tr><td class="sku">{sku}</td><td>{title}</td><td>{badge}</td></tr>'

    rows_html = "\n".join(row(r) for r in results)

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stuttgart Spares — Stock Sync</title>
<style>
  :root {
    --bg: #14110f;
    --panel: #1d1916;
    --panel-light: #262019;
    --line: #3a322a;
    --text: #ece4d8;
    --text-dim: #9a8f7f;
    --accent: #c8551f;
    --accent-dim: #6b3318;
    --in-stock: #4f7942;
    --out-stock: #a13a2f;
    --unknown: #8a7a55;
    --mono: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    --sans: 'Inter', -apple-system, sans-serif;
  }

  * { box-sizing: border-box; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    margin: 0;
    padding: 0;
    background-image:
      linear-gradient(var(--line) 1px, transparent 1px),
      linear-gradient(90deg, var(--line) 1px, transparent 1px);
    background-size: 32px 32px;
    background-attachment: fixed;
    background-position: -1px -1px;
  }

  .wrap {
    max-width: 1100px;
    margin: 0 auto;
    padding: 48px 24px 80px;
  }

  header {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-bottom: 2px solid var(--accent);
    padding-bottom: 20px;
    margin-bottom: 8px;
    flex-wrap: wrap;
    gap: 12px;
  }

  .title-block .eyebrow {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.18em;
    color: var(--accent);
    text-transform: uppercase;
    margin-bottom: 6px;
  }

  h1 {
    font-size: 28px;
    font-weight: 700;
    margin: 0;
    letter-spacing: -0.01em;
  }

  .updated {
    font-family: var(--mono);
    font-size: 12px;
    color: var(--text-dim);
    text-align: right;
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 1px;
    background: var(--line);
    margin: 28px 0 36px;
    border: 1px solid var(--line);
  }

  .stat {
    background: var(--panel);
    padding: 20px 18px;
  }

  .stat .num {
    font-family: var(--mono);
    font-size: 32px;
    font-weight: 700;
    line-height: 1;
  }

  .stat .label {
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: var(--text-dim);
    margin-top: 6px;
  }

  .stat.total .num { color: var(--text); }
  .stat.in .num { color: var(--in-stock); }
  .stat.out .num { color: var(--out-stock); }
  .stat.unk .num { color: var(--unknown); }

  .controls {
    display: flex;
    gap: 10px;
    margin-bottom: 16px;
    flex-wrap: wrap;
  }

  #search {
    flex: 1;
    min-width: 200px;
    background: var(--panel);
    border: 1px solid var(--line);
    color: var(--text);
    padding: 11px 14px;
    font-family: var(--mono);
    font-size: 13px;
    border-radius: 2px;
  }
  #search:focus { outline: 2px solid var(--accent); border-color: var(--accent); }
  #search::placeholder { color: var(--text-dim); }

  .filter-btn {
    background: var(--panel);
    border: 1px solid var(--line);
    color: var(--text-dim);
    padding: 11px 16px;
    font-family: var(--mono);
    font-size: 12px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    cursor: pointer;
    border-radius: 2px;
  }
  .filter-btn:hover { border-color: var(--accent); color: var(--text); }
  .filter-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--text); }
  .filter-btn:focus-visible { outline: 2px solid var(--accent); }

  table {
    width: 100%;
    border-collapse: collapse;
    background: var(--panel);
    border: 1px solid var(--line);
  }

  thead th {
    text-align: left;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--text-dim);
    padding: 12px 16px;
    border-bottom: 1px solid var(--line);
    background: var(--panel-light);
    position: sticky;
    top: 0;
  }

  tbody td {
    padding: 10px 16px;
    border-bottom: 1px solid var(--line);
    font-size: 13px;
    vertical-align: middle;
  }

  tbody tr:hover { background: var(--panel-light); }

  td.sku {
    font-family: var(--mono);
    color: var(--text-dim);
    white-space: nowrap;
  }

  .badge {
    font-family: var(--mono);
    font-size: 11px;
    letter-spacing: 0.05em;
    padding: 3px 9px;
    border-radius: 2px;
    display: inline-block;
    white-space: nowrap;
  }
  .badge-in     { background: rgba(79,121,66,0.18);  color: #8bbf78; border: 1px solid rgba(79,121,66,0.4); }
  .badge-out    { background: rgba(161,58,47,0.18);   color: #e08a7d; border: 1px solid rgba(161,58,47,0.4); }
  .badge-unknown{ background: rgba(138,122,85,0.18);  color: #cdbb8e; border: 1px solid rgba(138,122,85,0.4); }

  .table-wrap {
    max-height: 70vh;
    overflow-y: auto;
    border: 1px solid var(--line);
  }
  .table-wrap table { border: none; }

  footer {
    margin-top: 32px;
    font-family: var(--mono);
    font-size: 11px;
    color: var(--text-dim);
    text-align: center;
  }

  .hidden { display: none; }

  @media (max-width: 700px) {
    .stats { grid-template-columns: repeat(2, 1fr); }
    header { flex-direction: column; align-items: flex-start; }
    .updated { text-align: left; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="title-block">
      <div class="eyebrow">Stuttgart Spares · partworks.de sync</div>
      <h1>Stock Sync Dashboard</h1>
    </div>
    <div class="updated">Last run<br>__GENERATED_AT__</div>
  </header>

  <div class="stats">
    <div class="stat total"><div class="num">__TOTAL__</div><div class="label">Total SKUs</div></div>
    <div class="stat in"><div class="num">__IN_STOCK__</div><div class="label">In Stock</div></div>
    <div class="stat out"><div class="num">__OUT_STOCK__</div><div class="label">Out of Stock</div></div>
    <div class="stat unk"><div class="num">__UNKNOWN__</div><div class="label">Unknown</div></div>
  </div>

  <div class="controls">
    <input id="search" type="text" placeholder="Search by SKU or product name…" autocomplete="off">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="in">In stock</button>
    <button class="filter-btn" data-filter="out">Out of stock</button>
    <button class="filter-btn" data-filter="unknown">Unknown</button>
  </div>

  <div class="table-wrap">
    <table id="sku-table">
      <thead>
        <tr><th>SKU</th><th>Product</th><th>Status</th></tr>
      </thead>
      <tbody>
        __ROWS__
      </tbody>
    </table>
  </div>

  <footer>Generated automatically by stock_sync_final.py · Stuttgart Spares</footer>
</div>

<script>
  const search = document.getElementById('search');
  const filterBtns = document.querySelectorAll('.filter-btn');
  const rows = Array.from(document.querySelectorAll('#sku-table tbody tr'));
  let activeFilter = 'all';

  function statusOf(row) {
    if (row.querySelector('.badge-in')) return 'in';
    if (row.querySelector('.badge-out')) return 'out';
    return 'unknown';
  }

  function applyFilters() {
    const q = search.value.trim().toLowerCase();
    rows.forEach(row => {
      const text = row.textContent.toLowerCase();
      const matchesSearch = !q || text.includes(q);
      const matchesFilter = activeFilter === 'all' || statusOf(row) === activeFilter;
      row.classList.toggle('hidden', !(matchesSearch && matchesFilter));
    });
  }

  search.addEventListener('input', applyFilters);
  filterBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      filterBtns.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      activeFilter = btn.dataset.filter;
      applyFilters();
    });
  });
</script>
</body>
</html>
"""

    html = (
        html.replace("__GENERATED_AT__", generated_at)
            .replace("__TOTAL__", str(len(results)))
            .replace("__IN_STOCK__", str(len(in_stock)))
            .replace("__OUT_STOCK__", str(len(out_of_stock)))
            .replace("__UNKNOWN__", str(len(unknown)))
            .replace("__ROWS__", rows_html)
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard written -> {OUTPUT_FILE}")
    print(f"  Total: {len(results)} | In stock: {len(in_stock)} | "
          f"Out of stock: {len(out_of_stock)} | Unknown: {len(unknown)}")


if __name__ == "__main__":
    main()
