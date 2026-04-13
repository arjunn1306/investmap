import argparse
import pandas as pd
from real_estate_engine import Financing, OperatingAssumptions, RealEstateEngine

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv", help="Path to listings CSV")
    parser.add_argument("--down", type=float, default=25.0)
    parser.add_argument("--rate", type=float, default=7.0)
    args = parser.parse_args()

    df = pd.read_csv(args.csv)
    # Filter out properties with no data
    df = df[(df['price'] > 0) & (df['est_rent'] > 0)].copy()

    col_map = {
        "address": "address", "city": "city", "state": "state", "zipcode": "zipcode",
        "list_price": "price", "est_monthly_rent": "est_rent",
        "property_tax_annual": "taxes", "insurance_annual": "insurance",
        "hoa_monthly": "hoa", "other_monthly_expenses": "other_exp",
    }

    engine = RealEstateEngine(
        Financing(down_payment_pct=args.down/100, interest_rate=args.rate/100),
        OperatingAssumptions()
    )
    
    results = engine.analyze_dataframe(df, col_map)

    key_cols = [
        "address", "price", "est_rent", "cap_rate", 
        "cash_on_cash", "irr", "cashflow_year1"
    ]

    results_sorted = results.sort_values("cash_on_cash", ascending=False)
    
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print("\n--- INVESTMENT RANKINGS (Sorted by Cash-on-Cash) ---")
    print(results_sorted[key_cols].head(15))

if __name__ == "__main__":
    main()