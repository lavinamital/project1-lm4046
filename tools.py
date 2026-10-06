"""The tools the harness can run, and the JSON that describes them to the model."""

import json
import math

import requests

# Open-Meteo and Frankfurter are free and need no API key.
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
CURRENCY_URL = "https://api.frankfurter.app/latest"

MAX_FORECAST_DAYS = 16  # Open-Meteo's limit
MAX_TRIP_DAYS = 30

# Activity -> extra items. Shared with the tool description so the model knows the options.
ACTIVITY_ITEMS = {
    "beach": ["swimsuit", "sandals", "beach towel"],
    "hiking": ["hiking shoes", "reusable water bottle", "small backpack"],
    "business": ["formal outfit", "laptop and charger", "dress shoes"],
    "nightlife": ["going-out outfit", "comfortable dressy shoes"],
    "gym": ["workout clothes", "sneakers"],
    "snow": ["snow boots", "thermal base layers", "ski gloves"],
}


def get_forecast(location: str, days: int = 3) -> str:
    """Get the daily weather forecast for a city."""
    if not 1 <= days <= MAX_FORECAST_DAYS:
        return json.dumps({
            "error": f"days must be between 1 and {MAX_FORECAST_DAYS}. "
                     f"For longer trips, ask for {MAX_FORECAST_DAYS} days and say the rest is unknown."
        })
    try:
        places = requests.get(GEOCODE_URL, params={"name": location, "count": 1}, timeout=10).json()
        if not places.get("results"):
            return json.dumps({
                "error": f"City '{location}' was not found. Try just the city name, e.g. 'Paris' "
                         f"instead of 'Paris, France'."
            })
        place = places["results"][0]

        daily = requests.get(
            FORECAST_URL,
            params={
                "latitude": place["latitude"],
                "longitude": place["longitude"],
                "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                "temperature_unit": "fahrenheit",
                "forecast_days": days,
                "timezone": "auto",
            },
            timeout=10,
        ).json()["daily"]
    except requests.RequestException as e:
        # The model cannot see an exception. Return something it can reason about.
        return json.dumps({"error": f"Weather service failed: {e}. Ask the user to try again shortly."})

    forecast = [
        {"date": date, "high_f": high, "low_f": low, "rain_chance": rain}
        for date, high, low, rain in zip(
            daily["time"],
            daily["temperature_2m_max"],
            daily["temperature_2m_min"],
            daily["precipitation_probability_max"],
        )
    ]
    return json.dumps({
        "location": f"{place['name']}, {place.get('country', '')}".strip(", "),
        "days": forecast,
        # Pre-computed so the model can pass these straight into build_packing_list
        "summary": {
            "lowest_f": min(d["low_f"] for d in forecast),
            "highest_f": max(d["high_f"] for d in forecast),
            "max_rain_chance": max(d["rain_chance"] or 0 for d in forecast),
        },
    })


def convert_currency(amount: float, from_currency: str, to_currency: str) -> str:
    """Convert money between currencies at today's exchange rate."""
    from_currency, to_currency = from_currency.upper(), to_currency.upper()
    if from_currency == to_currency:
        return json.dumps({"amount": amount, "currency": to_currency, "note": "Same currency, no conversion."})
    try:
        resp = requests.get(
            CURRENCY_URL,
            params={"amount": amount, "from": from_currency, "to": to_currency},
            timeout=10,
        )
        if resp.status_code == 404:
            return json.dumps({
                "error": f"Unknown currency '{from_currency}' or '{to_currency}'. "
                         f"Use 3-letter ISO codes like USD, EUR, JPY, GBP, MXN."
            })
        data = resp.json()
    except requests.RequestException as e:
        return json.dumps({"error": f"Currency service failed: {e}. Ask the user to try again shortly."})

    return json.dumps({
        "amount": amount,
        "from": from_currency,
        "to": to_currency,
        "converted": data["rates"][to_currency],
        "rate_date": data["date"],
    })


def build_packing_list(
    days: int,
    lowest_f: float,
    highest_f: float,
    max_rain_chance: float = 0,
    activities: list[str] | None = None,
) -> str:
    """Build a packing list from trip length, weather, and planned activities."""
    if not 1 <= days <= MAX_TRIP_DAYS:
        return json.dumps({"error": f"days must be between 1 and {MAX_TRIP_DAYS}."})
    if lowest_f > highest_f:
        return json.dumps({"error": "lowest_f is higher than highest_f. Check the forecast summary and swap them."})

    clothes = [
        f"{days} tops",
        f"{max(1, math.ceil(days / 3))} pants/bottoms",
        f"{days + 1} pairs of underwear",
        f"{days + 1} pairs of socks",
        "pajamas",
        "comfortable walking shoes",
    ]
    weather = []
    if lowest_f < 45:
        weather += ["warm winter coat", "hat", "gloves", "scarf"]
    elif lowest_f < 60:
        weather += ["light jacket or sweater"]
    if highest_f > 80:
        weather += ["shorts", "sunscreen", "sunglasses", "sun hat"]
    if max_rain_chance >= 40:
        weather += ["umbrella", "waterproof jacket"]

    extras, unknown = [], []
    for activity in activities or []:
        key = activity.lower().strip()
        if key in ACTIVITY_ITEMS:
            extras += ACTIVITY_ITEMS[key]
        else:
            unknown.append(activity)

    result = {
        "clothes": clothes,
        "weather_gear": weather,
        "activity_gear": extras,
        "essentials": ["phone charger", "toothbrush and toiletries", "ID/passport", "medications"],
    }
    if unknown:
        result["note"] = (
            f"No preset items for {unknown}. Supported activities: {list(ACTIVITY_ITEMS)}. "
            f"Suggest items for the others yourself."
        )
    return json.dumps(result)


# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_forecast",
            "description": (
                "Get the daily forecast (high, low, rain chance) for a city, starting today. "
                "Returns a summary with lowest_f, highest_f and max_rain_chance for build_packing_list."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, e.g. 'Tokyo'"},
                    "days": {
                        "type": "integer",
                        "description": f"Number of days to forecast, 1-{MAX_FORECAST_DAYS}. Use the trip length.",
                    },
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "convert_currency",
            "description": "Convert an amount of money between two currencies at today's exchange rate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount to convert, e.g. 200"},
                    "from_currency": {"type": "string", "description": "3-letter ISO code, e.g. 'USD'"},
                    "to_currency": {"type": "string", "description": "3-letter ISO code, e.g. 'JPY'"},
                },
                "required": ["amount", "from_currency", "to_currency"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "build_packing_list",
            "description": (
                "Build a packing list for a trip. Call get_forecast first and pass its summary values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": f"Trip length in days, 1-{MAX_TRIP_DAYS}"},
                    "lowest_f": {"type": "number", "description": "Lowest temperature of the trip, °F"},
                    "highest_f": {"type": "number", "description": "Highest temperature of the trip, °F"},
                    "max_rain_chance": {"type": "number", "description": "Highest daily rain chance, 0-100"},
                    "activities": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": f"Planned activities. Supported: {list(ACTIVITY_ITEMS)}",
                    },
                },
                "required": ["days", "lowest_f", "highest_f"],
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_forecast": get_forecast,
    "convert_currency": convert_currency,
    "build_packing_list": build_packing_list,
}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
