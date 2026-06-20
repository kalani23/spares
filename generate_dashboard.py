"""
generate_dashboard.py
=======================
Reads sync_run_log.json (current SKU snapshot) and history.json
(daily run records) and generates a static index.html dashboard.

Usage:
    python generate_dashboard.py
"""

import json
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE     = "sync_run_log.json"
HISTORY_FILE = "history.json"
OUTPUT_FILE  = "index.html"


def main():
    if not Path(LOG_FILE).exists():
        print(f"[ERROR] {LOG_FILE} not found - run merge_and_update.py first")
        return

    with open(LOG_FILE, encoding="utf-8") as f:
        results = json.load(f)

    history = []
    if Path(HISTORY_FILE).exists():
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)

    in_stock     = [r for r in results if r.get("in_stock") is True]
    out_of_stock = [r for r in results if r.get("in_stock") is False]
    not_found    = [r for r in results if r.get("in_stock") is None]

    last_run = history[-1] if history else None
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
            badge = '<span class="badge badge-unknown">NOT FOUND</span>'
        return '<tr><td class="sku">' + sku + '</td><td>' + title + '</td><td>' + badge + '</td></tr>'

    rows_html = "\n".join(row(r) for r in results)

    def history_row(h, idx):
        ts = h.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            ts_display = dt.strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            ts_display = ts
        return ('<tr class="history-row" data-idx="' + str(idx) + '">'
                '<td class="mono">' + ts_display + '</td>'
                '<td>' + str(h.get('total_skus', 0)) + '</td>'
                '<td class="num-in">' + str(h.get('in_stock', 0)) + '</td>'
                '<td class="num-out">' + str(h.get('out_of_stock', 0)) + '</td>'
                '<td class="num-unk">' + str(h.get('not_found', 0)) + '</td>'
                '<td class="num-change">+' + str(h.get('newly_in_stock', 0)) + '</td>'
                '<td class="num-change-out">-' + str(h.get('newly_out_of_stock', 0)) + '</td>'
                '<td>' + str(h.get('updated', 0)) + '</td>'
                '<td>' + str(h.get('failed', 0)) + '</td>'
                '</tr>')

    history_rows_html = "\n".join(
        history_row(h, i) for i, h in enumerate(reversed(history))
    )

    def change_detail(h, idx):
        new_in = h.get("newly_in_stock_skus", [])
        new_out = h.get("newly_out_of_stock_skus", [])
        in_items = "".join(
            '<li><span class="mono">' + i["sku"] + '</span> - ' + i["title"][:60] + '</li>'
            for i in new_in
        ) or "<li class='empty'>None</li>"
        out_items = "".join(
            '<li><span class="mono">' + i["sku"] + '</span> - ' + i["title"][:60] + '</li>'
            for i in new_out
        ) or "<li class='empty'>None</li>"
        return ('<div class="change-detail" id="detail-' + str(idx) + '" style="display:none">'
                '<div class="change-col"><div class="change-col-title in">Newly in stock</div>'
                '<ul>' + in_items + '</ul></div>'
                '<div class="change-col"><div class="change-col-title out">Newly out of stock</div>'
                '<ul>' + out_items + '</ul></div>'
                '</div>')

    change_details_html = "\n".join(
        change_detail(h, i) for i, h in enumerate(reversed(history))
    )

    last_run_summary = ""
    if last_run:
        last_run_summary = (
            '<div class="last-run-banner">'
            '<div class="lrb-item"><span class="lrb-num">+' + str(last_run.get('newly_in_stock', 0)) + '</span>'
            '<span class="lrb-label">newly in stock</span></div>'
            '<div class="lrb-item"><span class="lrb-num out">-' + str(last_run.get('newly_out_of_stock', 0)) + '</span>'
            '<span class="lrb-label">newly out of stock</span></div>'
            '<div class="lrb-item"><span class="lrb-num">' + str(last_run.get('updated', 0)) + '</span>'
            '<span class="lrb-label">Shopify updates pushed</span></div>'
            '<div class="lrb-item"><span class="lrb-num fail">' + str(last_run.get('failed', 0)) + '</span>'
            '<span class="lrb-label">update failures</span></div>'
            '</div>'
        )

    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Stuttgart Spares - Stock Sync</title>
