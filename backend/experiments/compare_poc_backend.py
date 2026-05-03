import time
from concurrent.futures import ThreadPoolExecutor
from typing import List, Tuple, Dict, Optional

import numpy as np
import requests


OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/foot/"


FACILITIES: List[Dict] = [
    # Google Places（New）で取得できた座標（backend/backend.log 2026-05-03 16:40:20-21）
    {"name": "津田沼駅", "coord": (140.0204415, 35.6912248), "dist_m": 950},
    {"name": "京成津田沼駅", "coord": (140.02478, 35.6835671), "dist_m": 650},
    {"name": "青葉幼稚園", "coord": (140.0280861, 35.6854898), "dist_m": 350},
]


def get_osrm_distance(start_coord: Tuple[float, float], end_coord: Tuple[float, float], timeout_s: float) -> float:
    url = f"{OSRM_BASE_URL}{start_coord[0]},{start_coord[1]};{end_coord[0]},{end_coord[1]}?overview=false"
    try:
        response = requests.get(url, timeout=timeout_s)
        data = response.json()
        if data.get("code") == "Ok":
            return float(data["routes"][0]["distance"])
    except Exception:
        pass
    return float("inf")


def evaluate_point(
    point: Tuple[float, float],
    *,
    road_factor: float,
    timeout_s: float,
) -> float:
    total_error = 0.0
    for fac in FACILITIES:
        osrm_dist = get_osrm_distance(point, fac["coord"], timeout_s=timeout_s)
        total_error += abs(osrm_dist - (fac["dist_m"] * road_factor))
    return total_error


def generate_grid(center_lon: float, center_lat: float, radius_m: float, step_m: float) -> List[Tuple[float, float]]:
    degree_lat_m = 111320.0
    degree_lon_m = 111320.0 * np.cos(np.radians(center_lat))
    lat_step = step_m / degree_lat_m
    lon_step = step_m / degree_lon_m
    num_steps = int(radius_m / step_m)

    lats = np.linspace(center_lat - lat_step * num_steps, center_lat + lat_step * num_steps, num_steps * 2 + 1)
    lons = np.linspace(center_lon - lon_step * num_steps, center_lon + lon_step * num_steps, num_steps * 2 + 1)
    return [(float(lon), float(lat)) for lat in lats for lon in lons]


def scan(
    grid_points: List[Tuple[float, float]],
    *,
    road_factor: float,
    timeout_s: float,
    max_workers: int,
) -> Tuple[Optional[Tuple[float, float]], float]:
    best_point = None
    min_error = float("inf")
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        errors = list(executor.map(lambda p: evaluate_point(p, road_factor=road_factor, timeout_s=timeout_s), grid_points))
    for point, error in zip(grid_points, errors):
        if error < min_error:
            min_error = error
            best_point = point
    return best_point, min_error


def run_poc() -> dict:
    center_lon = sum(f["coord"][0] for f in FACILITIES) / len(FACILITIES)
    center_lat = sum(f["coord"][1] for f in FACILITIES) / len(FACILITIES)

    t0 = time.perf_counter()
    grid_coarse = generate_grid(center_lon, center_lat, radius_m=800, step_m=100)
    best_coarse, error_coarse = scan(grid_coarse, road_factor=1.0, timeout_s=5.0, max_workers=10)
    grid_fine = generate_grid(best_coarse[0], best_coarse[1], radius_m=150, step_m=25)
    best_fine, error_fine = scan(grid_fine, road_factor=1.0, timeout_s=5.0, max_workers=10)
    t1 = time.perf_counter()
    return {
        "label": "PoC",
        "best": best_fine,
        "avg_error_m": error_fine / len(FACILITIES),
        "points": len(grid_coarse) + len(grid_fine),
        "elapsed_s": t1 - t0,
    }


def run_backend_like() -> dict:
    center_lon = float(np.mean([f["coord"][0] for f in FACILITIES]))
    center_lat = float(np.mean([f["coord"][1] for f in FACILITIES]))

    t0 = time.perf_counter()
    grid_coarse = generate_grid(center_lon, center_lat, radius_m=1000, step_m=100)
    best_coarse, error_coarse = scan(grid_coarse, road_factor=1.1, timeout_s=3.0, max_workers=15)
    grid_fine = generate_grid(best_coarse[0], best_coarse[1], radius_m=200, step_m=20)
    best_fine, error_fine = scan(grid_fine, road_factor=1.1, timeout_s=3.0, max_workers=15)
    t1 = time.perf_counter()
    return {
        "label": "BackendLike",
        "best": best_fine,
        "avg_error_m": error_fine / len(FACILITIES),
        "points": len(grid_coarse) + len(grid_fine),
        "elapsed_s": t1 - t0,
    }


def run_backend_api() -> Optional[dict]:
    url = "http://localhost:8000/estimate"
    payload = {
        "facilities": [
            {"name": f["name"], "dist_m": f["dist_m"], "coord": [f["coord"][0], f["coord"][1]]} for f in FACILITIES
        ]
    }
    try:
        t0 = time.perf_counter()
        r = requests.post(url, json=payload, timeout=120)
        t1 = time.perf_counter()
        data = r.json()
        return {
            "label": "BackendAPI",
            "status": r.status_code,
            "coord": data.get("coord"),
            "avg_error_m": data.get("avg_error"),
            "elapsed_s": t1 - t0,
        }
    except Exception:
        return None


def main() -> int:
    results = [run_poc(), run_backend_like()]
    api = run_backend_api()
    if api:
        results.append(api)

    for r in results:
        if r["label"] == "BackendAPI":
            print(
                f'{r["label"]}: status={r["status"]} coord={r["coord"]} avg_error={r["avg_error_m"]} elapsed={r["elapsed_s"]:.2f}s'
            )
        else:
            print(
                f'{r["label"]}: best={r["best"]} avg_error={r["avg_error_m"]:.2f}m points={r["points"]} elapsed={r["elapsed_s"]:.2f}s'
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

