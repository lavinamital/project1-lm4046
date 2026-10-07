"""The tools the harness can run, and the JSON that describes them to the model."""

import json

import requests

from policy_tools import POLICY_TOOL_MAP, POLICY_TOOLS

# All free, no API key.
WORLD_BANK_URL = "https://api.worldbank.org/v2"
WITS_URL = "https://wits.worldbank.org/API/V1/SDMX/V21/datasource/tradestats-trade"
WIKIDATA_URL = "https://query.wikidata.org/sparql"
WIKIPEDIA_API_URL = "https://en.wikipedia.org/w/api.php"
WIKIPEDIA_SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/"

# Wikimedia rejects requests without a descriptive User-Agent.
HEADERS = {"User-Agent": "nation-brief-class-project/1.0"}

# World Bank indicator code -> the name the model sees
ECONOMY_INDICATORS = {
    "NY.GDP.MKTP.CD": "gdp_usd_billions",
    "NY.GDP.MKTP.KD.ZG": "gdp_growth_pct",
    "NY.GDP.PCAP.CD": "gdp_per_capita_usd",
    "SP.POP.TOTL": "population",
    "NE.EXP.GNFS.CD": "exports_usd_billions",
    "NE.IMP.GNFS.CD": "imports_usd_billions",
    "NV.AGR.TOTL.ZS": "agriculture_pct_of_gdp",
    "NV.IND.TOTL.ZS": "industry_pct_of_gdp",
    "NV.SRV.TOTL.ZS": "services_pct_of_gdp",
}

# WITS trade data lags a few years. Try the newest first.
TRADE_YEARS = [2023, 2022, 2021]
# Common names the World Bank spells differently
ALIASES = {
    "south korea": "KOR", "korea": "KOR", "north korea": "PRK", "russia": "RUS",
    "turkey": "TUR", "iran": "IRN", "egypt": "EGY", "vietnam": "VNM", "usa": "USA",
    "us": "USA", "america": "USA", "uk": "GBR", "britain": "GBR", "great britain": "GBR",
    "england": "GBR", "venezuela": "VEN", "syria": "SYR", "laos": "LAO", "yemen": "YEM",
    "slovakia": "SVK", "kyrgyzstan": "KGZ", "congo": "COG", "drc": "COD",
    "democratic republic of the congo": "COD", "ivory coast": "CIV", "czech republic": "CZE",
}

_countries: dict[str, str] = {}  # ISO3 -> World Bank name, loaded once


def _find_country(name: str) -> tuple[str, str] | None:
    """Turn a country name or code into (ISO3 code, World Bank name)."""
    if not _countries:
        rows = requests.get(
            f"{WORLD_BANK_URL}/country", params={"format": "json", "per_page": 400}, timeout=15
        ).json()[1]
        # Region "NA" marks aggregates like "World" or "Euro area"
        _countries.update({r["id"]: r["name"] for r in rows if r["region"]["id"] != "NA"})

    key = name.lower().strip()
    if key in ALIASES:
        return ALIASES[key], _countries[ALIASES[key]]
    if key.upper() in _countries:
        return key.upper(), _countries[key.upper()]
    for iso3, wb_name in _countries.items():
        if wb_name.lower() == key or wb_name.lower().startswith(key + ","):
            return iso3, wb_name
    return None


def _not_found(name: str) -> str:
    return json.dumps({
        "error": f"Country '{name}' was not found. Use the common English name (e.g. "
                 f"'South Korea', 'Germany') or a 3-letter code (e.g. 'KOR')."
    })


def _wits(reporter: str, partner: str, product: str, indicators: str) -> tuple[int, list[dict]] | None:
    """Fetch WITS trade values for the newest year that has data.

    Returns (year, rows) where each row is {partner, product, indicator, usd_billions}.
    """
    for year in TRADE_YEARS:
        resp = requests.get(
            f"{WITS_URL}/reporter/{reporter.lower()}/year/{year}/partner/{partner}"
            f"/product/{product}/indicator/{indicators}",
            params={"format": "JSON"},
            timeout=30,
        )
        if resp.status_code != 200:
            continue
        data = resp.json()
        # SDMX JSON: series keys like "0:0:3:1:0" index into each dimension's value list
        dims = [d["values"] for d in data["structure"]["dimensions"]["series"]]
        rows = []
        for key, series in data["dataSets"][0]["series"].items():
            _, _, p, prod, ind = (dims[i][int(k)] for i, k in enumerate(key.split(":")))
            value = series["observations"]["0"][0]
            if value is not None:
                rows.append({
                    "partner": p["id"], "partner_name": p["name"],
                    "product_id": prod["id"], "product": prod["name"],
                    "indicator": ind["id"],
                    "usd_billions": round(value / 1e6, 2),  # WITS reports US$ thousands
                })
        return year, rows
    return None


