import os
import json
import re
import requests
from bs4 import BeautifulSoup

PAGES = [
    {
        "name": "Jordan 4 Sale",
        "url": "https://www.nike.ae/en/sale?gs=3&pmin=0%2C01&prefn1=division&prefn2=jordan_editions&prefv1=FOOTWEAR&prefv2=jordan_4&srule=price-high-to-low&start=0&sz=24",
    },
    {
        "name": "Jordan 4 Retro",
        "url": "https://www.nike.ae/en/search?q=jordan%204%20retro&pmin=149,01&pmax=806,01&prefn1=division&prefv1=FOOTWEAR&prefn2=gender&prefv2=MENS&gs=3&srule=Featured_Generic",
    },
    {
        "name": "Jordan 2 3 5 6 9 10 11 12 14 Sale",
        "url": "https://www.nike.ae/en/sale?pmin=0,01&prefn1=division&prefv1=FOOTWEAR&prefn2=jordan_editions&prefv2=jordan_14|jordan_3|Jordan_5|Jordan_6|Jordan_12|jordan_9|jordan_11|jordan_10&prefn3=nikeBrands&prefv3=jordan&gs=3&srule=latest-products",
    },
]

GIST_ID       = os.environ["GIST_ID"]
GIST_FILENAME = "nike_counts.json"
GITHUB_TOKEN  = os.environ["GITHUB_TOKEN"]
DISCORD_URL   = os.environ["DISCORD_WEBHOOK_URL"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AE,en;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
    "X-Forwarded-For": "94.200.0.1",  # UAE IP range
}

# ── Gist helpers ─────────────────────────────────────────────────────────────
def load_gist() -> dict:
    url = f"https://api.github.com/gists/{GIST_ID}"
    r = requests.get(url, headers={"Authorization": f"token {GITHUB_TOKEN}"})
    r.raise_for_status()
    content = r.json()["files"][GIST_FILENAME]["content"]
    return json.loads(content)


def save_gist(data: dict) -> None:
    url = f"https://api.github.com/gists/{GIST_ID}"
    payload = {"files": {GIST_FILENAME: {"content": json.dumps(data, indent=2)}}}
    r = requests.patch(url, json=payload, headers={"Authorization": f"token {GITHUB_TOKEN}"})
    r.raise_for_status()


# ── Scraper ──────────────────────────────────────────────────────────────────
def get_count(page: dict) -> int | None:
    url = page["url"]
    try:
        session = requests.Session()
        # First hit the homepage to get cookies like a real browser would
        session.get("https://www.nike.ae/en/home", headers=HEADERS, timeout=20)
        # Now hit the actual page
        r = session.get(url, headers=HEADERS, timeout=20)
        r.raise_for_status()

        print(f"  HTTP {r.status_code}, locale in HTML: ", end="")
        locale_match = re.search(r'data-locale="([^"]+)"', r.text)
        print(locale_match.group(1) if locale_match else "not found")

        soup = BeautifulSoup(r.text, "html.parser")

        # Method 1: data attribute on count span (sale/category pages)
        el = soup.select_one(".js-search-results__counts")
        if el and el.has_attr("data-search-results-count"):
            return int(el["data-search-results-count"])

        # Method 2: hidden input data-products-count (search pages)
        hidden = soup.select_one("input.in-search-show")
        if hidden and hidden.has_attr("data-products-count"):
            return int(hidden["data-products-count"])

        # Method 3: section data-products-count
        section = soup.select_one("section.b-product-grid")
        if section and section.has_attr("data-products-count"):
            return int(section["data-products-count"])

        # Method 4: parse text from count span
        if el:
            text = el.get_text(strip=True)
            match = re.search(r"\d+", text)
            if match:
                return int(match.group())

        # Method 5: filters count div
        filters_count = soup.select_one(".js-search-results-filters-count")
        if filters_count:
            text = filters_count.get_text(strip=True)
            match = re.search(r"\d+", text)
            if match:
                return int(match.group())

        print(f"  ⚠ No count found. Checking what elements exist...")
        print(f"  in-search-show exists: {soup.select_one('input.in-search-show') is not None}")
        print(f"  js-search-results__counts exists: {soup.select_one('.js-search-results__counts') is not None}")
        print(f"  b-product-grid exists: {soup.select_one('section.b-product-grid') is not None}")
        return None

    except Exception as e:
        print(f"  ⚠ Error: {e}")
        return None


# ── Discord ──────────────────────────────────────────────────────────────────
def send_discord(name: str, old: int, new: int, url: str) -> None:
    direction = "📈" if new > old else "📉"
    message = {
        "embeds": [{
            "title": f"{direction} Nike.ae update — {name}",
            "description": f"Item count changed from **{old}** → **{new}**\n[Open page]({url})",
            "color": 0x00A859 if new > old else 0xFF6B00,
        }]
    }
    r = requests.post(DISCORD_URL, json=message, timeout=10)
    r.raise_for_status()


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("Loading previous counts from Gist…")
    try:
        previous = load_gist()
    except Exception as e:
        print(f"Could not load Gist (first run?): {e}")
        previous = {}

    updated = dict(previous)

    for page in PAGES:
        name = page["name"]
        url  = page["url"]
        print(f"\nChecking: {name}")

        count = get_count(page)
        if count is None:
            print(f"  Skipping — could not read count")
            continue

        print(f"  Current count: {count}")
        old_count = previous.get(name)

        if old_count is None:
            print(f"  First time seeing this page — storing {count}")
            updated[name] = count
        elif count != old_count:
            print(f"  Change detected: {old_count} → {count} — sending Discord ping")
            try:
                send_discord(name, old_count, count, url)
                print(f"  ✓ Discord notified")
            except Exception as e:
                print(f"  ✗ Discord failed: {e}")
            updated[name] = count
        else:
            print(f"  No change")

    print("\nSaving updated counts to Gist…")
    save_gist(updated)
    print("Done.")


if __name__ == "__main__":
    main()
