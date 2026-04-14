"""
scrape_listings.py — Downloads active for-sale listings from Redfin's GIS-CSV
endpoint (the same endpoint Redfin's "Download All" button uses).

No browser / JS rendering needed. Works on both local machines and cloud servers
because it hits an API endpoint, not a JavaScript-rendered page.
"""

import io
import json
import re
import requests
import pandas as pd
from typing import Optional

AUTOCOMPLETE_URL = "https://www.redfin.com/stingray/do/location-autocomplete"
GIS_CSV_URL      = "https://www.redfin.com/stingray/api/gis-csv"

def _make_session() -> requests.Session:
    """Create a session that mimics a real browser, including cookies from homepage."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Referer": "https://www.redfin.com/",
    })
    try:
        # Visit homepage first to get session cookies (bypasses bot detection)
        session.get("https://www.redfin.com/", timeout=15)
    except Exception:
        pass
    # Switch to JSON accept header for API calls
    session.headers.update({
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def _to_num(val) -> Optional[float]:
    """Coerce a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        s = re.sub(r"[^\d.\-]", "", str(val))
        return float(s) if s else None
    except (ValueError, TypeError):
        return None


def _get_region(zip_code: str, session: requests.Session) -> Optional[dict]:
    """
    Query Redfin's autocomplete API to get region metadata for a ZIP code.
    Returns {'region_id', 'region_type', 'market'} or None.
    """
    try:
        r = session.get(
            AUTOCOMPLETE_URL,
            params={"location": zip_code, "count": 10, "v": 2},
            timeout=15,
        )
        print(f"[REDFIN] Autocomplete status: {r.status_code}")

        # Redfin prepends "{}&&\n" before the real JSON payload
        text = r.text.strip()
        if text.startswith("{}&&"):
            text = text[4:].strip()
        data = json.loads(text)

        # Collect all rows from all sections
        all_rows = []
        for section in data.get("payload", {}).get("sections", []):
            all_rows.extend(section.get("rows", []))

        print(f"[REDFIN] Autocomplete rows: {len(all_rows)}")
        for row in all_rows:
            print(f"  type={row.get('type')} name={row.get('name')} market={row.get('market')} id={row.get('id')}")

        # Prefer type 2 (ZIP code), fall back to first result
        match = next((r for r in all_rows if str(r.get("type", "")) == "2"), None)
        if match is None and all_rows:
            match = all_rows[0]
            print(f"[REDFIN] No type-2 row found, using first result")

        if match:
            tid = match.get("id", {})
            return {
                "region_id":   tid.get("tableId"),
                "region_type": tid.get("type", 2),
                "market":      match.get("market", ""),
            }
    except Exception as e:
        print(f"[REDFIN AUTOCOMPLETE ERROR] {e}")
    return None


def scrape_redfin_zip(zip_code: str) -> pd.DataFrame:
    """
    Download active for-sale listings for *zip_code* via Redfin's GIS-CSV
    endpoint.  Returns a DataFrame with normalized columns:

        address, city, state, zipcode, price, beds, sq_ft,
        hoa, other_exp, taxes, insurance, est_rent, listing_url, lat, lon

    lat/lon come directly from Redfin — no geocoding needed for most listings.
    """
    session = _make_session()
    region = _get_region(zip_code, session)
    if not region:
        print(f"[REDFIN] Could not resolve region for ZIP {zip_code}")
        return pd.DataFrame()

    params = {
        "al":          1,
        "market":      region["market"],
        "region_id":   region["region_id"],
        "region_type": region["region_type"],
        "status":      1,               # active for sale
        "uipt":        "1,2,3,4,5,6",  # all property types
        "v":           8,
    }

    print(f"[REDFIN] GIS-CSV params: {params}")
    try:
        r = session.get(GIS_CSV_URL, params=params, timeout=30)
        print(f"[REDFIN] GIS-CSV status: {r.status_code}, size: {len(r.text)} bytes")
        print(f"[REDFIN] GIS-CSV first 200 chars: {r.text[:200]}")
        if r.status_code != 200:
            print(f"[REDFIN] GIS-CSV returned HTTP {r.status_code}")
            return pd.DataFrame()
        raw = pd.read_csv(io.StringIO(r.text))
        print(f"[REDFIN] CSV rows: {len(raw)}, columns: {list(raw.columns)[:6]}")
    except Exception as e:
        print(f"[REDFIN] CSV download/parse error: {e}")
        return pd.DataFrame()

    if raw.empty:
        return pd.DataFrame()

    # Strip extra whitespace from column names
    raw.columns = [c.strip() for c in raw.columns]

    # Redfin's URL column has a very long name starting with "URL"
    url_col = next((c for c in raw.columns if c.startswith("URL")), None)

    rows = []
    for _, row in raw.iterrows():
        price = _to_num(row.get("PRICE"))
        if not price:
            continue

        # Prefer dedicated address columns; fall back to parsing LOCATION
        street = str(row.get("ADDRESS", "")).strip()
        city   = str(row.get("CITY", "")).strip()
        state  = str(row.get("STATE OR PROVINCE", "")).strip()

        if not street:
            loc   = str(row.get("LOCATION", ""))
            parts = [p.strip() for p in loc.split(",")]
            street = parts[0] if parts else ""
            city   = parts[1] if len(parts) > 1 else ""
            state  = parts[2].split()[0] if len(parts) > 2 and parts[2].split() else ""

        zipcode = str(row.get("ZIP OR POSTAL CODE", zip_code))
        zipcode = zipcode.split(".")[0].zfill(5)  # "95037.0" → "95037"

        lat = _to_num(row.get("LATITUDE"))
        lon = _to_num(row.get("LONGITUDE"))
        hoa = _to_num(row.get("HOA/MONTH")) or 0.0

        rows.append({
            "address":     street,
            "city":        city,
            "state":       state,
            "zipcode":     zipcode,
            "price":       price,
            "beds":        _to_num(row.get("BEDS")),
            "sq_ft":       _to_num(row.get("SQUARE FEET")),
            "hoa":         hoa,
            "other_exp":   0.0,
            "taxes":       price * 0.012,   # annual, 1.2% estimate
            "insurance":   price * 0.002,   # annual, 0.2% estimate
            "est_rent":    price * 0.006,   # monthly, 0.6% rule (UI slider overrides)
            "listing_url": str(row.get(url_col, "")) if url_col else None,
            "lat":         lat,
            "lon":         lon,
        })

    return pd.DataFrame(rows)