def get_economy_profile(country: str) -> str:
    """Get a country's GDP, growth, trade totals and the size of each economic sector."""
    try:
        found = _find_country(country)
        if not found:
            return _not_found(country)
        iso3, name = found

        profile = {"country": name, "iso3": iso3}
        for code, label in ECONOMY_INDICATORS.items():
            rows = requests.get(
                f"{WORLD_BANK_URL}/country/{iso3}/indicator/{code}",
                params={"format": "json", "mrnev": 1},  # most recent non-empty value
                timeout=15,
            ).json()
            if len(rows) > 1 and rows[1]:
                value, year = rows[1][0]["value"], rows[1][0]["date"]
                if label.endswith("_billions"):
                    value = value / 1e9
                profile[label] = {"value": round(value, 2), "year": year}
            else:
                profile[label] = "no data"
    except requests.RequestException as e:
        # The model cannot see an exception. Return something it can reason about.
        return json.dumps({"error": f"World Bank service failed: {e}. Ask the user to try again shortly."})
    return json.dumps(profile)


def get_trade_partners(country: str, top_n: int = 5) -> str:
    """Get a country's top export destinations, import sources and export product groups."""
    top_n = max(1, min(top_n, 15))
    try:
        found = _find_country(country)
        if not found:
            return _not_found(country)
        iso3, name = found

        by_partner = _wits(iso3, "all", "Total", "XPRT-TRD-VL;MPRT-TRD-VL")
        by_product = _wits(iso3, "wld", "all", "XPRT-TRD-VL")
    except requests.RequestException as e:
        return json.dumps({"error": f"WITS trade service failed: {e}. Ask the user to try again shortly."})
    if not by_partner:
        return json.dumps({
            "error": f"No recent trade data for {name} (checked {TRADE_YEARS}). "
                     f"Use get_economy_profile for total exports and imports instead."
        })

    year, rows = by_partner
    rows = [r for r in rows if r["partner"] in _countries]  # drops regions and "World"

    def top(indicator):
        picked = sorted((r for r in rows if r["indicator"] == indicator), key=lambda r: -r["usd_billions"])
        return [{"country": r["partner_name"], "usd_billions": r["usd_billions"]} for r in picked[:top_n]]

    result = {
        "country": name,
        "year": year,
        "top_export_destinations": top("XPRT-TRD-VL"),
        "top_import_sources": top("MPRT-TRD-VL"),
    }
    if by_product:
        # Only the HS product sections ("84-85_MachElec"), not overlapping groupings like "manuf"
        sections = sorted(
            (r for r in by_product[1] if "_" in r["product_id"]), key=lambda r: -r["usd_billions"]
        )
        result["top_export_products"] = [
            {"product": r["product"], "usd_billions": r["usd_billions"]} for r in sections[:top_n]
        ]
    return json.dumps(result)


def _wikidata_relations(iso3_a: str, iso3_b: str) -> dict:
    """Diplomatic relations flag and shared memberships from Wikidata."""
    query = f"""
    SELECT ?aLabel ?bLabel ?rel ?orgLabel WHERE {{
      ?a wdt:P298 "{iso3_a}". ?b wdt:P298 "{iso3_b}".
      OPTIONAL {{ {{ ?a wdt:P530 ?b }} UNION {{ ?b wdt:P530 ?a }} BIND(true AS ?rel) }}
      OPTIONAL {{ ?a wdt:P463 ?org. ?b wdt:P463 ?org. }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
    }}"""
    rows = requests.get(
        WIKIDATA_URL,
        params={"query": query, "format": "json"},
        headers=HEADERS,
        timeout=30,
    ).json()["results"]["bindings"]
    if not rows:
        return {}
    return {
        "name_a": rows[0]["aLabel"]["value"],
        "name_b": rows[0]["bLabel"]["value"],
        # Wikidata doesn't list every pair, so a missing entry is "unknown", not "no"
        "formal_diplomatic_relations": (
            True if any("rel" in r for r in rows)
            else "unknown: not recorded in Wikidata. Don't conclude there are no relations."
        ),
        "shared_organizations": sorted({r["orgLabel"]["value"] for r in rows if "orgLabel" in r}),
    }


