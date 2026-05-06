import os
import logging
import json
import re
import math
from urllib.parse import quote
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Tuple, Optional
from google import genai
import requests
import numpy as np
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

# --- ロギング設定 ---
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("backend.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# --- モデル定義 ---
class ExtractionRequest(BaseModel):
    text: str

class Facility(BaseModel):
    name: str
    place_name: Optional[str] = None
    dist_m: int
    coord: Optional[Tuple[float, float]] = None

class EstimationRequest(BaseModel):
    facilities: List[Facility]

# --- 配置・定数 ---
GEMINI_MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-3.1-flash-lite-preview")
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
MAPBOX_ACCESS_TOKEN = os.getenv("MAPBOX_ACCESS_TOKEN")
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY")
GEOCODER_PROVIDER = os.getenv("GEOCODER_PROVIDER", "mapbox").lower()  # mapbox | google | auto
OSRM_BASE_URL = os.getenv("OSRM_BASE_URL", "http://localhost:5000/route/v1/foot/")
OSRM_TABLE_BASE_URL = os.getenv("OSRM_TABLE_BASE_URL", "http://localhost:5000/table/v1/foot/")
OSRM_MODE = os.getenv("OSRM_MODE", "route").lower()  # route | table
OSRM_TIMEOUT_S = float(os.getenv("OSRM_TIMEOUT_S", "3"))
OSRM_TABLE_MAX_WORKERS = int(os.getenv("OSRM_TABLE_MAX_WORKERS", "6"))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- 1. テキスト抽出 ---
@app.post("/extract")
async def extract_facilities(req: ExtractionRequest):
    logger.info(f"Extract request received using {GEMINI_MODEL_NAME}")
    prompt = f"""
    あなたは不動産情報の構造化エンジニアです。
    以下の不動産テキストから周辺施設と、物件がある「市区町村名」を抽出し、以下のJSON形式で回答してください。
    もしテキストに市区町村名が明記されていない場合は、施設名（例：津田沼駅→習志野市）から推測してください。

    {{
      "area": "市区町村名",
      "facilities": [
        {{"name": "施設名", "dist_m": 距離(数値)}}
      ]
    }}

    【ルール】
    1. 回答はJSONデータのみを出力してください。
    2. 距離が「分」の場合は「80m/分」で計算して数値にしてください。
    3. JSON以外の文字（解説や```jsonなどの装飾）は一切含めないでください。

    テキスト:
    {req.text}
    """
    try:
        response = client.models.generate_content(model=GEMINI_MODEL_NAME, contents=prompt)
        content = response.text.strip()
        logger.info(f"Model response (raw): {content[:200]}...")

        json_match = re.search(r"```json\s*(\{.*?\})\s*```", content, re.DOTALL)
        if not json_match:
            json_match = re.search(r"(\{.*\})", content, re.DOTALL)

        if json_match:
            json_str = json_match.group(1).strip()
            json_str = re.sub(r",\s*([\]\}])", r"\1", json_str)
            try:
                raw_data = json.loads(json_str)
                area = raw_data.get("area", "")
                facilities_raw = raw_data.get("facilities", [])
                normalized_data = []
                for item in facilities_raw:
                    name = item.get("name") or "不明な施設"
                    dist = item.get("dist_m") or item.get("distance") or 0
                    normalized_data.append({"name": name, "dist_m": int(dist)})
                
                logger.info(f"Successfully extracted {len(normalized_data)} facilities in {area}")
                return {"area": area, "facilities": normalized_data}
            except json.JSONDecodeError as je:
                logger.error(f"JSON decode error: {je}")
                raise HTTPException(status_code=500, detail="JSON Parse Error")
        else:
            raise HTTPException(status_code=500, detail="No JSON found")
    except Exception as e:
        logger.exception("Error during extraction")
        raise HTTPException(status_code=500, detail=str(e))


# --- 2. ジオコーディング (Mapbox) ---
def is_in_bbox(coord: List[float], bbox_str: str) -> bool:
    try:
        minLon, minLat, maxLon, maxLat = map(float, bbox_str.split(","))
        return minLon <= coord[0] <= maxLon and minLat <= coord[1] <= maxLat
    except:
        return True

def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    r = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))

