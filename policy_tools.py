"""US policy analysis tools: who lives here, what the government collects, and who pays for a change."""

import json
import os

import requests

# Census requires a free key: https://api.census.gov/data/key_signup.html
CENSUS_API_KEY = os.environ.get("CENSUS_API_KEY")
CENSUS_URL = "https://api.census.gov/data/{year}/acs/acs1"
CENSUS_YEARS = [2025, 2024, 2023]  # ACS 1-year estimates come out each September

TREASURY_URL = "https://api.fiscaldata.treasury.gov/services/api/fiscal_service/v1/accounting/mts/mts_table_4"
# Monthly Treasury Statement line code -> revenue source
REVENUE_LINES = {
    "120": "individual_income_taxes",
    "130": "corporate_income_taxes",
    "286": "payroll_taxes",
    "314": "excise_taxes",
    "350": "estate_and_gift_taxes",
    "405": "customs_duties_tariffs",
    "820": "miscellaneous",
    "830": "total",
}

WITS_US_IMPORTS_URL = (
    "https://wits.worldbank.org/API/V1/SDMX/V21/datasource/tradestats-trade/reporter/usa"
    "/year/{year}/partner/wld/product/Total/indicator/MPRT-TRD-VL"
)

# Census table B19001: household income brackets, as (variable, low, high, midpoint).
# The top bracket's average comes from aggregate income instead (see _top_bracket_mean).
INCOME_BRACKETS = [
    ("B19001_002E", 0, 10_000, 5_000),
    ("B19001_003E", 10_000, 15_000, 12_500),
    ("B19001_004E", 15_000, 20_000, 17_500),
    ("B19001_005E", 20_000, 25_000, 22_500),
    ("B19001_006E", 25_000, 30_000, 27_500),
    ("B19001_007E", 30_000, 35_000, 32_500),
    ("B19001_008E", 35_000, 40_000, 37_500),
    ("B19001_009E", 40_000, 45_000, 42_500),
    ("B19001_010E", 45_000, 50_000, 47_500),
    ("B19001_011E", 50_000, 60_000, 55_000),
    ("B19001_012E", 60_000, 75_000, 67_500),
    ("B19001_013E", 75_000, 100_000, 87_500),
    ("B19001_014E", 100_000, 125_000, 112_500),
    ("B19001_015E", 125_000, 150_000, 137_500),
    ("B19001_016E", 150_000, 200_000, 175_000),
    ("B19001_017E", 200_000, None, None),
]
SUMMARY_VARS = {
    "B19001_001E": "households",
    "B19013_001E": "median_household_income",
    "B19025_001E": "aggregate_household_income",
    "B19083_001E": "gini_index",
    "B17001_001E": "poverty_universe",
    "B17001_002E": "people_in_poverty",
}

# Groups the impact table reports, as (label, low, high)
INCOME_GROUPS = [
    ("under $25k", 0, 25_000),
    ("$25k-$50k", 25_000, 50_000),
    ("$50k-$100k", 50_000, 100_000),
    ("$100k-$200k", 100_000, 200_000),
    ("$200k+", 200_000, None),
]
# Rough share of income each group spends, from the shape of the BLS Consumer Expenditure
# Survey (low-income households spend about all their income, high-income ones about half).
SPENDING_TO_INCOME = {"under $25k": 1.0, "$25k-$50k": 0.95, "$50k-$100k": 0.8, "$100k-$200k": 0.65, "$200k+": 0.45}
# A broad VAT or sales tax usually exempts housing, health care and similar. Assume it hits
# this share of spending.
CONSUMPTION_TAX_BASE = 0.6

POLICY_TYPES = {
    "income_tax": "Change income tax rate by rate_change_pct points on household income above income_threshold",
    "consumption_tax": "New or changed national sales tax/VAT of rate_change_pct percent on consumer spending",
    "tariff": "Change the average tariff on all imported goods by rate_change_pct points",
    "household_payment": "Pay (or credit) every household amount_per_household dollars per year, below income_threshold if given",
}

class DataUnavailable(Exception):
    """A data source couldn't answer. The message tells the model what to do next."""


_census_cache: dict[str, dict] = {}  # state name (or "United States") -> profile


