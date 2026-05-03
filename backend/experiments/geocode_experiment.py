import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

import requests


@dataclass(frozen=True)
class GeocodeCase:
    label: str
    query: str
    bbox: Optional[str] = None
    proximity: Optional[str] = None
    country: str = "jp"
    language: str = "ja"
    limit: int = 10


def fetch_mapbox_geocode(*, token: str, case: GeocodeCase) -> dict:
    encoded = quote(case.query, safe="")
    url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{encoded}.json"
    params: dict = {
        "access_token": token,
        "limit": case.limit,
        "country": case.country,
        "language": case.language,
    }
    if case.bbox:
        params["bbox"] = case.bbox
    if case.proximity:
        params["proximity"] = case.proximity
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


def main() -> int:
    token = os.getenv("MAPBOX_ACCESS_TOKEN")
    if not token:
        print("MAPBOX_ACCESS_TOKEN が未設定です（backend/.env を読み込むなら `set -a; source backend/.env; set +a` してから実行してください）")
        return 1

    # 習志野市（ログに出ている値）
    narashino_center = (140.01894, 35.669388)
    bbox = "139.96893999999998,35.619388,140.06894,35.719387999999995"
    proximity = f"{narashino_center[0]},{narashino_center[1]}"

    cases = [
        GeocodeCase(label="raw", query="青葉幼稚園", bbox=bbox, proximity=proximity),
        GeocodeCase(label="with_area_city", query="青葉幼稚園 習志野市", bbox=bbox, proximity=proximity),
        GeocodeCase(label="with_area_short", query="青葉幼稚園 習志野", bbox=bbox, proximity=proximity),
        GeocodeCase(label="with_tsudanuma", query="青葉幼稚園 津田沼", bbox=bbox, proximity=proximity),
        GeocodeCase(label="with_kindergarten", query="青葉幼稚園 幼稚園", bbox=bbox, proximity=proximity),
        GeocodeCase(label="with_area_and_kindergarten", query="青葉幼稚園 幼稚園 習志野市", bbox=bbox, proximity=proximity),
        GeocodeCase(label="no_bbox_raw", query="青葉幼稚園", proximity=proximity),
        GeocodeCase(label="no_bbox_with_area", query="青葉幼稚園 習志野市", proximity=proximity),
    ]

    for case in cases:
        print(f"\n=== {case.label}: {case.query} ===")
        try:
            data = fetch_mapbox_geocode(token=token, case=case)
        except Exception as e:
            print(f"request_failed: {e}")
            continue

        features = data.get("features")
        if not isinstance(features, list):
            print(f"unexpected_response: {data}")
            continue

        if not features:
            print("features: []")
            continue

        for idx, feat in enumerate(features[:5], start=1):
            center = feat.get("center")
            place_name = feat.get("place_name")
            relevance = feat.get("relevance")
            types = feat.get("place_type")
            print(f"{idx}. {place_name} center={center} relevance={relevance} types={types}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

