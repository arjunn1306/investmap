"""
app.py — Flask backend for the Real Estate Investment Map.

Endpoints:
  GET  /                → index.html
  POST /api/analyze     → analyze by ZIP (uses cached CSV or scrapes locally)
  POST /api/upload      → analyze an uploaded CSV file
"""

import os
import io
import json
import math
import time

import requests
import pandas as pd
from flask import Flask, render_template, request, jsonify
from werkzeug.exceptions import HTTPException

from real_estate_engine import Financing, OperatingAssumptions, RealEstateEngine

# Only import scraper when running locally (cloud IPs get blocked by Redfin)
try:
    from scrape_listings import scrape_redfin_zip
    SCRAPING_ENABLED = True
except ImportError:
    SCRAPING_ENABLED = False

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024   # 5 MB upload limit

@app.errorhandler(Exception)
def handle_exception(e):
    if isinstance(e, HTTPException):
        return jsonify({"error": e.description}), e.code
    return jsonify({"error": str(e)}), 500

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
# On Render / cloud, write to /tmp so we have write permissions
DATA_DIR   = os.environ.get("DATA_DIR", BASE_DIR)
GEOCACHE   = os.path.join(DATA_DIR, "geocode_cache.json")
NOMINATIM  = {"User-Agent": "RealEstateInvestMap/1.0 (educational-use)"}


# ── Geocoding ─────────────────────────────────────────────────────────────── #

def _geo_load() -> dict:
    try:
        with open(GEOCACHE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _geo_save(cache: dict) -> None:
    try:
        with open(GEOCACHE, "w") as f:
            json.dump(cache, f, indent=2)
    except OSError:
        pass  # best-effort on read-only filesystems


def geocode_address(address, city, state, zipcode, cache):
    key = f"{address}|{city}|{state}|{zipcode}"
    if key in cache:
        return cache[key]
    query = f"{address}, {city}, {state} {zipcode}, USA"
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "us"},
            headers=NOMINATIM, timeout=10,
        )
        data = r.json()
        if data:
            result = {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
            cache[key] = result
            _geo_save(cache)
            time.sleep(1.1)
            return result
    except Exception as e:
        print(f"[GEOCODE] {query}: {e}")
    return None


def geocode_zip(zipcode, cache):
    key = f"zip|{zipcode}"
    if key in cache:
        return cache[key]
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"postalcode": zipcode, "country": "US", "format": "json", "limit": 1},
            headers=NOMINATIM, timeout=10,
        )
        data = r.json()
        if data:
            result = {"lat": float(data[0]["lat"]), "lon": float(data[0]["lon"])}
            cache[key] = result
            _geo_save(cache)
            time.sleep(1.1)
            return result
    except Exception as e:
        print(f"[GEOCODE ZIP] {zipcode}: {e}")
    return None


# ── Shared helpers ─────────────────────────────────────────────────────────── #

def safe_float(val):
    if val is None:
        return None
    try:
        f = float(val)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError):
        return None


