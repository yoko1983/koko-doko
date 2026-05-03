import json
import os

import requests


def main() -> int:
    api_key = os.getenv("GOOGLE_PLACES_API_KEY")
    if not api_key:
        print("GOOGLE_PLACES_API_KEY が未設定です（例: `set -a; source backend/.env; set +a`）")
        return 1

    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.types",
    }

    # ログに出ている習志野市中心（Mapbox結果）と同じbbox
    bbox = "139.96893999999998,35.619388,140.06894,35.719387999999995"
    min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))

    queries = [
        "青葉幼稚園",
        "青葉幼稚園 習志野市",
        "青葉幼稚園 津田沼",
    ]

    for q in queries:
        print(f"\n=== {q} ===")
        body = {
            "textQuery": q,
            "languageCode": "ja",
            "regionCode": "JP",
            "locationBias": {
                "rectangle": {
                    "low": {"latitude": min_lat, "longitude": min_lon},
                    "high": {"latitude": max_lat, "longitude": max_lon},
                }
            },
        }
        r = requests.post(url, headers=headers, data=json.dumps(body), timeout=15)
        try:
            data = r.json()
        except Exception:
            data = {"_raw": r.text}

        if r.status_code >= 400:
            print(f"error {r.status_code}: {data}")
            continue

        places = data.get("places") or []
        if not places:
            print("places: []")
            continue

        for i, p in enumerate(places[:5], start=1):
            name = ((p.get("displayName") or {}).get("text")) or ""
            addr = p.get("formattedAddress") or ""
            loc = p.get("location") or {}
            print(f"{i}. {name} | {addr} | loc=({loc.get('latitude')},{loc.get('longitude')}) | types={p.get('types')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