def geocode_mapbox(name: str, proximity: Optional[str] = None, bbox: Optional[str] = None, area: Optional[str] = None) -> dict:
    if not MAPBOX_ACCESS_TOKEN:
        raise HTTPException(status_code=500, detail="Mapbox token missing")
    
    # 検索戦略をさらに精緻化
    search_queries = [name]
    if area:
        search_queries.append(f"{name} {area}")
    # 特定のキーワードが含まれていない場合、カテゴリーを補完したクエリも試す
    if "駅" in name and "駅" == name[-1]:
        search_queries.append(f"{name} 鉄道駅")
        if area:
            search_queries.append(f"{name} 鉄道駅 {area}")
    if "幼稚園" in name:
        search_queries.append(f"{name} 幼稚園")
        if area:
            search_queries.append(f"{name} 幼稚園 {area}")
    if "小学校" in name:
        search_queries.append(f"{name} 小学校")
        if area:
            search_queries.append(f"{name} 小学校 {area}")
    if "公園" in name:
        search_queries.append(f"{name} 公園")
        if area:
            search_queries.append(f"{name} 公園 {area}")

    proximity_lonlat: Optional[Tuple[float, float]] = None
    if proximity:
        try:
            lon_s, lat_s = proximity.split(",")
            proximity_lonlat = (float(lon_s), float(lat_s))
        except Exception:
            proximity_lonlat = None

    for query in search_queries:
        encoded_query = quote(query, safe="")
        base_url = f"https://api.mapbox.com/geocoding/v5/mapbox.places/{encoded_query}.json"

        strategies: List[Tuple[str, dict, bool]] = []
        if bbox:
            strategies.append((
                base_url,
                {"access_token": MAPBOX_ACCESS_TOKEN, "limit": 5, "country": "jp", "bbox": bbox, "language": "ja"},
                True,  # bboxを厳密適用
            ))

        params = {"access_token": MAPBOX_ACCESS_TOKEN, "limit": 5, "country": "jp", "language": "ja"}
        if proximity:
            params["proximity"] = proximity
        strategies.append((base_url, params, False))  # bboxは使わない（＝枠外も許容。ただし距離で弾く）

        for url, params, strict_bbox in strategies:
            try:
                response = requests.get(url, params=params, timeout=5)
                data = response.json()

                features = data.get("features")
                if not isinstance(features, list):
                    msg = data.get("message") or data.get("error")
                    if msg:
                        logger.error(f"Unexpected Mapbox response: {data}")
                    continue

                if features:
                    best_match = None
                    best_match_dist = float("inf")
                    fallback = None
                    fallback_dist = float("inf")

                    for feat in features:
                        coord = feat.get("center")
                        if not (isinstance(coord, list) and len(coord) == 2):
                            continue

                        if strict_bbox and bbox and not is_in_bbox(coord, bbox):
                            continue

                        dist_m = None
                        if proximity_lonlat:
                            dist_m = haversine_m(proximity_lonlat[0], proximity_lonlat[1], coord[0], coord[1])
                            if (not strict_bbox) and dist_m > 20000:
                                continue

                        place_name = feat.get("place_name") or ""
                        area_ok = (not area) or (area in place_name)

                        if area_ok:
                            d = dist_m if dist_m is not None else 0.0
                            if d < best_match_dist:
                                best_match_dist = d
                                best_match = feat
                        else:
                            d = dist_m if dist_m is not None else 0.0
                            if d < fallback_dist:
                                fallback_dist = d
                                fallback = feat

                    if best_match:
                        coord = best_match["center"]
                        logger.info(f"Mapbox found: {best_match.get('place_name')} at {coord}")
                        return {"coord": coord, "place_name": best_match.get("place_name")}

                    if area and fallback:
                        # areaが指定されているのに一致しない結果しかない場合は、誤認識を避けるため採用しない
                        logger.warning(
                            f"No results matched area='{area}' for {name}. Closest mismatch was: {fallback.get('place_name')}"
                        )
                        continue
            except Exception as e:
                continue
            
    logger.warning(f"No valid local results found for {name}")
    return {"coord": None}