def fill_missing_columns(df: pd.DataFrame, rent_pct: float) -> pd.DataFrame:
    """
    Fill missing financial columns with estimates.

    Rent is adjusted by bedroom count because rent-to-price ratios differ by
    unit size — smaller units command higher ratios than large single-family homes.
    This ensures cap rates vary meaningfully across the portfolio rather than
    being identical for every property.
    """
    df = df.copy()
    for col in ["price", "est_rent", "taxes", "insurance", "hoa", "other_exp", "beds"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Bed-count multiplier: real markets show smaller units have higher rent/price ratios
    BED_MULTIPLIER = {0: 1.35, 1: 1.25, 2: 1.10, 3: 1.00, 4: 0.88, 5: 0.76}

    def beds_multiplier(beds_val):
        try:
            b = int(beds_val)
            return BED_MULTIPLIER.get(b, 0.70)  # 6+ beds → lower ratio
        except (TypeError, ValueError):
            return 1.00  # unknown → use base rate

    # Estimate rent only for rows where est_rent is missing
    if "est_rent" not in df.columns:
        df["est_rent"] = float("nan")
    mask = df["est_rent"].isna() | (df["est_rent"] == 0)
    if mask.any():
        beds_col = df.get("beds", pd.Series([None] * len(df), index=df.index))
        multipliers = beds_col.map(beds_multiplier)
        df.loc[mask, "est_rent"] = df.loc[mask, "price"] * rent_pct * multipliers[mask]

    # Taxes: use sq_ft-based estimate when available (assessments track sq_ft better than price)
    if "taxes" not in df.columns:
        df["taxes"] = float("nan")
    tax_mask = df["taxes"].isna() | (df["taxes"] == 0)
    if tax_mask.any():
        if "sq_ft" in df.columns and df["sq_ft"].notna().any():
            # ~$1.20/sqft/year is a rough national average; scales with size not list price
            sqft = df.loc[tax_mask, "sq_ft"].fillna(df["sq_ft"].median())
            df.loc[tax_mask, "taxes"] = sqft * 1.20
        else:
            df.loc[tax_mask, "taxes"] = df.loc[tax_mask, "price"] * 0.012

    # Insurance: flat $/sqft is more realistic than % of price
    if "insurance" not in df.columns:
        df["insurance"] = float("nan")
    ins_mask = df["insurance"].isna() | (df["insurance"] == 0)
    if ins_mask.any():
        if "sq_ft" in df.columns and df["sq_ft"].notna().any():
            sqft = df.loc[ins_mask, "sq_ft"].fillna(df["sq_ft"].median())
            df.loc[ins_mask, "insurance"] = sqft * 0.55  # ~$0.55/sqft/year
        else:
            df.loc[ins_mask, "insurance"] = df.loc[ins_mask, "price"] * 0.002

    for col in ["hoa", "other_exp"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = df[col].fillna(0.0)
    return df


def underwrite_and_geocode(df, params):
    """Run the underwriting engine + geocoding on a dataframe. Returns JSON payload."""
    rent_pct     = params["rent_pct"]
    min_cap_rate = params["min_cap_rate"]
    min_coc      = params["min_coc"]
    max_price    = params["max_price"]
    down_pct     = params["down_pct"]
    rate         = params["rate"]
    zip_code     = params.get("zip_code", "")

    df = fill_missing_columns(df, rent_pct)
    df = df[df["price"].notna() & (df["price"] > 0)].copy()
    df = df[df["est_rent"].notna() & (df["est_rent"] > 0)].copy()
    if max_price:
        df = df[df["price"] <= max_price].copy()

    # Drop rows missing required string fields (scraper sometimes returns NaN address/city/state)
    for str_col in ["address", "city", "state", "zipcode"]:
        if str_col in df.columns:
            df = df[df[str_col].notna() & (df[str_col].astype(str).str.strip() != "")].copy()

    total_scraped = len(df)
    if total_scraped == 0:
        return None, "No valid listings after filtering. Try relaxing your criteria."

    col_map = {
        "address": "address", "city": "city", "state": "state", "zipcode": "zipcode",
        "list_price": "price", "est_monthly_rent": "est_rent",
        "property_tax_annual": "taxes", "insurance_annual": "insurance",
        "hoa_monthly": "hoa", "other_monthly_expenses": "other_exp",
    }
    engine = RealEstateEngine(
        Financing(down_payment_pct=down_pct / 100, interest_rate=rate / 100),
        OperatingAssumptions(),
    )
    try:
        results = engine.analyze_dataframe(df, col_map)
    except Exception as e:
        return None, f"Underwriting failed: {e}"

    filtered = results[
        (results["cap_rate"] >= min_cap_rate) | (results["cash_on_cash"] >= min_coc)
    ].copy().sort_values("cap_rate", ascending=False)

    if "listing_url" in df.columns:
        url_map = df.drop_duplicates(subset=["address"]).set_index("address")["listing_url"].to_dict()
        filtered["listing_url"] = filtered["address"].map(url_map)

    # Pre-extract lat/lon embedded in source data (Redfin GIS-CSV includes them)
    coord_map = {}
    if "lat" in df.columns and "lon" in df.columns:
        for _, src in df.iterrows():
            addr = str(src.get("address", "")).strip()
            lat  = safe_float(src.get("lat"))
            lon  = safe_float(src.get("lon"))
            if addr and lat is not None and lon is not None:
                coord_map[addr] = {"lat": lat, "lon": lon}

    geo_cache = _geo_load()
    inferred_zip = zip_code or (str(df["zipcode"].dropna().iloc[0]) if "zipcode" in df.columns and not df["zipcode"].dropna().empty else "")

    # Use data centroid as map center if we have embedded coords; otherwise geocode zip
    if coord_map:
        lats = [c["lat"] for c in coord_map.values()]
        lons = [c["lon"] for c in coord_map.values()]
        zip_center = {"lat": sum(lats) / len(lats), "lon": sum(lons) / len(lons)}
    else:
        zip_center = geocode_zip(inferred_zip, geo_cache) if inferred_zip else None

    # Cap fresh Nominatim calls to avoid gunicorn timeout (cache hits are free)
    MAX_NEW_GEOCODE = 15
    new_geocode_count = 0

    listings = []
    for _, row in filtered.iterrows():
        row_zip = str(row.get("zipcode", inferred_zip))
        addr    = str(row.get("address", "")).strip()

        # 1. Embedded coords from CSV (instant)
        coords = coord_map.get(addr)

        # 2. Already cached in geocode_cache.json (instant)
        if not coords:
            cache_key = f"{addr}|{row.get('city', '')}|{row.get('state', '')}|{row_zip}"
            if cache_key in geo_cache:
                coords = geo_cache[cache_key]

        # 3. Fresh Nominatim call (capped to avoid timeout)
        if not coords and new_geocode_count < MAX_NEW_GEOCODE:
            coords = geocode_address(
                addr, str(row.get("city", "")),
                str(row.get("state", "")), row_zip, geo_cache,
            )
            new_geocode_count += 1
        listing = {
            "address":           str(row.get("address", "")),
            "city":              str(row.get("city", "")),
            "state":             str(row.get("state", "")),
            "zipcode":           row_zip,
            "price":             safe_float(row.get("price")),
            "est_rent":          safe_float(row.get("est_rent")),
            "cap_rate":          safe_float(row.get("cap_rate")),
            "cash_on_cash":      safe_float(row.get("cash_on_cash")),
            "irr":               safe_float(row.get("irr")),
            "cashflow_year1":    safe_float(row.get("cashflow_year1")),
            "dscr":              safe_float(row.get("dscr")),
            "noi_annual":        safe_float(row.get("noi_annual")),
            "down_payment":      safe_float(row.get("down_payment")),
            "total_cash_needed": safe_float(row.get("total_cash_needed")),
            "listing_url":       row.get("listing_url") or None,
        }
        if coords:
            listing["lat"] = coords["lat"]
            listing["lon"] = coords["lon"]
        listings.append(listing)

    return {
        "listings":      listings,
        "total":         len(listings),
        "total_scraped": total_scraped,
        "zip_center":    zip_center,
        "zip_code":      inferred_zip,
    }, None


def normalize_redfin_csv(df: pd.DataFrame) -> pd.DataFrame:
    """
    If the uploaded CSV looks like a raw Redfin download (uppercase columns),
    map it to the standard lowercase column names we expect.
    Returns the df unchanged if it already has standard columns.
    """
    df = df.copy()
    # Strip whitespace from column names
    df.columns = [c.strip() for c in df.columns]

    # Already normalized — nothing to do
    if "address" in df.columns and "price" in df.columns:
        return df

    # Redfin column → our standard column
    redfin_map = {
        "ADDRESS":           "address",
        "CITY":              "city",
        "STATE OR PROVINCE": "state",
        "ZIP OR POSTAL CODE":"zipcode",
        "PRICE":             "price",
        "BEDS":              "beds",
        "SQUARE FEET":       "sq_ft",
        "HOA/MONTH":         "hoa",
        "LATITUDE":          "lat",
        "LONGITUDE":         "lon",
    }
    df = df.rename(columns=redfin_map)

    # Redfin URL column has a very long name starting with "URL"
    url_col = next((c for c in df.columns if c.startswith("URL")), None)
    if url_col:
        df = df.rename(columns={url_col: "listing_url"})

    # Clean up numeric price/zip fields Redfin sometimes exports oddly
    if "zipcode" in df.columns:
        df["zipcode"] = df["zipcode"].astype(str).str.split(".").str[0].str.zfill(5)

    return df


def parse_params(source: dict) -> dict:
    return {
        "min_cap_rate": float(source.get("min_cap_rate", 0.04)),
        "min_coc":      float(source.get("min_coc",      0.03)),
        "max_price":    float(source.get("max_price",    0))    or None,
        "down_pct":     float(source.get("down_pct",     25.0)),
        "rate":         float(source.get("rate",          7.0)),
        "rent_pct":     float(source.get("rent_pct",      0.7)) / 100,
        "zip_code":     str(source.get("zip_code",        "")),
    }


# ── Routes ────────────────────────────────────────────────────────────────── #

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/analyze", methods=["POST"])
def analyze():
    """Analyze a ZIP code — loads cached CSV or scrapes (local-only)."""
    try:
        body     = request.get_json(silent=True) or {}
        zip_code = str(body.get("zip_code", "")).strip()

        if not zip_code or not zip_code.isdigit() or len(zip_code) != 5:
            return jsonify({"error": "Please enter a valid 5-digit ZIP code."}), 400

        force_refresh = bool(body.get("force_refresh", False))
        csv_path      = os.path.join(DATA_DIR, f"redfin_{zip_code}.csv")

        if force_refresh or not os.path.exists(csv_path):
            if not SCRAPING_ENABLED:
                return jsonify({
                    "error": (
                        "Live scraping is disabled on this server (Redfin blocks cloud IPs). "
                        "Run scrape_listings.py locally, then upload the CSV using the Upload tab."
                    )
                }), 503
            print(f"[INFO] Scraping Redfin for ZIP {zip_code} …")
            df = scrape_redfin_zip(zip_code)
            if df.empty:
                return jsonify({"error": f"No listings found for ZIP {zip_code}. Try another ZIP."}), 404
            df.to_csv(csv_path, index=False)
        else:
            df = pd.read_csv(csv_path)

        body["zip_code"] = zip_code
        payload, err = underwrite_and_geocode(df, parse_params(body))
        if err:
            return jsonify({"error": err}), 404
        return jsonify(payload)
    except Exception as e:
        print(f"[ERROR /api/analyze] {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/upload", methods=["POST"])
def upload():
    """Analyze a user-uploaded CSV file."""
    try:
        if "file" not in request.files:
            return jsonify({"error": "No file uploaded."}), 400

        f = request.files["file"]
        if not f.filename.lower().endswith(".csv"):
            return jsonify({"error": "Please upload a .csv file."}), 400

        try:
            df = pd.read_csv(io.StringIO(f.read().decode("utf-8", errors="replace")))
        except Exception as e:
            return jsonify({"error": f"Could not parse CSV: {e}"}), 400

        # Normalize Redfin-format CSV (uppercase columns) to our standard lowercase names
        df = normalize_redfin_csv(df)

        required = {"address", "city", "state", "zipcode", "price"}
        missing  = required - set(df.columns)
        if missing:
            return jsonify({"error": f"CSV is missing required columns: {', '.join(sorted(missing))}"}), 400

        params = parse_params(request.form)
        payload, err = underwrite_and_geocode(df, params)
        if err:
            return jsonify({"error": err}), 404
        return jsonify(payload)
    except Exception as e:
        print(f"[ERROR /api/upload] {e}")
        return jsonify({"error": str(e)}), 500


# ── Run ───────────────────────────────────────────────────────────────────── #

if __name__ == "__main__":
    port  = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_ENV", "development") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug)
