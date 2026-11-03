import argparse
import logging
from datetime import datetime, timezone

import requests

from .config import TARGET_LOCATIONS
from .db import get_connection, get_or_create_city
from .geocoding import get_coordinates

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

WEATHER_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

WEATHER_VARS = ["temperature_2m", "relative_humidity_2m", "wind_speed_10m", "weather_code"]
AIR_VARS = ["pm10", "pm2_5", "carbon_monoxide"]


def fetch_hourly(url: str, lat: float, lon: float, variables: list[str], days: int) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": ",".join(variables),
        "past_days": days,
        "forecast_days": 1,
    }
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()["hourly"]


def past_rows(hourly: dict, variables: list[str]) -> list[tuple]:
    """Return (time, v1, v2, ...) tuples for past hours with no missing values."""
    now = datetime.now(tz=timezone.utc).replace(tzinfo=None)
    rows = []
    for i, ts in enumerate(hourly["time"]):
        if datetime.fromisoformat(ts) > now:
            continue
        values = [hourly[v][i] for v in variables]
        if any(v is None for v in values):
            continue
        rows.append((ts, *values))
    return rows


def insert_weather(conn, city_id: int, rows: list[tuple]) -> None:
    sql = """
        INSERT INTO weather_data (city_id, recorded_at, temperature, relative_humidity, wind_speed, weather_code)
        SELECT %s, %s, %s, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM weather_data WHERE city_id = %s AND recorded_at = %s
        )"""
    data = [(city_id, ts, t, h, w, int(code), city_id, ts) for ts, t, h, w, code in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, data)
    conn.commit()


def insert_air(conn, city_id: int, rows: list[tuple]) -> None:
    sql = """
        INSERT INTO air_quality (city_id, recorded_at, pm10, pm2_5, carbon_monoxide)
        SELECT %s, %s, %s, %s, %s
        WHERE NOT EXISTS (
            SELECT 1 FROM air_quality WHERE city_id = %s AND recorded_at = %s
        )"""
    data = [(city_id, ts, p10, p25, co, city_id, ts) for ts, p10, p25, co in rows]
    with conn.cursor() as cur:
        cur.executemany(sql, data)
    conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=60)
    days = min(parser.parse_args().days, 92)

    conn = get_connection()
    try:
        for loc in TARGET_LOCATIONS:
            coords = get_coordinates(loc["city"], loc["region"], loc["country"])
            if not coords:
                continue
            city_id = get_or_create_city(
                conn, loc["city"], coords["lat"], coords["lon"], loc["country"], loc["region"]
            )

            weather = past_rows(fetch_hourly(WEATHER_URL, coords["lat"], coords["lon"], WEATHER_VARS, days), WEATHER_VARS)
            insert_weather(conn, city_id, weather)
            logger.info("%s: %d weather rows processed", loc["city"], len(weather))

            air = past_rows(fetch_hourly(AIR_URL, coords["lat"], coords["lon"], AIR_VARS, days), AIR_VARS)
            insert_air(conn, city_id, air)
            logger.info("%s: %d air quality rows processed", loc["city"], len(air))
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()