def geocode_google_places(name: str, proximity: Optional[str] = None, bbox: Optional[str] = None, area: Optional[str] = None) -> dict:
    if not GOOGLE_PLACES_API_KEY:
        raise HTTPException(status_code=500, detail="Google Places API key missing")

    text_query = name
    if area:
        text_query = f"{name} {area}"

    url = "https://places.googleapis.com/v1/places:searchText"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.location,places.types",
    }

    body: dict = {
        "textQuery": text_query,
        "languageCode": "ja",
        "regionCode": "JP",
    }

    if bbox:
        try:
            min_lon, min_lat, max_lon, max_lat = map(float, bbox.split(","))
            body["locationBias"] = {
                "rectangle": {
                    "low": {"latitude": min_lat, "longitude": min_lon},
                    "high": {"latitude": max_lat, "longitude": max_lon},
                }
            }
        except Exception:
            pass
    elif proximity:
        try:
            lon_s, lat_s = proximity.split(",")
            body["locationBias"] = {
                "circle": {
                    "center": {"latitude": float(lat_s), "longitude": float(lon_s)},
                    "radius": 5000.0,
                }
            }
        except Exception:
            pass

    try:
        resp = requests.post(url, headers=headers, data=json.dumps(body), timeout=10)
        data = resp.json()
    except Exception:
        logger.exception(f"Google Places request failed for {name}")
        return {"coord": None}

    if resp.status_code >= 400:
        logger.error(f"Unexpected Google Places response ({resp.status_code}) for {name}: {data}")
        return {"coord": None}

    places = data.get("places")
    if not isinstance(places, list) or not places:
        logger.warning(f"No Google Places results found for {name}")
        return {"coord": None}

    # 最初の候補を採用（locationBiasをかけているので relevance に近い順が期待）
    place = places[0]
    loc = (place.get("location") or {})
    lat = loc.get("latitude")
    lon = loc.get("longitude")
    if lat is None or lon is None:
        logger.warning(f"Google Places result had no location for {name}: {place}")
        return {"coord": None}

    display_name = ((place.get("displayName") or {}).get("text")) or ""
    formatted = place.get("formattedAddress") or ""
    place_name = formatted if formatted else display_name
    logger.info(f"Google Places found: {display_name or place_name} at [{lon}, {lat}]")
    return {"coord": [float(lon), float(lat)], "place_name": place_name}

@app.get("/geocode")
async def geocode(
    name: str,
    proximity: Optional[str] = None,
    bbox: Optional[str] = None,
    area: Optional[str] = None,
    provider: Optional[str] = None,
):
    resolved_provider = (provider or GEOCODER_PROVIDER or "mapbox").lower()
    logger.info(f"Geocode request: {name} (provider: {resolved_provider}, bbox: {bbox})")

    if resolved_provider == "google":
        return geocode_google_places(name=name, proximity=proximity, bbox=bbox, area=area)
    if resolved_provider == "mapbox":
        return geocode_mapbox(name=name, proximity=proximity, bbox=bbox, area=area)
    if resolved_provider == "auto":
        result = {"coord": None}
        if GOOGLE_PLACES_API_KEY:
            result = geocode_google_places(name=name, proximity=proximity, bbox=bbox, area=area)
        if result.get("coord"):
            return result
        return geocode_mapbox(name=name, proximity=proximity, bbox=bbox, area=area)

    raise HTTPException(status_code=400, detail="Unknown geocoder provider")

# --- 3. 立地推定ロジック ---
def get_osrm_distance(start_coord: Tuple[float, float], end_coord: Tuple[float, float]) -> float:
    url = f"{OSRM_BASE_URL}{start_coord[0]},{start_coord[1]};{end_coord[0]},{end_coord[1]}?overview=false"
    logger.debug(f"OSRM Route Request: {url}")
    try:
        response = requests.get(url, timeout=OSRM_TIMEOUT_S)
        data = response.json()
        if data.get("code") == "Ok":
            return data["routes"][0]["distance"]
        logger.warning(f"OSRM Route Error: {data.get('code')} for {url}")
    except Exception as e:
        logger.error(f"OSRM Route Exception: {str(e)} for {url}")
    return float('inf')

def get_osrm_table_distances(start_coord: Tuple[float, float], end_coords: List[Tuple[float, float]]) -> List[float]:
    if not end_coords:
        return []
    coords = [start_coord, *end_coords]
    coord_str = ";".join([f"{lon},{lat}" for lon, lat in coords])
    url = f"{OSRM_TABLE_BASE_URL}{coord_str}"
    params = {
        "sources": "0",
        "destinations": ";".join(str(i) for i in range(1, len(coords))),
        "annotations": "distance",
    }
    logger.debug(f"OSRM Table Request: {url} params={params}")
    try:
        response = requests.get(url, params=params, timeout=OSRM_TIMEOUT_S)
        data = response.json()
        if data.get("code") != "Ok":
            logger.warning(f"OSRM Table Error: {data.get('code')} for {url}")
            return [float("inf")] * len(end_coords)
        distances = data.get("distances")
        if not (isinstance(distances, list) and distances and isinstance(distances[0], list)):
            logger.warning(f"OSRM Table Malformed Data: {distances}")
            return [float("inf")] * len(end_coords)
        row = distances[0]
        if len(row) < (1 + len(end_coords)):
            return [float("inf")] * len(end_coords)
        out: List[float] = []
        for d in row[1 : 1 + len(end_coords)]:
            out.append(float(d) if d is not None else float("inf"))
        return out
    except Exception as e:
        logger.error(f"OSRM Table Exception: {str(e)} for {url}")
        return [float("inf")] * len(end_coords)

