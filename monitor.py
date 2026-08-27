import os
import json
import re
import requests
from bs4 import BeautifulSoup

# Each page can set "mode":
#   "total"     (default) — tracks the total product count on the page
#   "available"           — tracks only items NOT marked "Sold Out"
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
    {
        "name": "Nike Mind (available)",
        "url": "https://www.nike.ae/en/trends/nike-mind?pmin=0,01&prefn1=division&prefv1=FOOTWEAR&gs=3&srule=Featured_Generic",
        "mode": "available",
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
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
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


# ── Fetch ────────────────────────────────────────────────────────────────────
def fetch_soup(url: str):
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser"), r.text


# ── Total count (unchanged logic) ────────────────────────────────────────────
def get_total_count(soup) -> int | None:
    el = soup.select_one(".js-search-results__counts")
    if el:
        # Sale/category pages: count is in data attribute
        if el.has_attr("data-search-results-count"):
            return int(el["data-search-results-count"])
        # Search pages: count is in text e.g. "(1)" with only data-keyword attribute
        if el.has_attr("data-keyword"):
            text = el.get_text(strip=True)  # "(1)"
            match = re.search(r"\d+", text)
            if match:
                return int(match.group())

    # Fallback: hidden input
    hidden = soup.select_one("input.in-search-show")
    if hidden and hidden.has_attr("data-products-count"):
        return int(hidden["data-products-count"])

    return None


# ── Available count (items NOT marked "Sold Out") ────────────────────────────
def get_available_count(soup, html: str) -> int | None:
    total = get_total_count(soup)  # for logging / sanity only

    # Strategy 1 — schema.org availability microdata (most reliable if present)
    avail_nodes = soup.find_all(attrs={"itemprop": "availability"})
    if len(avail_nodes) >= 2:
        out = 0
        for n in avail_nodes:
            val = (n.get("content") or n.get("href") or n.get_text() or "").lower()
            if "outofstock" in val or "out_of_stock" in val or "soldout" in val:
                out += 1
        available = len(avail_nodes) - out
        print(f"    [schema.org] total={total} items={len(avail_nodes)} soldout={out} available={available}")
        return available

    # Strategy 2 — product tiles via data-pid, check each tile's text for "sold out"
    pid_map = {}
    for el in soup.select("[data-pid]"):
        pid = el.get("data-pid")
        if pid and pid not in pid_map:
            pid_map[pid] = el
    if pid_map:
        sold = 0
        for pid, el in pid_map.items():
            text = el.get_text(separator=" ", strip=True).lower()
            if "sold out" in text:
                sold += 1
        available = len(pid_map) - sold
        print(f"    [data-pid] total={total} tiles={len(pid_map)} soldout={sold} available={available}")
        return available

    # Strategy 3 — fallback: total minus count of "sold out" text occurrences
    if total is not None:
        sold = len(re.findall(r"sold\s*out", html, re.IGNORECASE))
        available = max(total - sold, 0)
        print(f"    [fallback] total={total} soldout_strings={sold} available={available}")
        return available

    print("    ⚠ Could not determine availability")
    return None


def get_count_for_page(page) -> int | None:
    try:
        soup, html = fetch_soup(page["url"])
    except Exception as e:
        print(f"  ⚠ Fetch error: {e}")
        return None

    mode = page.get("mode", "total")
    if mode == "available":
        return get_available_count(soup, html)
    return get_total_count(soup)


# ── Discord ──────────────────────────────────────────────────────────────────
def send_discord(name: str, old: int, new: int, url: str, label: str = "Item count") -> None:
    direction = "📈" if new > old else "📉"
    message = {
        "embeds": [{
            "title": f"{direction} Nike.ae update — {name}",
            "description": f"{label} changed from **{old}** → **{new}**\n[Open page]({url})",
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
        print(f"Could not load Gist: {e}")
        previous = {}

    updated = dict(previous)

    for page in PAGES:
        name = page["name"]
        url  = page["url"]
        mode = page.get("mode", "total")
        label = "Available items" if mode == "available" else "Item count"
        print(f"\nChecking: {name}  (mode: {mode})")

        count = get_count_for_page(page)
        if count is None:
            print(f"  Skipping — could not read count")
            continue

        print(f"  Current {label.lower()}: {count}")
        old_count = previous.get(name)

        if old_count is None:
            print(f"  First time seeing this page — storing {count}")
            updated[name] = count
        elif count != old_count:
            print(f"  Change detected: {old_count} → {count} — sending Discord ping")
            try:
                send_discord(name, old_count, count, url, label=label)
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