def _census(state: str | None) -> dict:
    """Household income distribution for the US or one state from the latest ACS 1-year data.

    Raises DataUnavailable with a message the model can act on.
    """
    if not CENSUS_API_KEY:
        raise DataUnavailable("CENSUS_API_KEY is not set on the server. Tell the user Census data is unavailable.")
    name = (state or "United States").strip()
    if name.lower() in _census_cache:
        return _census_cache[name.lower()]

    variables = ["NAME", *SUMMARY_VARS, *(v for v, *_ in INCOME_BRACKETS)]
    geo = {"for": "us:1"} if name.lower() in ("united states", "us", "usa") else {"for": "state:*"}
    for year in CENSUS_YEARS:
        resp = requests.get(
            CENSUS_URL.format(year=year),
            params={"get": ",".join(variables), **geo, "key": CENSUS_API_KEY},
            timeout=20,
        )
        if resp.status_code == 200:
            break
    else:
        raise DataUnavailable(f"Census ACS data was unavailable for {CENSUS_YEARS}. Try again later.")

    header, *rows = resp.json()
    match = next((r for r in rows if r[0].lower() == name.lower()), None) if len(rows) > 1 else rows[0]
    if match is None:
        raise DataUnavailable(f"State '{state}' not found. Use a full US state name like 'Ohio' or 'New York'.")
    row = dict(zip(header, match))

    profile = {"geography": row["NAME"], "year": year}
    profile.update({label: float(row[var]) for var, label in SUMMARY_VARS.items()})
    profile["brackets"] = [
        {"low": low, "high": high, "mid": mid, "households": float(row[var])}
        for var, low, high, mid in INCOME_BRACKETS
    ]
    profile["brackets"][-1]["mid"] = _top_bracket_mean(profile)
    _census_cache[name.lower()] = profile
    return profile


def _top_bracket_mean(profile: dict) -> float:
    """Average income of $200k+ households: total income minus everyone else's, divided by their count."""
    *lower, top = profile["brackets"]
    rest = sum(b["households"] * b["mid"] for b in lower)
    return (profile["aggregate_household_income"] - rest) / top["households"]


def _income_above(b: dict, threshold: float) -> float:
    """Average income above `threshold` per household in bracket `b`.

    Incomes are spread evenly inside a bracket, except the open-ended top one, which follows
    a Pareto tail with the bracket's mean (the usual shape of top incomes).
    """
    low, high, mean = b["low"], b["high"], b["mid"]
    if threshold <= low:
        return mean - threshold
    if high is not None:
        return (high - threshold) ** 2 / (2 * (high - low)) if threshold < high else 0.0
    alpha = mean / (mean - low)  # Pareto shape that gives this mean above `low`
    return (low / threshold) ** alpha * threshold / (alpha - 1)


def _group_of(income: float) -> str:
    return next(label for label, low, high in INCOME_GROUPS if high is None or income < high)


def _federal_revenue(fiscal_year: int | None) -> dict:
    """Federal revenue by source for a complete fiscal year (Oct-Sep), in $ billions."""
    filters = f"line_code_nbr:in:({','.join(REVENUE_LINES)}),record_calendar_month:eq:09"
    if fiscal_year:
        filters += f",record_fiscal_year:eq:{fiscal_year}"
    rows = requests.get(
        TREASURY_URL,
        params={"filter": filters, "sort": "-record_date", "page[size]": len(REVENUE_LINES),
                "fields": "record_date,record_fiscal_year,line_code_nbr,current_fytd_net_rcpt_amt"},
        timeout=20,
    ).json()["data"]
    if not rows:
        raise DataUnavailable(f"No Treasury data for fiscal year {fiscal_year}. Fiscal years end in September; try an earlier one.")
    revenue = {"fiscal_year": int(rows[0]["record_fiscal_year"])}
    revenue.update({
        REVENUE_LINES[r["line_code_nbr"]] + "_billions": round(float(r["current_fytd_net_rcpt_amt"]) / 1e9, 1)
        for r in rows
    })
    return revenue


