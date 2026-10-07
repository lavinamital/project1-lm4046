# Nation Brief

A chat agent with two jobs:

- **World Brief:** explain a country's economy, its major industries and trade partners, and how
  two countries relate, from live World Bank, WITS (World Bank trade), Wikidata and Wikipedia data.
- **US Policy Lab:** take a proposed US tax, tariff or payment and estimate what the government
  gains or spends each year, what each household income group pays, and what could follow.

Built on the `gemini-web-tool-calling` starter: FastAPI + LiteLLM + Gemini
(`vertex_ai/gemini-3.5-flash-lite`), with a per-session conversation store and the tool calls
shown in the UI. Policy estimates also render as a bar chart of cost per household.

## Tools

World tools (`tools.py`):

| Tool | What it does | Data |
| --- | --- | --- |
| `get_economy_profile(country)` | GDP, growth, GDP per capita, population, exports, imports, and the share of GDP from agriculture, industry and services | [World Bank API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392) |
| `get_trade_partners(country, top_n)` | Top export destinations, import sources and export product groups | [WITS](https://wits.worldbank.org) |
| `get_bilateral_relations(country_a, country_b)` | Formal diplomatic relations, shared international organizations, trade between the two, and Wikipedia's summary of their relations | Wikidata, WITS, Wikipedia |

US policy tools (`policy_tools.py`):

| Tool | What it does | Data |
| --- | --- | --- |
| `get_us_household_profile(state)` | Households, median and mean income, Gini, poverty rate and income distribution, for the US or one state | [Census ACS](https://www.census.gov/data/developers/data-sets/acs-1year.html) |
| `get_federal_revenue(fiscal_year)` | Federal revenue by source: income, corporate, payroll, excise, estate, tariffs | [Treasury Fiscal Data](https://fiscaldata.treasury.gov/api-documentation/) |
| `estimate_policy_impact(policy_type, ...)` | Yearly revenue change, its share of federal revenue, and average cost per household by income group | Census + Treasury + WITS |

`estimate_policy_impact` supports four policy types: `income_tax` (rate change above an income
threshold), `consumption_tax` (national sales tax/VAT), `tariff` (on all goods imports) and
`household_payment` (a per-household payment or credit, optionally means-tested). It uses the
Census household income distribution, spreads incomes evenly within brackets and models the
$200k+ bracket as a Pareto tail. It is a **static** estimate: it assumes no change in behavior or
prices and returns its assumptions so the model can state them.

`get_bilateral_relations` and `estimate_policy_impact` are the original tools. Every tool returns
errors as JSON that tell the model what to do next, and data sources fail independently.

## Sample queries

1. `What are Brazil's major industries, and who does it trade with most?` then
   `How does it get along with Argentina?` (memory: "it" is Brazil).
2. `What if the US put a 10% tariff on all imports? Who pays?`
3. `Raise income taxes 3 points on income over $200k. What's the gain vs. the cost to people?`
   then `What about just for households in Mississippi?`

## Setup

The Census API needs a free key: https://api.census.gov/data/key_signup.html. Set it as the
`CENSUS_API_KEY` environment variable locally and on the Cloud Run service. Without it, the
policy tools tell the model that Census data is unavailable; everything else still works.

1. `gcloud auth application-default login`
2. `CENSUS_API_KEY=your-key uv run app.py`, then open http://localhost:8000

## Deploy

Deployed on Google Cloud Run with continuous deploy from GitHub. The app listens on `$PORT`.
