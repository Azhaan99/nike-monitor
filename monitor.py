import os
import json
import re
import time
import requests
from bs4 import BeautifulSoup

# Page types:
#   Nike pages ("mode"):
#     "total"     (default) — tracks the total product count on the page
#     "available"           — tracks only items NOT marked "Sold Out"
#   Amazon pages ("type": "amazon"):
#     tracks the buy-box status (unavailable / buyable / pre-order / other sellers)
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
    {
        "name": "Amazon - iPhone Duo 2TB Star White",
        "url": "https://www.amazon.ae/dp/B0HJ9Y9J66",
        "type": "amazon",
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

AMAZON_HEADERS = {
    **HEADERS,
    "Accept-Encoding": "gzip, deflate",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

AMAZON_STATE_LABELS = {
    "UNAVAILABLE":   "Currently unavailable",
    "BUYABLE":       "Available to buy",
    "PREORDER":      "Pre-order open",
    "OTHER_SELLERS": "Available from other sellers",
    "NOT_BUYABLE":   "No buy button (unclear status)",
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


# ── Nike: total count ────────────────────────────────────────────────────────
def get_total_count(soup) -> int | None:
    el = soup.select_one(".js-search-results__counts")
    if el:
        if el.has_attr("data-search-results-count"):
            return int(el["data-search-results-count"])
        if el.has_attr("data-keyword"):
            text = el.get_text(strip=True)
            match = re.search(r"\d+", text)
            if match:
                return int(match.group())

    hidden = soup.select_one("input.in-search-show")
    if hidden and hidden.has_attr("data-products-count"):
        return int(hidden["data-products-count"])

    return None


# ── Nike: available count (items NOT marked "Sold Out") ──────────────────────
def get_available_count(soup, html: str) -> int | None:
    total = get_total_count(soup)

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

    if total is not None:
        sold = len(re.findall(r"sold\s*out", html, re.IGNORECASE))
        available = max(total - sold, 0)
        print(f"    [fallback] total={total} soldout_strings={sold} available={available}")
        return available

    print("    ⚠ Could not determine availability")
    return None


def get_nike_count(page) -> int | None:
    try:
        soup, html = fetch_soup(page["url"])
    except Exception as e:
        print(f"  ⚠ Fetch error: {e}")
        return None

    if page.get("mode", "total") == "available":
        return get_available_count(soup, html)
    return get_total_count(soup)


# ── Amazon: buy-box status ───────────────────────────────────────────────────
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def is_amazon_blocked(html: str) -> bool:
    markers = [
        "validateCaptcha",
        "Enter the characters you see below",
        "api-services-support@amazon.com",
        "Sorry, we just need to make sure you're not a robot",
    ]
    return any(m in html for m in markers)


def parse_amazon(html: str) -> dict | None:
    """Returns {"state", "availability", "price"} or None if the page isn't a usable product page."""
    if is_amazon_blocked(html):
        print("    ⚠ Amazon served a CAPTCHA / robot check")
        return None

    soup = BeautifulSoup(html, "html.parser")

    if not soup.select_one("#productTitle"):
        print("    ⚠ No product title found (unexpected page)")
        return None

    avail_el = soup.select_one("#availability")
    availability = _clean(avail_el.get_text(" ")) if avail_el else ""

    price_el = (
        soup.select_one("#corePrice_feature_div .a-offscreen")
        or soup.select_one("#corePriceDisplay_desktop_feature_div .a-offscreen")
        or soup.select_one("#apex_desktop .a-offscreen")
    )
    price = _clean(price_el.get_text()) if price_el else ""

    has_cart = soup.select_one("#add-to-cart-button") is not None
    has_buy  = soup.select_one("#buy-now-button") is not None
    other_sellers = soup.select_one("#buybox-see-all-buying-choices") is not None

    buybox = soup.select_one("#desktop_buybox") or soup.select_one("#buybox")
    buybox_text = _clean(buybox.get_text(" ")).lower() if buybox else ""
    preorder = "pre-order" in buybox_text or "preorder" in buybox_text

    unavailable = (
        "currently unavailable" in availability.lower()
        or soup.select_one("#outOfStock") is not None
    )

    if has_cart or has_buy:
        state = "PREORDER" if preorder else "BUYABLE"
    elif other_sellers:
        state = "OTHER_SELLERS"
    elif unavailable:
        state = "UNAVAILABLE"
    else:
        state = "NOT_BUYABLE"

    print(f"    [amazon] state={state} cart={has_cart} buy_now={has_buy} "
          f"other_sellers={other_sellers} availability='{availability}' price='{price}'")
    return {"state": state, "availability": availability, "price": price}


def get_amazon_status(url: str) -> dict | None:
    session = requests.Session()
    for attempt in range(2):
        try:
            r = session.get(url, headers=AMAZON_HEADERS, timeout=20)
            if r.status_code in (429, 503):
                print(f"    ⚠ Amazon returned HTTP {r.status_code} (attempt {attempt + 1})")
            else:
                result = parse_amazon(r.text)
                if result:
                    return result
        except Exception as e:
            print(f"    ⚠ Fetch error (attempt {attempt + 1}): {e}")
        if attempt == 0:
            time.sleep(5)
    return None


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


def send_discord_amazon(name: str, old_state: str, status: dict, url: str) -> None:
    new_state = status["state"]
    good = new_state in ("BUYABLE", "PREORDER", "OTHER_SELLERS")
    lines = [
        f"**{AMAZON_STATE_LABELS.get(old_state, old_state)}** → **{AMAZON_STATE_LABELS.get(new_state, new_state)}**",
    ]
    if status.get("availability"):
        lines.append(f"Availability: {status['availability']}")
    if status.get("price"):
        lines.append(f"Price: {status['price']}")
    lines.append(f"[Open page]({url})")

    message = {
        "embeds": [{
            "title": f"{'🟢' if good else '🔴'} Amazon.ae update — {name}",
            "description": "\n".join(lines),
            "color": 0x00A859 if good else 0xFF6B00,
        }]
    }
    r = requests.post(DISCORD_URL, json=message, timeout=10)
    r.raise_for_status()


# ── Main ─────────────────────────────────────────────────────────────────────
def check_nike(page, previous, updated):
    name, url = page["name"], page["url"]
    mode = page.get("mode", "total")
    label = "Available items" if mode == "available" else "Item count"
    print(f"\nChecking: {name}  (mode: {mode})")

    count = get_nike_count(page)
    if count is None:
        print("  Skipping — could not read count")
        return

    print(f"  Current {label.lower()}: {count}")
    old_count = previous.get(name)

    if old_count is None:
        print(f"  First time seeing this page — storing {count}")
        updated[name] = count
    elif count != old_count:
        print(f"  Change detected: {old_count} → {count} — sending Discord ping")
        try:
            send_discord(name, old_count, count, url, label=label)
            print("  ✓ Discord notified")
        except Exception as e:
            print(f"  ✗ Discord failed: {e}")
        updated[name] = count
    else:
        print("  No change")


def check_amazon(page, previous, updated):
    name, url = page["name"], page["url"]
    print(f"\nChecking: {name}  (type: amazon)")

    status = get_amazon_status(url)
    if status is None:
        print("  Skipping — Amazon blocked or page unreadable (stored status kept)")
        return

    state = status["state"]
    print(f"  Current status: {AMAZON_STATE_LABELS.get(state, state)}")
    old_state = previous.get(name)

    if old_state is None:
        print(f"  First time seeing this page — storing {state}")
        updated[name] = state
    elif state != old_state:
        print(f"  Change detected: {old_state} → {state} — sending Discord ping")
        try:
            send_discord_amazon(name, old_state, status, url)
            print("  ✓ Discord notified")
        except Exception as e:
            print(f"  ✗ Discord failed: {e}")
        updated[name] = state
    else:
        print("  No change")


def main():
    print("Loading previous counts from Gist…")
    try:
        previous = load_gist()
    except Exception as e:
        print(f"Could not load Gist: {e}")
        previous = {}

    updated = dict(previous)

    for page in PAGES:
        if page.get("type") == "amazon":
            check_amazon(page, previous, updated)
        else:
            check_nike(page, previous, updated)

    print("\nSaving updated counts to Gist…")
    save_gist(updated)
    print("Done.")


if __name__ == "__main__":
    main()
