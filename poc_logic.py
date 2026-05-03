import requests
import numpy as np
from typing import List, Tuple, Dict
from concurrent.futures import ThreadPoolExecutor

# --- 設定・サンプルデータ ---
OSRM_BASE_URL = "http://router.project-osrm.org/route/v1/foot/"

FACILITIES = [
    {"name": "JR 津田沼駅", "coord": (140.02056, 35.69111), "dist_m": 950},
    {"name": "京成津田沼駅", "coord": (140.02444, 35.68361), "dist_m": 650},
    {"name": "青葉幼稚園", "coord": (140.02927, 35.68551), "dist_m": 350},
]

def get_osrm_distance(start_coord: Tuple[float, float], end_coord: Tuple[float, float]) -> float:
    url = f"{OSRM_BASE_URL}{start_coord[0]},{start_coord[1]};{end_coord[0]},{end_coord[1]}?overview=false"
    try:
        response = requests.get(url, timeout=5)
        data = response.json()
        if data.get("code") == "Ok":
            return data["routes"][0]["distance"]
    except Exception:
        pass
    return float('inf')

def evaluate_point(point: Tuple[float, float]) -> float:
    """各地点での誤差の合計を計算（並列化用）"""
    total_error = 0
    for fac in FACILITIES:
        osrm_dist = get_osrm_distance(point, fac["coord"])
        total_error += abs(osrm_dist - fac["dist_m"])
    return total_error

def generate_grid(center_lon: float, center_lat: float, radius_m: float, step_m: float):
    DEGREE_LAT_M = 111320
    DEGREE_LON_M = 111320 * np.cos(np.radians(center_lat))
    
    lat_step = step_m / DEGREE_LAT_M
    lon_step = step_m / DEGREE_LON_M
    num_steps = int(radius_m / step_m)
    
    lats = np.linspace(center_lat - lat_step * num_steps, center_lat + lat_step * num_steps, num_steps * 2 + 1)
    lons = np.linspace(center_lon - lon_step * num_steps, center_lon + lon_step * num_steps, num_steps * 2 + 1)
    
    return [(lon, lat) for lat in lats for lon in lons]

def scan_area(grid_points: List[Tuple[float, float]], max_workers=10):
    """グリッド点を並列でスキャンする"""
    best_point = None
    min_error = float('inf')
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        errors = list(executor.map(evaluate_point, grid_points))
    
    for point, error in zip(grid_points, errors):
        if error < min_error:
            min_error = error
            best_point = point
            
    return best_point, min_error

def solve():
    # 1. 初期中心
    center_lon = sum(f["coord"][0] for f in FACILITIES) / len(FACILITIES)
    center_lat = sum(f["coord"][1] for f in FACILITIES) / len(FACILITIES)

    print("--- Step 1: Coarse Scan (Wide area, large steps) ---")
    grid_coarse = generate_grid(center_lon, center_lat, radius_m=800, step_m=100)
    print(f"Scanning {len(grid_coarse)} points...")
    best_coarse, error_coarse = scan_area(grid_coarse)
    print(f"Best coarse point: {best_coarse}, Error: {error_coarse/len(FACILITIES):.2f}m")

    print("\n--- Step 2: Fine Scan (Focused area, small steps) ---")
    grid_fine = generate_grid(best_coarse[0], best_coarse[1], radius_m=150, step_m=25)
    print(f"Scanning {len(grid_fine)} points...")
    best_fine, error_fine = scan_area(grid_fine)

    print("\n--- 推定完了 ---")
    print(f"推定座標 (Lon, Lat): {best_fine}")
    print(f"最小平均誤差: {error_fine / len(FACILITIES):.2f} m")
    print(f"Google Maps Link: https://www.google.com/maps?q={best_fine[1]},{best_fine[0]}")

if __name__ == "__main__":
    solve()
