"""
scrape_listings.py

Redfin's Akamai bot protection blocks all automated HTTP clients and headless
browsers. The only reliable way to get listing data is through Redfin's own
"Download All" CSV button in a real browser.

This module exists so app.py can detect SCRAPING_ENABLED = False gracefully
and return a helpful error message instead of crashing.
"""

import pandas as pd


def scrape_redfin_zip(zip_code: str) -> pd.DataFrame:
    """
    Not implemented — Redfin blocks all automated requests.
    Returns empty DataFrame so the caller shows a clear error.
    """
    print(f"[SCRAPER] Automated scraping blocked by Redfin for ZIP {zip_code}")
    return pd.DataFrame()
