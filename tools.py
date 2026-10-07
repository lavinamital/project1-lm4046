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


# Rough per-item weights in pounds, matching the items build_packing_list returns.
ITEM_WEIGHTS_LB = {
    "tops": 0.4, "pants/bottoms": 1.2, "underwear": 0.1, "socks": 0.1, "pajamas": 0.6,
    "comfortable walking shoes": 2.0, "warm winter coat": 3.5, "hat": 0.2, "gloves": 0.2,
    "scarf": 0.3, "light jacket or sweater": 1.2, "shorts": 0.5, "sunscreen": 0.5,
    "sunglasses": 0.1, "sun hat": 0.3, "umbrella": 0.8, "waterproof jacket": 1.0,
    "swimsuit": 0.3, "sandals": 1.0, "beach towel": 1.5, "hiking shoes": 2.5,
    "reusable water bottle": 0.5, "small backpack": 1.0, "formal outfit": 2.0,
    "laptop and charger": 4.5, "dress shoes": 2.5, "going-out outfit": 1.0,
    "comfortable dressy shoes": 2.0, "workout clothes": 0.7, "sneakers": 2.0,
    "snow boots": 4.0, "thermal base layers": 1.0, "ski gloves": 0.5, "phone charger": 0.2,
    "toothbrush and toiletries": 2.0, "id/passport": 0.1, "medications": 0.3,
}
UNKNOWN_ITEM_LB = 1.0
EMPTY_BAG_LB = 7.0
KG_PER_LB = 0.4536

# Carry-on weight limits in kg. None = the airline has no carry-on weight limit.
AIRLINE_CARRY_ON_KG = {
    "united": None, "delta": None, "american": None, "jetblue": None, "southwest": None,
    "british airways": 23, "easyjet": 15, "air france": 12, "klm": 12, "lufthansa": 8,
    "ryanair": 10, "ana": 10, "jal": 10, "emirates": 7, "qantas": 7, "singapore airlines": 7,
}


def check_carry_on(items: list[str], airline: str) -> str:
    """Estimate a packing list's weight and check it against an airline's carry-on limit."""
    key = airline.lower().strip()
    if key not in AIRLINE_CARRY_ON_KG:
        return json.dumps({
            "error": f"No carry-on limit on file for '{airline}'. "
                     f"Known airlines: {list(AIRLINE_CARRY_ON_KG)}. Ask the user which one they fly."
        })

    weighed, unknown = [], []
    for item in items:
        # "4 tops" -> 4 x "tops", "5 pairs of socks" -> 5 x "socks"
        count, _, name = item.partition(" ")
        if not count.isdigit():
            count, name = "1", item
        name = name.lower().removeprefix("pairs of ").strip()
        if name not in ITEM_WEIGHTS_LB:
            unknown.append(item)
        weighed.append((item, int(count) * ITEM_WEIGHTS_LB.get(name, UNKNOWN_ITEM_LB)))

    total_lb = EMPTY_BAG_LB + sum(lb for _, lb in weighed)
    total_kg = round(total_lb * KG_PER_LB, 1)
    limit_kg = AIRLINE_CARRY_ON_KG[key]
    heaviest = [item for item, _ in sorted(weighed, key=lambda w: -w[1])[:3]]

    result = {
        "airline": airline,
        "estimated_kg": total_kg,
        "estimated_lb": round(total_lb, 1),
        "limit_kg": limit_kg,
        "fits": limit_kg is None or total_kg <= limit_kg,
        "heaviest_items": heaviest,
        "tip": "Wear the heaviest shoes and jacket on the plane to save weight.",
        "note": "Estimate includes a 7 lb empty bag. Airlines change limits; confirm on their site.",
    }
    if limit_kg is not None and total_kg > limit_kg:
        result["over_by_kg"] = round(total_kg - limit_kg, 1)
    if unknown:
        result["guessed_weights_for"] = unknown
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
    {
        "type": "function",
        "function": {
            "name": "check_carry_on",
            "description": (
                "Estimate the weight of a packing list and check it against an airline's carry-on "
                "weight limit. Pass the items exactly as build_packing_list returned them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Every item from the packing list, e.g. ['4 tops', 'umbrella']",
                    },
                    "airline": {
                        "type": "string",
                        "description": f"Airline name. Known: {list(AIRLINE_CARRY_ON_KG)}",
                    },
                },
                "required": ["items", "airline"],
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_forecast": get_forecast,
    "convert_currency": convert_currency,
    "build_packing_list": build_packing_list,
    "check_carry_on": check_carry_on,
}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
