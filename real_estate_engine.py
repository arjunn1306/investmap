
"""
real_estate_engine.py

A local real-estate underwriting engine that does NOT rely on any closed APIs.
It works on any tabular data you can export or build yourself (CSV/Excel from
Redfin, MLS, county assessor, your manual sheet, etc.).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, List, Iterable

import numpy as np
import pandas as pd


# ------------------------ Data Models ------------------------ #

@dataclass
class PropertyInput:
    """Input description for a single property.

    Required fields:
        - address, city, state, zipcode
        - list_price (purchase price)
        - est_monthly_rent (expected market rent)

    Optional fields improve accuracy but are not required.
    """
    address: str
    city: str
    state: str
    zipcode: str

    list_price: float
    est_monthly_rent: float

    # Optional / nice-to-have
    sq_ft: Optional[float] = None
    beds: Optional[float] = None
    baths: Optional[float] = None
    year_built: Optional[int] = None

    property_tax_annual: Optional[float] = None
    insurance_annual: Optional[float] = None

    hoa_monthly: float = 0.0
    other_monthly_expenses: float = 0.0


@dataclass
class Financing:
    """Financing assumptions for the deal."""
    down_payment_pct: float = 0.20      # 20% down
    interest_rate: float = 0.07         # 7% annual nominal
    loan_term_years: int = 30
    closing_costs_pct: float = 0.03     # 3% of purchase price


@dataclass
class OperatingAssumptions:
    """Operating & long-term assumptions."""
    vacancy_rate: float = 0.05          # 5% of gross rent
    maintenance_pct_rent: float = 0.08  # 8% of gross rent
    capex_pct_rent: float = 0.05        # 5% of gross rent
    management_pct_rent: float = 0.08   # 8% of gross rent

    appreciation_rate: float = 0.03     # 3% annual value growth
    rent_growth_rate: float = 0.025     # 2.5% annual rent growth
    selling_costs_pct: float = 0.06     # 6% of final sale price
    holding_period_years: int = 10

    income_tax_rate: float = 0.24       # Only used if you extend to after-tax


# ------------------------ Utility Functions ------------------------ #

def amortized_payment(principal: float, annual_rate: float, years: int) -> float:
    """Monthly payment for a standard fully-amortizing loan."""
    if principal <= 0:
        return 0.0

    r = annual_rate / 12.0
    n = years * 12

    if r == 0:
        return principal / n

    return principal * (r * (1 + r) ** n) / ((1 + r) ** n - 1)


def remaining_balance(principal: float, annual_rate: float, years: int, payments_made: int) -> float:
    """Remaining balance on an amortizing loan after a given number of monthly payments."""
    if principal <= 0:
        return 0.0

    r = annual_rate / 12.0
    n = years * 12
    pmt = amortized_payment(principal, annual_rate, years)

    if r == 0:
        return principal - pmt * payments_made

    # Standard formula for remaining balance
    bal = principal * (1 + r) ** payments_made - pmt * ((1 + r) ** payments_made - 1) / r
    return max(0.0, bal)


def compute_irr(cash_flows: Iterable[float], tol: float = 1e-6, max_iter: int = 200):
    """Compute IRR via binary search on the discount rate.

    Works without any external financial library.
    Returns None if it fails to converge or if cash flows never change sign.
    """
    cfs = list(cash_flows)
    if len(cfs) < 2:
        return None

    # Must have at least one positive and one negative cash flow
    if not (any(cf > 0 for cf in cfs) and any(cf < 0 for cf in cfs)):
        return None

    def npv(rate: float) -> float:
        return sum(cf / ((1 + rate) ** t) for t, cf in enumerate(cfs))

    low, high = -0.99, 2.0  # -99% to 200% annual
    npv_low = npv(low)
    npv_high = npv(high)

    # If NPV doesn't change sign over [low, high], IRR might be outside this range
    if npv_low * npv_high > 0:
        return None

    for _ in range(max_iter):
        mid = 0.5 * (low + high)
        val = npv(mid)

        if abs(val) < tol:
            return mid

        if npv_low * val < 0:
            high = mid
            npv_high = val
        else:
            low = mid
            npv_low = val

    return mid


# ------------------------ Core Engine ------------------------ #

class RealEstateEngine:
    """Core underwriting engine working on local tabular data (no external API)."""

    def __init__(self, financing: Financing, ops: OperatingAssumptions):
        self.financing = financing
        self.ops = ops

    # ---- Single property analysis ---- #

    def analyze_property(self, p: PropertyInput) -> Dict[str, float]:
        f = self.financing
        o = self.ops

        # Purchase & loan
        down_payment = f.down_payment_pct * p.list_price
        loan_amount = max(0.0, p.list_price - down_payment)
        closing_costs = f.closing_costs_pct * p.list_price
        total_cash_needed = down_payment + closing_costs

        monthly_debt_service = amortized_payment(loan_amount, f.interest_rate, f.loan_term_years)
        annual_debt_service = monthly_debt_service * 12.0

        # Income
        gross_rent_monthly = p.est_monthly_rent
        vacancy = o.vacancy_rate * gross_rent_monthly
        effective_rent_monthly = gross_rent_monthly - vacancy

        # Operating expenses (monthly)
        maint = o.maintenance_pct_rent * gross_rent_monthly
        capex = o.capex_pct_rent * gross_rent_monthly
        mgmt = o.management_pct_rent * gross_rent_monthly

        taxes_monthly = (p.property_tax_annual or 0.0) / 12.0
        insurance_monthly = (p.insurance_annual or 0.0) / 12.0

        fixed_monthly = taxes_monthly + insurance_monthly + p.hoa_monthly + p.other_monthly_expenses
        operating_expenses_monthly = maint + capex + mgmt + fixed_monthly

        noi_annual = (effective_rent_monthly - operating_expenses_monthly) * 12.0

        # Year 1 cash flow (before taxes)
        cashflow_year1 = noi_annual - annual_debt_service

        # Basic metrics
        cap_rate = noi_annual / p.list_price if p.list_price else float("nan")
        coc = cashflow_year1 / total_cash_needed if total_cash_needed else float("nan")
        dscr = noi_annual / annual_debt_service if annual_debt_service else float("nan")

        # Long-term sale pro forma
        hp = o.holding_period_years
        sale_price = p.list_price * ((1 + o.appreciation_rate) ** hp)

        remaining_bal = remaining_balance(
            loan_amount,
            f.interest_rate,
            f.loan_term_years,
            payments_made=hp * 12,
        )

        net_sale_proceeds = sale_price * (1 - o.selling_costs_pct) - remaining_bal

        # Build cash flow stream for IRR (annual steps)
        cfs: List[float] = []
        cfs.append(-total_cash_needed)

        # Start with year-1 CF and grow rent + NOI over time
        cf = cashflow_year1
        for year in range(1, hp + 1):
            if year > 1:
                cf *= (1 + o.rent_growth_rate)

            if year < hp:
                cfs.append(cf)
            else:
                cfs.append(cf + net_sale_proceeds)

        irr = compute_irr(cfs)

        return {
            "address": p.address,
            "city": p.city,
            "state": p.state,
            "zipcode": p.zipcode,
            "price": p.list_price,
            "est_rent": p.est_monthly_rent,

            "noi_annual": noi_annual,
            "debt_service_annual": annual_debt_service,
            "cashflow_year1": cashflow_year1,

            "cap_rate": cap_rate,
            "cash_on_cash": coc,
            "dscr": dscr,
            "irr": irr,

            "down_payment": down_payment,
            "loan_amount": loan_amount,
            "closing_costs": closing_costs,
            "total_cash_needed": total_cash_needed,

            "sale_price_year_end": sale_price,
            "net_sale_proceeds": net_sale_proceeds,
        }

    # ---- DataFrame analysis ---- #

    def analyze_dataframe(self, df: pd.DataFrame, col_map: Dict[str, str]) -> pd.DataFrame:
        """Analyze every row in df using a column mapping.

        col_map: dict from PropertyInput field name -> column name in df.
                 Required keys: address, city, state, zipcode,
                                 list_price, est_monthly_rent.
        """
        results: List[Dict[str, float]] = []
        prop_fields = PropertyInput.__dataclass_fields__.keys()

        for _, row in df.iterrows():
            kwargs = {}
            for field in prop_fields:
                colname = col_map.get(field)
                if colname is not None and colname in df.columns:
                    kwargs[field] = row[colname]

            # Validate required fields
            for req in ["address", "city", "state", "zipcode", "list_price", "est_monthly_rent"]:
                if req not in kwargs or pd.isna(kwargs[req]):
                    raise ValueError(f"Missing required field '{req}' for row: {row.to_dict()}")

            # Coerce to Python floats where appropriate
            float_fields = [
                "list_price",
                "est_monthly_rent",
                "property_tax_annual",
                "insurance_annual",
                "hoa_monthly",
                "other_monthly_expenses",
            ]
            for ff in float_fields:
                if ff in kwargs and kwargs[ff] is not None and not isinstance(kwargs[ff], float):
                    try:
                        kwargs[ff] = float(kwargs[ff])
                    except (TypeError, ValueError):
                        pass

            p = PropertyInput(**kwargs)
            metrics = self.analyze_property(p)
            results.append(metrics)

        return pd.DataFrame(results)


# ------------------------ CLI Helper ------------------------ #

def _build_basic_arg_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Local real estate underwriting engine (no external API required)."
    )
    parser.add_argument("csv", help="Path to CSV file with property data")
    parser.add_argument(
        "--down",
        type=float,
        default=20.0,
        help="Down payment percentage (default: 20)",
    )
    parser.add_argument(
        "--rate",
        type=float,
        default=7.0,
        help="Interest rate in percent (default: 7.0)",
    )
    parser.add_argument(
        "--term",
        type=int,
        default=30,
        help="Loan term in years (default: 30)",
    )
    parser.add_argument(
        "--holding",
        type=int,
        default=10,
        help="Holding period in years (default: 10)",
    )
    return parser


def main():
    """Simple CLI that assumes a generic schema for the CSV.

    Expected columns in the CSV (you can rename your export to match):

        address, city, state, zipcode,
        price, est_rent, taxes, insurance, hoa, other_exp
    """
    parser = _build_basic_arg_parser()
    args = parser.parse_args()

    df = pd.read_csv(args.csv)

    col_map = {
        "address": "address",
        "city": "city",
        "state": "state",
        "zipcode": "zipcode",
        "list_price": "price",
        "est_monthly_rent": "est_rent",
        "property_tax_annual": "taxes",
        "insurance_annual": "insurance",
        "hoa_monthly": "hoa",
        "other_monthly_expenses": "other_exp",
    }

    financing = Financing(
        down_payment_pct=args.down / 100.0,
        interest_rate=args.rate / 100.0,
        loan_term_years=args.term,
    )
    ops = OperatingAssumptions(holding_period_years=args.holding)

    engine = RealEstateEngine(financing, ops)
    results = engine.analyze_dataframe(df, col_map)

    # Sort by cash-on-cash descending and print a compact view
    key_cols = [
        "address", "city", "state", "price", "est_rent",
        "cap_rate", "cash_on_cash", "irr", "dscr", "cashflow_year1",
    ]
    existing_cols = [c for c in key_cols if c in results.columns]

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 160)
    print(results[existing_cols].sort_values("cash_on_cash", ascending=False))


if __name__ == "__main__":
    main()
