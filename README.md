# Pack Smart

A trip packing assistant. Tell it where you're going, for how long, and what you'll be doing.
It checks the real forecast for your destination, builds a packing list from that weather and
your activities, and converts spending money into the local currency.

Built on the `gemini-web-tool-calling` starter: FastAPI + LiteLLM + Gemini
(`vertex_ai/gemini-3.5-flash-lite`), with a per-session conversation store and the tool calls
shown in the UI.

## Tools

| Tool | What it does | Data |
| --- | --- | --- |
| `get_forecast(location, days)` | Daily high, low and rain chance, plus a trip summary | [Open-Meteo](https://open-meteo.com) (external API) |
| `convert_currency(amount, from_currency, to_currency)` | Converts money at today's rate | [Frankfurter](https://frankfurter.app) (external API) |
| `build_packing_list(days, lowest_f, highest_f, max_rain_chance, activities)` | Turns trip length, weather and activities into a categorized list | Original logic in `tools.py` |

The agent usually chains them: `get_forecast` → `build_packing_list` takes the forecast's summary.
Every tool returns errors as JSON that tell the model how to fix the call (unknown city,
bad currency code, trip too long, unsupported activity).

## Sample queries

1. `I'm going to Tokyo for 5 days, mostly sightseeing and nightlife. What should I pack?`
2. `Packing for a 3-day hiking trip to Denver this weekend.` then follow up with
   `What if I add a day at the gym?`. The follow-up reuses the same trip from memory.
3. `How much is $300 in euros? I'm heading to Barcelona for a week.`

## Run locally

1. `gcloud auth application-default login`
2. `uv run app.py`, then open http://localhost:8000

## Deploy

Deployed on Google Cloud Run with continuous deploy from GitHub. The app listens on `$PORT`.
