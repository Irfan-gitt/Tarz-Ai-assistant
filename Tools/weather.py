import requests
import os
from dotenv import load_dotenv

load_dotenv()

OWM_KEY = os.getenv("OPENWEATHER_KEY")


def get_weather(city: str) -> str:
    try:
        url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={OWM_KEY}&units=metric"
        data = requests.get(url, timeout=5).json()

        if data.get("cod") != 200:
            return f"Could not find weather for {city}."

        temp = data["main"]["temp"]
        feels_like = data["main"]["feels_like"]
        humidity = data["main"]["humidity"]
        description = data["weather"][0]["description"].title()
        wind = data["wind"]["speed"]
        city_name = data["name"]
        country = data["sys"]["country"]

        return (
            f"Weather in {city_name}, {country}: "
            f"{description}, {temp}°C, feels like {feels_like}°C. "
            f"Humidity {humidity}%, wind {wind} m/s."
        )

    except Exception as e:
        return f"Weather error: {e}"