def evaluate_point(point: Tuple[float, float], facilities: List[Facility]) -> float:
    total_error = 0
    ROAD_FACTOR = 1.0  # PoCの精度を再現するため1.0に戻す
    INF_PENALTY = 10000 

    if OSRM_MODE == "table":
        end_coords = [fac.coord for fac in facilities if fac.coord]
        osrm_dists = get_osrm_table_distances(point, end_coords)
        facs = [fac for fac in facilities if fac.coord]
        for fac, osrm_dist in zip(facs, osrm_dists):
            if osrm_dist < float('inf'):
                total_error += abs(osrm_dist - (fac.dist_m * ROAD_FACTOR))
            else:
                total_error += INF_PENALTY
    else:
        for fac in facilities:
            if not fac.coord:
                continue
            osrm_dist = get_osrm_distance(point, fac.coord)
            if osrm_dist < float('inf'):
                total_error += abs(osrm_dist - (fac.dist_m * ROAD_FACTOR))
            else:
                total_error += INF_PENALTY
    return total_error

@app.post("/estimate")
async def estimate_location(req: EstimationRequest):
    facilities = [f for f in req.facilities if f.coord]
    if len(facilities) < 2:
        raise HTTPException(status_code=400, detail="Need at least 2 coords")

    logger.info(f"Estimate request: facilities={len(facilities)} osrm_mode={OSRM_MODE} timeout={OSRM_TIMEOUT_S}s")
    lons = [f.coord[0] for f in facilities]
    lats = [f.coord[1] for f in facilities]
    center_lon, center_lat = np.mean(lons), np.mean(lats)

    def generate_grid(c_lon, c_lat, radius, step):
        DEGREE_LAT_M = 111320
        DEGREE_LON_M = 111320 * np.cos(np.radians(c_lat))
        l_step, n_step = step/DEGREE_LAT_M, int(radius/step)
        lo_step = step/DEGREE_LON_M
        lt_range = np.linspace(c_lat - l_step*n_step, c_lat + l_step*n_step, n_step*2+1)
        ln_range = np.linspace(c_lon - lo_step*n_step, c_lon + lo_step*n_step, n_step*2+1)
        return [(float(ln), float(lt)) for lt in lt_range for ln in ln_range]

    def scan(grid):
        best, min_err = None, float('inf')
        workers = OSRM_TABLE_MAX_WORKERS if OSRM_MODE == "table" else 15
        with ThreadPoolExecutor(max_workers=workers) as executor:
            errors = list(executor.map(lambda p: evaluate_point(p, facilities), grid))
        for p, e in zip(grid, errors):
            if e < min_err:
                min_err, best = e, p
        return best, min_err

    try:
        # 1段階（超広域）: 半径800mを200m刻みでアタリをつける（約81地点）
        best_1, err_1 = scan(generate_grid(center_lon, center_lat, 800, 200))
        if best_1 is None or err_1 >= float('inf'):
            logger.error(f"Stage 1 scan failed: err={err_1}")
            raise HTTPException(status_code=502, detail=f"OSRM calculation failed (Stage 1). err={err_1}")

        # 2段階（中域）: 半径200mを50m刻みで絞り込む（約81地点）
        best_2, err_2 = scan(generate_grid(best_1[0], best_1[1], 200, 50))
        if best_2 is None or err_2 >= float('inf'):
            logger.error(f"Stage 2 scan failed: err={err_2}")
            raise HTTPException(status_code=502, detail=f"OSRM calculation failed (Stage 2). err={err_2}")

        # 3段階（詳細）: 半径50mを10m刻みでピンポイント特定（約121地点）
        best_3, err_3 = scan(generate_grid(best_2[0], best_2[1], 50, 10))
        if best_3 is None or err_3 >= float('inf'):
            logger.error(f"Stage 3 scan failed: err={err_3}")
            raise HTTPException(status_code=502, detail=f"OSRM calculation failed (Stage 3). err={err_3}")

        logger.info(f"Estimation successful: {best_3} (err={err_3})")
        return {
            "coord": best_3,
            "avg_error": err_3 / len(facilities),
            "google_maps": f"https://www.google.com/maps?q={best_3[1]},{best_3[0]}",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Error during estimation")
        raise HTTPException(status_code=500, detail=str(e))