<style>
  :root {
    --bg: #14110f; --panel: #1d1916; --panel-light: #262019; --line: #3a322a;
    --text: #ece4d8; --text-dim: #9a8f7f; --accent: #c8551f; --accent-dim: #6b3318;
    --in-stock: #4f7942; --out-stock: #a13a2f; --unknown: #8a7a55;
    --mono: 'JetBrains Mono', 'SF Mono', 'Consolas', monospace;
    --sans: 'Inter', -apple-system, sans-serif;
  }
  * { box-sizing: border-box; }
  body {
    background: var(--bg); color: var(--text); font-family: var(--sans);
    margin: 0; padding: 0;
    background-image: linear-gradient(var(--line) 1px, transparent 1px),
      linear-gradient(90deg, var(--line) 1px, transparent 1px);
    background-size: 32px 32px; background-attachment: fixed; background-position: -1px -1px;
  }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 48px 24px 80px; }
  header { display: flex; justify-content: space-between; align-items: flex-end;
    border-bottom: 2px solid var(--accent); padding-bottom: 20px; margin-bottom: 8px;
    flex-wrap: wrap; gap: 12px; }
  .title-block .eyebrow { font-family: var(--mono); font-size: 11px; letter-spacing: 0.18em;
    color: var(--accent); text-transform: uppercase; margin-bottom: 6px; }
  h1 { font-size: 28px; font-weight: 700; margin: 0; letter-spacing: -0.01em; }
  h2 { font-size: 16px; font-weight: 700; margin: 0 0 14px; text-transform: uppercase;
    letter-spacing: 0.06em; color: var(--text-dim); }
  .updated { font-family: var(--mono); font-size: 12px; color: var(--text-dim); text-align: right; }

  .last-run-banner { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px;
    background: var(--line); border: 1px solid var(--line); margin: 24px 0; }
  .lrb-item { background: var(--panel); padding: 16px 18px; text-align: center; }
  .lrb-num { display: block; font-family: var(--mono); font-size: 26px; font-weight: 700; color: var(--in-stock); }
  .lrb-num.out { color: var(--out-stock); }
  .lrb-num.fail { color: var(--out-stock); }
  .lrb-label { font-size: 10px; text-transform: uppercase; letter-spacing: 0.08em; color: var(--text-dim); }

  .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 1px; background: var(--line);
    margin: 28px 0 36px; border: 1px solid var(--line); }
  .stat { background: var(--panel); padding: 20px 18px; }
  .stat .num { font-family: var(--mono); font-size: 32px; font-weight: 700; line-height: 1; }
  .stat .label { font-size: 11px; text-transform: uppercase; letter-spacing: 0.1em; color: var(--text-dim); margin-top: 6px; }
  .stat.total .num { color: var(--text); }
  .stat.in .num { color: var(--in-stock); }
  .stat.out .num { color: var(--out-stock); }
  .stat.unk .num { color: var(--unknown); }

  section { margin-bottom: 44px; }

  .controls { display: flex; gap: 10px; margin-bottom: 16px; flex-wrap: wrap; }
  #search { flex: 1; min-width: 200px; background: var(--panel); border: 1px solid var(--line);
    color: var(--text); padding: 11px 14px; font-family: var(--mono); font-size: 13px; border-radius: 2px; }
  #search:focus { outline: 2px solid var(--accent); border-color: var(--accent); }
  #search::placeholder { color: var(--text-dim); }
  .filter-btn { background: var(--panel); border: 1px solid var(--line); color: var(--text-dim);
    padding: 11px 16px; font-family: var(--mono); font-size: 12px; letter-spacing: 0.04em;
    text-transform: uppercase; cursor: pointer; border-radius: 2px; }
  .filter-btn:hover { border-color: var(--accent); color: var(--text); }
  .filter-btn.active { background: var(--accent-dim); border-color: var(--accent); color: var(--text); }

  table { width: 100%; border-collapse: collapse; background: var(--panel); border: 1px solid var(--line); }
  thead th { text-align: left; font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
    color: var(--text-dim); padding: 12px 16px; border-bottom: 1px solid var(--line);
    background: var(--panel-light); position: sticky; top: 0; }
  tbody td { padding: 10px 16px; border-bottom: 1px solid var(--line); font-size: 13px; vertical-align: middle; }
  tbody tr:hover { background: var(--panel-light); }
  td.sku, td.mono, .mono { font-family: var(--mono); color: var(--text-dim); white-space: nowrap; }

  .badge { font-family: var(--mono); font-size: 11px; letter-spacing: 0.05em; padding: 3px 9px;
    border-radius: 2px; display: inline-block; white-space: nowrap; }
  .badge-in { background: rgba(79,121,66,0.18); color: #8bbf78; border: 1px solid rgba(79,121,66,0.4); }
  .badge-out { background: rgba(161,58,47,0.18); color: #e08a7d; border: 1px solid rgba(161,58,47,0.4); }
  .badge-unknown { background: rgba(138,122,85,0.18); color: #cdbb8e; border: 1px solid rgba(138,122,85,0.4); }

  .table-wrap { max-height: 70vh; overflow-y: auto; border: 1px solid var(--line); }
  .table-wrap table { border: none; }

  .num-in { color: #8bbf78; font-family: var(--mono); }
  .num-out { color: #e08a7d; font-family: var(--mono); }
  .num-unk { color: #cdbb8e; font-family: var(--mono); }
  .num-change { color: #8bbf78; font-family: var(--mono); font-weight: 700; }
  .num-change-out { color: #e08a7d; font-family: var(--mono); font-weight: 700; }

  .history-row { cursor: pointer; }
  .history-row:hover { background: var(--panel-light); }

  .change-detail { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line);
    border: 1px solid var(--line); margin-top: -1px; margin-bottom: 8px; }
  .change-col { background: var(--panel-light); padding: 14px 18px; }
  .change-col-title { font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
    margin-bottom: 8px; font-weight: 700; }
  .change-col-title.in { color: #8bbf78; }
  .change-col-title.out { color: #e08a7d; }
  .change-col ul { margin: 0; padding-left: 18px; font-size: 12px; color: var(--text-dim); }
  .change-col li { margin-bottom: 4px; }
  .change-col li.empty { font-style: italic; opacity: 0.6; list-style: none; padding-left: 0; }

  footer { margin-top: 32px; font-family: var(--mono); font-size: 11px; color: var(--text-dim); text-align: center; }
  .hidden { display: none; }

  @media (max-width: 700px) {
    .stats, .last-run-banner { grid-template-columns: repeat(2, 1fr); }
    header { flex-direction: column; align-items: flex-start; }
    .updated { text-align: left; }
    .change-detail { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="wrap">

  <header>
    <div class="title-block">
      <div class="eyebrow">Stuttgart Spares - partworks.de sync</div>
      <h1>Stock Sync Dashboard</h1>
    </div>
    <div class="updated">Dashboard generated<br>__GENERATED_AT__</div>
  </header>

  __LAST_RUN_SUMMARY__

  <div class="stats">
    <div class="stat total"><div class="num">__TOTAL__</div><div class="label">Total SKUs</div></div>
    <div class="stat in"><div class="num">__IN_STOCK__</div><div class="label">In Stock</div></div>
    <div class="stat out"><div class="num">__OUT_STOCK__</div><div class="label">Out of Stock</div></div>
    <div class="stat unk"><div class="num">__UNKNOWN__</div><div class="label">Not Found on Supplier</div></div>
  </div>

  <section>
    <h2>Daily Run History (click a row to see what changed)</h2>
    <div class="table-wrap" style="max-height: 50vh;">
      <table>
        <thead>
          <tr>
            <th>Run Time</th><th>Total</th><th>In Stock</th><th>Out</th><th>Not Found</th>
            <th>New In-Stock</th><th>New Out</th><th>Updated</th><th>Failed</th>
          </tr>
        </thead>
        <tbody id="history-body">
          __HISTORY_ROWS__
        </tbody>
      </table>
    </div>
    __CHANGE_DETAILS__
  </section>

  <section>
    <h2>Current SKU Status</h2>
    <div class="controls">
      <input id="search" type="text" placeholder="Search by SKU or product name..." autocomplete="off">
      <button class="filter-btn active" data-filter="all">All</button>
      <button class="filter-btn" data-filter="in">In stock</button>
      <button class="filter-btn" data-filter="out">Out of stock</button>
      <button class="filter-btn" data-filter="unknown">Not found</button>
    </div>
    <div class="table-wrap">
      <table id="sku-table">
        <thead><tr><th>SKU</th><th>Product</th><th>Status</th></tr></thead>
        <tbody>
          __ROWS__
        </tbody>
      </table>
    </div>
  </section>

  <footer>Generated automatically by merge_and_update.py + generate_dashboard.py - Stuttgart Spares</footer>
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

  document.querySelectorAll('.history-row').forEach(row => {
    row.addEventListener('click', () => {
      const idx = row.getAttribute('data-idx');
      const detail = document.getElementById('detail-' + idx);
      if (!detail) return;
      const isOpen = detail.style.display !== 'none';
      document.querySelectorAll('.change-detail').forEach(d => d.style.display = 'none');
      detail.style.display = isOpen ? 'none' : 'grid';
      if (!isOpen) detail.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    });
  });
</script>
</body>
</html>
"""

    history_rows_final = history_rows_html or "<tr><td colspan='9' style='text-align:center;color:var(--text-dim);padding:20px;'>No run history yet</td></tr>"

    html = (
        html.replace("__GENERATED_AT__", generated_at)
            .replace("__TOTAL__", str(len(results)))
            .replace("__IN_STOCK__", str(len(in_stock)))
            .replace("__OUT_STOCK__", str(len(out_of_stock)))
            .replace("__UNKNOWN__", str(len(not_found)))
            .replace("__ROWS__", rows_html)
            .replace("__HISTORY_ROWS__", history_rows_final)
            .replace("__CHANGE_DETAILS__", change_details_html)
            .replace("__LAST_RUN_SUMMARY__", last_run_summary)
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"Dashboard written -> {OUTPUT_FILE}")
    print(f"  Total: {len(results)} | In stock: {len(in_stock)} | "
          f"Out of stock: {len(out_of_stock)} | Not found: {len(not_found)}")
    print(f"  Run history: {len(history)} runs")


if __name__ == "__main__":
    main()