def _us_goods_imports_billions() -> tuple[int, float]:
    for year in (2024, 2023, 2022):
        resp = requests.get(WITS_US_IMPORTS_URL.format(year=year), params={"format": "JSON"}, timeout=30)
        if resp.status_code == 200:
            series = resp.json()["dataSets"][0]["series"]
            value = next(iter(series.values()))["observations"]["0"][0]
            return year, value / 1e6  # WITS reports US$ thousands
    raise DataUnavailable("US import data is unavailable right now. Try again later.")


def get_us_household_profile(state: str | None = None) -> str:
    """Income, inequality and poverty for US households, nationally or in one state."""
    try:
        p = _census(state)
    except DataUnavailable as e:
        return json.dumps({"error": str(e)})
    except requests.RequestException as e:
        return json.dumps({"error": f"Census service failed: {e}. Ask the user to try again shortly."})

    groups = {label: 0.0 for label, *_ in INCOME_GROUPS}
    for b in p["brackets"]:
        groups[_group_of(b["low"])] += b["households"]
    return json.dumps({
        "geography": p["geography"],
        "year": p["year"],
        "households_millions": round(p["households"] / 1e6, 2),
        "median_household_income": p["median_household_income"],
        "mean_household_income": round(p["aggregate_household_income"] / p["households"]),
        "gini_index": p["gini_index"],
        "poverty_rate_pct": round(100 * p["people_in_poverty"] / p["poverty_universe"], 1),
        "share_of_households_pct": {k: round(100 * v / p["households"], 1) for k, v in groups.items()},
    })


def get_federal_revenue(fiscal_year: int | None = None) -> str:
    """How much the federal government collected, by source, in a fiscal year."""
    try:
        return json.dumps(_federal_revenue(fiscal_year))
    except DataUnavailable as e:
        return json.dumps({"error": str(e)})
    except requests.RequestException as e:
        return json.dumps({"error": f"Treasury service failed: {e}. Ask the user to try again shortly."})


def estimate_policy_impact(
    policy_type: str,
    rate_change_pct: float = 0,
    income_threshold: float = 0,
    amount_per_household: float = 0,
    state: str | None = None,
) -> str:
    """Static estimate of a policy's revenue and its cost to households by income group."""
    if policy_type not in POLICY_TYPES:
        return json.dumps({"error": f"Unknown policy_type '{policy_type}'. Use one of: {POLICY_TYPES}"})
    if policy_type == "household_payment" and not amount_per_household:
        return json.dumps({"error": "household_payment needs amount_per_household, e.g. 1000."})
    if policy_type != "household_payment" and not rate_change_pct:
        return json.dumps({"error": f"{policy_type} needs rate_change_pct, e.g. 2 for +2 points or -2 for a cut."})

    try:
        p = _census(state)
        revenue = _federal_revenue(None)
        imports = _us_goods_imports_billions() if policy_type == "tariff" else None
    except DataUnavailable as e:
        return json.dumps({"error": str(e)})
    except requests.RequestException as e:
        return json.dumps({"error": f"A data service failed: {e}. Ask the user to try again shortly."})

    rate = rate_change_pct / 100
    total_spending = sum(
        b["households"] * b["mid"] * SPENDING_TO_INCOME[_group_of(b["low"])] for b in p["brackets"]
    )

    # Cost to each bracket's typical household (positive = household pays more)
    groups = {label: {"households": 0.0, "cost": 0.0, "income": 0.0} for label, *_ in INCOME_GROUPS}
    for b in p["brackets"]:
        income, spending = b["mid"], b["mid"] * SPENDING_TO_INCOME[_group_of(b["low"])]
        if policy_type == "income_tax":
            cost = rate * _income_above(b, income_threshold)
        elif policy_type == "consumption_tax":
            cost = rate * spending * CONSUMPTION_TAX_BASE
        elif policy_type == "tariff":
            # Assume importers pass the whole tariff on to consumers, in proportion to spending
            cost = rate * imports[1] * 1e9 * spending / total_spending
        else:
            cost = -amount_per_household if not income_threshold or income < income_threshold else 0.0
        g = groups[_group_of(b["low"])]
        g["households"] += b["households"]
        g["cost"] += cost * b["households"]
        g["income"] += income * b["households"]

    total_cost = sum(g["cost"] for g in groups.values())
    by_group = {
        label: {
            "avg_cost_per_household": round(g["cost"] / g["households"]) if g["households"] else 0,
            "pct_of_income": round(100 * g["cost"] / g["income"], 2) if g["income"] else 0,
            "share_of_total_pct": round(100 * g["cost"] / total_cost, 1) if total_cost else 0,
        }
        for label, g in groups.items()
    }

    result = {
        "policy": POLICY_TYPES[policy_type],
        "inputs": {"rate_change_pct": rate_change_pct, "income_threshold": income_threshold,
                   "amount_per_household": amount_per_household},
        "geography": p["geography"],
        "census_year": p["year"],
        "annual_revenue_change_billions": round(total_cost / 1e9, 1),
        "vs_federal_revenue": {
            "fiscal_year": revenue["fiscal_year"],
            "total_federal_revenue_billions": revenue["total_billions"],
            "change_as_pct_of_total": round(100 * total_cost / 1e9 / revenue["total_billions"], 2),
        },
        "cost_by_household_income": by_group,
        "assumptions": [
            "Static estimate: no change in behavior, prices, wages or growth.",
            "Uses household income from the Census, not taxable income; deductions are ignored.",
            "Each household is placed at its income bracket's midpoint.",
        ],
    }
    if policy_type == "income_tax":
        result["assumptions"].append(
            "Census survey income leaves out capital gains and under-reports the very top, so "
            "revenue from taxing high earners is likely understated."
        )
    if state:
        result["assumptions"].append(f"Revenue is only what {p['geography']} households would pay or receive.")
    if policy_type in ("consumption_tax", "tariff"):
        result["assumptions"].append(
            "Spending shares by income are approximate, following the BLS Consumer Expenditure Survey."
        )
    if policy_type == "consumption_tax":
        result["assumptions"].append(f"The tax applies to {int(CONSUMPTION_TAX_BASE * 100)}% of spending (housing and health care exempt).")
    if policy_type == "tariff":
        result["assumptions"].append(
            f"Applied to {imports[0]} US goods imports of ${imports[1]:,.0f}B, fully passed on to "
            f"consumers. Real imports would fall, so actual revenue would be lower."
        )
    return json.dumps(result)