def _wikipedia_relations_summary(names_a: list[str], names_b: list[str]) -> dict:
    """Find the '<A>–<B> relations' article and return its summary.

    Each country gets several spellings ("China", "People's Republic of China"), since
    article titles use the common name.
    """
    hits = requests.get(
        WIKIPEDIA_API_URL,
        params={"action": "query", "list": "search", "srsearch": f"{names_a[0]} {names_b[0]} relations",
                "srlimit": 3, "format": "json"},
        headers=HEADERS,
        timeout=15,
    ).json()["query"]["search"]
    for hit in hits:
        title = hit["title"]
        if (
            "relations" in title.lower()
            and any(n in title for n in names_a)
            and any(n in title for n in names_b)
        ):
            summary = requests.get(WIKIPEDIA_SUMMARY_URL + title.replace(" ", "_"), headers=HEADERS, timeout=15).json()
            return {"article": title, "summary": summary.get("extract", "")}
    return {}


def get_bilateral_relations(country_a: str, country_b: str) -> str:
    """Summarize the relationship between two countries: diplomacy, shared memberships and trade."""
    try:
        found_a, found_b = _find_country(country_a), _find_country(country_b)
    except requests.RequestException as e:
        return json.dumps({"error": f"Country lookup failed: {e}. Ask the user to try again shortly."})
    if not found_a:
        return _not_found(country_a)
    if not found_b:
        return _not_found(country_b)
    (iso_a, wb_a), (iso_b, wb_b) = found_a, found_b
    if iso_a == iso_b:
        return json.dumps({"error": "Both countries are the same. Ask the user for a second country."})

    result = {"country_a": wb_a, "country_b": wb_b}

    # Each source fails on its own, so one outage doesn't hide the rest.
    try:
        result.update(_wikidata_relations(iso_a, iso_b))
    except requests.RequestException:
        result["diplomacy_note"] = "Wikidata was unavailable; diplomatic data is missing."

    try:
        trade = _wits(iso_a, iso_b.lower(), "Total", "XPRT-TRD-VL;MPRT-TRD-VL")
        if trade:
            year, rows = trade
            values = {r["indicator"]: r["usd_billions"] for r in rows}
            result["trade"] = {
                "year": year,
                "a_exports_to_b_usd_billions": values.get("XPRT-TRD-VL"),
                "a_imports_from_b_usd_billions": values.get("MPRT-TRD-VL"),
            }
        else:
            result["trade_note"] = f"No recent bilateral trade data reported by {wb_a}."
    except requests.RequestException:
        result["trade_note"] = "WITS trade service was unavailable."

    try:
        article = _wikipedia_relations_summary(
            [wb_a, result.get("name_a", wb_a)], [wb_b, result.get("name_b", wb_b)]
        )
        result.update(article or {"article_note": "No Wikipedia article on this relationship was found."})
    except requests.RequestException:
        result["article_note"] = "Wikipedia was unavailable."

    return json.dumps(result)


# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_economy_profile",
            "description": (
                "Get a country's economic profile from the World Bank: GDP, GDP growth, GDP per "
                "capita, population, total exports and imports, and the share of GDP from "
                "agriculture, industry and services. Each value comes with its year."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "country": {"type": "string", "description": "Country name or 3-letter code, e.g. 'Brazil' or 'BRA'"},
                },
                "required": ["country"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trade_partners",
            "description": (
                "Get a country's top export destinations, top import sources, and top export "
                "product groups (its major export industries) by value, for the latest year available."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "country": {"type": "string", "description": "Country name or 3-letter code"},
                    "top_n": {"type": "integer", "description": "How many partners/products to list, 1-15. Default 5."},
                },
                "required": ["country"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bilateral_relations",
            "description": (
                "Describe the relationship between two countries: whether they have formal "
                "diplomatic relations, international organizations they both belong to, how much "
                "they trade with each other, and a summary of the Wikipedia article on their relations."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "country_a": {"type": "string", "description": "First country, e.g. 'Japan'"},
                    "country_b": {"type": "string", "description": "Second country, e.g. 'South Korea'"},
                },
                "required": ["country_a", "country_b"],
            },
        },
    },
    *POLICY_TOOLS,
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_economy_profile": get_economy_profile,
    "get_trade_partners": get_trade_partners,
    "get_bilateral_relations": get_bilateral_relations,
    **POLICY_TOOL_MAP,
}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
