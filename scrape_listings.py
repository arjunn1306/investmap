import argparse
import random
import time
import re
from typing import List, Dict, Optional, Set
import requests
from bs4 import BeautifulSoup
import pandas as pd

BASE_SEARCH_URL = "https://www.redfin.com/zipcode/{zip}/page-{page}"
BASE_DOMAIN = "https://www.redfin.com"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.google.com/"
}

def fetch_html(url: str, timeout: int = 15) -> Optional[str]:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        if resp.status_code == 403:
            print(f"[ERROR] 403 Forbidden. Slow down your delay_range.")
            return None
        return resp.text if resp.status_code == 200 else None
    except requests.RequestException:
        return None

def extract_listing_links_from_search(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = {BASE_DOMAIN + a.get("href") if not a.get("href").startswith("http") else a.get("href") 
             for a in soup.select("a[href*='/home/']") if a.get("href")}
    return sorted(links)

def parse_detail_page(url: str, html: str) -> Dict:
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    address_full = title.split("|")[0].strip() if "|" in title else title.strip()

    street = city = state = zipcode = ""
    parts = [p.strip() for p in address_full.split(",")]
    if len(parts) >= 3:
        street, city = parts[0], parts[1]
        state_zip = parts[2].split()
        if len(state_zip) >= 2:
            state, zipcode = state_zip[0], state_zip[1]

    def to_int(pattern):
        m = re.search(pattern, text)
        if not m: return None
        s_clean = m.group(1).replace(",", "").replace("$", "").strip()
        return int(float(s_clean)) if s_clean.replace(".", "", 1).isdigit() else None

    price = to_int(r"(\$\s?[\d,]+)")
    beds = to_int(r"(\d+)\s*bd")
    sq_ft = to_int(r"([\d,]+)\s*sq ft")
    
    # --- AUTOMATED ESTIMATES ---
    return {
        "address": street or address_full,
        "city": city,
        "state": state,
        "zipcode": zipcode,
        "price": price,
        "beds": beds,
        "sq_ft": sq_ft,
        "listing_url": url,
        "est_rent": (price * 0.006) if price else 0, # Monthly 0.6% rule
        "taxes": (price * 0.012) if price else 0,    # Annual 1.2%
        "insurance": (price * 0.002) if price else 0,# Annual 0.2%
        "hoa": 0,
        "other_exp": 0,
    }

def scrape_redfin_zip(zip_code: str, max_pages: int = 2, max_listings: int = None) -> pd.DataFrame:
    """Scrape Redfin listings for a given ZIP code and return a DataFrame."""
    all_urls = []
    for page in range(1, max_pages + 1):
        html = fetch_html(BASE_SEARCH_URL.format(zip=zip_code, page=page))
        if not html:
            break
        urls = extract_listing_links_from_search(html)
        if not urls:
            break
        all_urls.extend(urls)
        time.sleep(random.uniform(1.0, 2.5))

    unique_urls = sorted(set(all_urls))
    if max_listings:
        unique_urls = unique_urls[:max_listings]

    results = []
    for i, url in enumerate(unique_urls):
        print(f"[{i+1}/{len(unique_urls)}] Scraping: {url}")
        html = fetch_html(url)
        if html:
            results.append(parse_detail_page(url, html))
        time.sleep(random.uniform(1.0, 2.5))

    return pd.DataFrame(results) if results else pd.DataFrame()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("zip")
    parser.add_argument("--pages", type=int, default=1)
    args = parser.parse_args()

    all_urls = []
    for page in range(1, args.pages + 1):
        html = fetch_html(BASE_SEARCH_URL.format(zip=args.zip, page=page))
        if not html: break
        all_urls.extend(extract_listing_links_from_search(html))
        time.sleep(random.uniform(1.0, 2.5))

    results = []
    for i, url in enumerate(sorted(set(all_urls))):
        print(f"Scraping {i+1}/{len(all_urls)}: {url}")
        html = fetch_html(url)
        if html: results.append(parse_detail_page(url, html))
        time.sleep(random.uniform(1.0, 2.5))

    df = pd.DataFrame(results)
    out_path = f"redfin_{args.zip}.csv"
    df.to_csv(out_path, index=False)
    print(f"Success! Saved to {out_path}")

if __name__ == "__main__":
    main()