# Nation Brief

A chat agent that explains a country's economy and its relations with other countries.
Ask what a country's major industries are, who it trades with, or how two countries get along,
and it pulls live data from the World Bank, the World Bank's trade database (WITS), Wikidata
and Wikipedia.

Built on the `gemini-web-tool-calling` starter: FastAPI + LiteLLM + Gemini
(`vertex_ai/gemini-3.5-flash-lite`), with a per-session conversation store and the tool calls
shown in the UI.

## Tools

| Tool | What it does | Data |
| --- | --- | --- |
| `get_economy_profile(country)` | GDP, growth, GDP per capita, population, total exports and imports, and the share of GDP from agriculture, industry and services | [World Bank API](https://datahelpdesk.worldbank.org/knowledgebase/articles/889392) |
| `get_trade_partners(country, top_n)` | Top export destinations, top import sources, and top export product groups | [WITS](https://wits.worldbank.org) |
| `get_bilateral_relations(country_a, country_b)` | Whether two countries have formal diplomatic relations, the international organizations they both belong to, their trade with each other, and the summary of Wikipedia's "A–B relations" article | Wikidata, WITS, Wikipedia |

`get_bilateral_relations` is the original tool: it combines four sources into one picture of a
relationship. Each source fails independently, so one outage doesn't hide the rest.

All tools accept common names ("South Korea", "UK", "Russia") or 3-letter codes, and return
errors as JSON that tell the model how to fix the call (unknown country, same country twice,
no recent trade data). Trade data lags a few years, so every number comes with its year.

## Sample queries

1. `What are Brazil's major industries, and who does it trade with most?`
   then follow up with `How does it get along with Argentina?`. The follow-up uses memory to
   know "it" means Brazil.
2. `How would you describe relations between Japan and South Korea?`
3. `Compare the economies of Germany and India.`

## Run locally

1. `gcloud auth application-default login`
2. `uv run app.py`, then open http://localhost:8000

## Deploy

Deployed on Google Cloud Run with continuous deploy from GitHub. The app listens on `$PORT`.
