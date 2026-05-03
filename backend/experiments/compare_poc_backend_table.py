import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Dict, Optional

import numpy as np
import requests


OSRM_TABLE_URL = "http://router.project-osrm.org/table/v1/foot/"

FACILITIES: List[Dict] = [
    {"name": "津田沼駅", "coord": (140.0204415, 35.6912248), "dist_m": 950},
    {"name": "京成津田沼駅", "coord": (140.02478, 35.6835671), "dist_m": 650},
    {"name": "青葉幼稚園", "coord": (140.0280861, 35.6854898), "dist_m": 350},
]


def osrm_table_distances_m(
    source: Tuple[float, float],
    destinations: List[Tuple[float, float]],
    *,
    timeout_s: float,
) -> List[float]:
    coords = [source, *destinations]
    coord_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    url = f"{OSRM_TABLE_URL}{coord_str}"
    params = {
        "sources": "0",
        "destinations": ";".join(str(i) for i in range(1, len(coords))),
        "annotations": "distance",
    }
    try:
        r = requests.get(url, params=params, timeout=timeout_s)
        data = r.json()
        if data.get("code") != "Ok":
            return [float("inf")] * len(destinations)
        distances = (data.get("distances") or [[None]])[0][1:]
        out: List[float] = []
        for d in distances:
            out.append(float(d) if d is not None else float("inf"))
        return out
    except Exception:
        return [float("inf")] * len(destinations)


def generate_grid(center_lon: float, center_lat: float, radius_m: float, step_m: float) -> List[Tuple[float, float]]:
    degree_lat_m = 111320.0
    degree_lon_m = 111320.0 * np.cos(np.radians(center_lat))
    lat_step = step_m / degree_lat_m
    lon_step = step_m / degree_lon_m
    num_steps = int(radius_m / step_m)

    lats = np.linspace(center_lat - lat_step * num_steps, center_lat + lat_step * num_steps, num_steps * 2 + 1)
    lons = np.linspace(center_lon - lon_step * num_steps, center_lon + lon_step * num_steps, num_steps * 2 + 1)
    return [(float(lon), float(lat)) for lat in lats for lon in lons]


def evaluate_point(point: Tuple[float, float], *, road_factor: float, timeout_s: float) -> float:
    dests = [f["coord"] for f in FACILITIES]
    osrm_dists = osrm_table_distances_m(point, dests, timeout_s=timeout_s)
    total = 0.0
    for fac, osrm_dist in zip(FACILITIES, osrm_dists):
        total += abs(osrm_dist - fac["dist_m"] * road_factor)
    return total


def scan(
    grid_points: List[Tuple[float, float]],
    *,
    road_factor: float,
    timeout_s: float,
    max_workers: int,
) -> Tuple[Optional[Tuple[float, float]], float, float]:
    t0 = time.perf_counter()
    best_point = None
    min_error = float("inf")

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(evaluate_point, p, road_factor=road_factor, timeout_s=timeout_s): p for p in grid_points}
        done = 0
        for fut in as_completed(futures):
            p = futures[fut]
            e = fut.result()
            done += 1
            if e < min_error:
                min_error = e
                best_point = p
    t1 = time.perf_counter()
    return best_point, min_error, t1 - t0


def run_poc() -> dict:
    center_lon = sum(f["coord"][0] for f in FACILITIES) / len(FACILITIES)
    center_lat = sum(f["coord"][1] for f in FACILITIES) / len(FACILITIES)

    grid_coarse = generate_grid(center_lon, center_lat, radius_m=800, step_m=100)
    best_coarse, err_coarse, t_coarse = scan(grid_coarse, road_factor=1.0, timeout_s=5.0, max_workers=10)
    grid_fine = generate_grid(best_coarse[0], best_coarse[1], radius_m=150, step_m=25)
    best_fine, err_fine, t_fine = scan(grid_fine, road_factor=1.0, timeout_s=5.0, max_workers=10)
    return {
        "label": "PoC(Table)",
        "best": best_fine,
        "avg_error_m": err_fine / len(FACILITIES),
        "points": len(grid_coarse) + len(grid_fine),
        "elapsed_s": t_coarse + t_fine,
    }


def run_backend_like() -> dict:
    center_lon = float(np.mean([f["coord"][0] for f in FACILITIES]))
    center_lat = float(np.mean([f["coord"][1] for f in FACILITIES]))

    grid_coarse = generate_grid(center_lon, center_lat, radius_m=1000, step_m=100)
    best_coarse, err_coarse, t_coarse = scan(grid_coarse, road_factor=1.1, timeout_s=3.0, max_workers=15)
    grid_fine = generate_grid(best_coarse[0], best_coarse[1], radius_m=200, step_m=20)
    best_fine, err_fine, t_fine = scan(grid_fine, road_factor=1.1, timeout_s=3.0, max_workers=15)
    return {
        "label": "BackendLike(Table)",
        "best": best_fine,
        "avg_error_m": err_fine / len(FACILITIES),
        "points": len(grid_coarse) + len(grid_fine),
        "elapsed_s": t_coarse + t_fine,
    }


def main() -> int:
    for r in (run_poc(), run_backend_like()):
        print(
            f'{r["label"]}: best={r["best"]} avg_error={r["avg_error_m"]:.2f}m points={r["points"]} elapsed={r["elapsed_s"]:.2f}s'
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

