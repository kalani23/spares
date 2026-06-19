"""
debug_github_block.py
========================
One-off diagnostic: run this directly in GitHub Actions to see exactly
what partworks.de returns right now — confirms whether GitHub's IP pool
is hard-blocked, and whether it's the same stripped-page signature as
before or something new (CAPTCHA, hard 403, etc.)

Usage (in workflow or locally):
    python debug_github_block.py
"""
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

print("Checking outbound IP this runner is using...")
try:
    ip_resp = requests.get("https://api.ipify.org?format=json", timeout=10)
    print(f"  Runner IP: {ip_resp.json()}")
except Exception as e:
    print(f"  Could not check IP: {e}")

print("\nTesting partworks.de search endpoint...")
url = "https://partworks.de/search/?qs=2879"
s = requests.Session()
s.headers.update(HEADERS)

try:
    r = s.get(url, timeout=20, allow_redirects=True)
    print(f"  Status code: {r.status_code}")
    print(f"  Final URL:   {r.url}")
    print(f"  Body length: {len(r.text)}")
    print(f"  First 500 chars of body:")
    print(r.text[:500])
    print("\n  Headers received:")
    for k, v in r.headers.items():
        print(f"    {k}: {v}")
except Exception as e:
    print(f"  Request failed: {e}")
