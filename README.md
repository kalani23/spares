# Stuttgart Spares — Stock Sync

Daily stock sync from partworks.de → Shopify inventory.

## How it works

1. **Discovers** all subcategory pages on partworks.de
2. **Scrapes** each listing page sequentially, extracting SKU + stock status
3. **On block** (IP gets blocked mid-scrape):
   - Saves a `checkpoint.json` with progress
   - Exits with code 2
   - Workflow automatically triggers a **new run with a fresh IP**
   - New run resumes from the exact page where it stopped
4. **On complete**: updates Shopify inventory for any changed products

## Setup (one time)

### Step 1 — Create GitHub repo

```bash
# On your machine
git clone https://github.com/YOUR_USERNAME/stuttgart-spares-sync.git
cd stuttgart-spares-sync

# Copy all files from this folder into the repo
# Then push:
git add .
git commit -m "initial setup"
git push
```

### Step 2 — Add Shopify token as secret

1. Go to your repo on GitHub
2. **Settings → Secrets and variables → Actions**
3. Click **New repository secret**
4. Name: `SHOPIFY_TOKEN`
5. Value: (paste your Shopify token here — starts with `shpat_`)
6. Click **Add secret**

### Step 3 — Enable workflow permissions

1. Go to **Settings → Actions → General**
2. Scroll to **Workflow permissions**
3. Select **Read and write permissions**
4. Check **Allow GitHub Actions to create and approve pull requests**
5. Click **Save**

### Step 4 — Test run

1. Go to **Actions** tab in your repo
2. Click **Stock Sync** in the left sidebar
3. Click **Run workflow** → **Run workflow**
4. Watch the logs

## Files

| File | Purpose |
|------|---------|
| `stock_sync.py` | Main scraper + Shopify updater |
| `stock_map.json` | Latest SKU → in_stock map (committed after each run) |
| `sync_log.json` | History of last 30 runs |
| `checkpoint.json` | Temporary resume point (NOT committed, passed as artifact) |
| `.github/workflows/stock_sync.yml` | GitHub Actions workflow |

## Schedule

Runs daily at 4pm UTC. Also runs automatically when blocked (self-triggers).

## Troubleshooting

**All runs timing out**: GitHub Actions runner got a US IP. Just wait — the
next self-triggered run will likely get an EU IP. The site only accepts
European IPs.

**Infinite loop**: Check that workflow permissions are set to read+write.
The self-trigger only fires on exit code 2 (genuine block), not on crashes.