POLICY_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_us_household_profile",
            "description": (
                "Get US Census data on households, nationally or for one state: number of "
                "households, median and mean income, inequality (Gini), poverty rate, and the "
                "share of households in each income group. Use it to describe who a policy affects."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "state": {"type": "string", "description": "Full US state name, e.g. 'Ohio'. Omit for the whole US."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_federal_revenue",
            "description": (
                "Get US federal revenue for a complete fiscal year from the Treasury, by source: "
                "individual income, corporate, payroll, excise, estate taxes, tariffs (customs) and total."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fiscal_year": {"type": "integer", "description": "Fiscal year ending in September, e.g. 2025. Omit for the latest."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "estimate_policy_impact",
            "description": (
                "Estimate a US tax or transfer policy's yearly fiscal effect (revenue gained or "
                "spent) and its cost to households in each income group, from Census income data "
                "and Treasury revenue. Policy types: " + json.dumps(POLICY_TYPES)
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "policy_type": {"type": "string", "enum": list(POLICY_TYPES)},
                    "rate_change_pct": {
                        "type": "number",
                        "description": "Percentage-point change for income_tax/tariff, or the rate for consumption_tax. Negative for a cut.",
                    },
                    "income_threshold": {
                        "type": "number",
                        "description": "income_tax: only income above this is affected. household_payment: only households below this get it. 0 = everyone.",
                    },
                    "amount_per_household": {
                        "type": "number",
                        "description": "household_payment only: dollars per household per year.",
                    },
                    "state": {"type": "string", "description": "Limit to one state's households, e.g. 'Texas'. Omit for the whole US."},
                },
                "required": ["policy_type"],
            },
        },
    },
]

POLICY_TOOL_MAP = {
    "get_us_household_profile": get_us_household_profile,
    "get_federal_revenue": get_federal_revenue,
    "estimate_policy_impact": estimate_policy_impact,
}
