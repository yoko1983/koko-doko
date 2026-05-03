import React, { useState, useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap, Circle } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';
import L from 'leaflet';
import { MapPin, Search, Loader2, ExternalLink, Trash2 } from 'lucide-react';
import axios from 'axios';

// Leaflet default icon fix
import icon from 'leaflet/dist/images/marker-icon.png';
import iconShadow from 'leaflet/dist/images/marker-shadow.png';
let DefaultIcon = L.icon({
    iconUrl: icon,
    shadowUrl: iconShadow,
    iconSize: [25, 41],
    iconAnchor: [12, 41]
});
L.Marker.prototype.options.icon = DefaultIcon;

const API_BASE = "http://localhost:8000";

interface Facility {
  name: string;
  place_name?: string;
  dist_m: number;
  coord?: [number, number];
}

interface EstimationResult {
  coord: [number, number];
  avg_error: number;
  google_maps: string;
}

type GeocoderProvider = 'auto' | 'google' | 'mapbox';

function ChangeView({ center }: { center: [number, number] }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, 15);
  }, [center, map]);
  return null;
}

function App() {
  const [inputText, setInputText] = useState('');
  const [facilities, setFacilities] = useState<Facility[]>([]);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<EstimationResult | null>(null);
  const [mapCenter, setMapCenter] = useState<[number, number]>([35.6812, 139.7671]); // Tokyo Station
  const [errorLog, setErrorLog] = useState<string | null>(null);
  const [areaName, setAreaName] = useState<string>('');
  const [areaCoord, setAreaCoord] = useState<[number, number] | undefined>(undefined);
  const [geocoderProvider, setGeocoderProvider] = useState<GeocoderProvider>(() => {
    const saved = localStorage.getItem('geocoderProvider');
    if (saved === 'auto' || saved === 'google' || saved === 'mapbox') return saved;
    return 'auto';
  });

  useEffect(() => {
    localStorage.setItem('geocoderProvider', geocoderProvider);
  }, [geocoderProvider]);

  // 座標取得ロジック（bbox対応版）
  const runGeocoding = async (targetFacilities: Facility[], areaCoord?: [number, number], areaName?: string) => {
    const updated = [...targetFacilities];
    let hasError = false;

    // 市区町村の座標があれば、そこから±0.05度（約5km）の範囲をbboxとする
    let bbox: string | undefined = undefined;
    if (areaCoord) {
      const offset = 0.05;
      bbox = `${areaCoord[0] - offset},${areaCoord[1] - offset},${areaCoord[0] + offset},${areaCoord[1] + offset}`;
    }

    for (let i = 0; i < updated.length; i++) {
      if (!updated[i].coord) {
        try {
          const res = await axios.get(`${API_BASE}/geocode`, { 
            params: { 
              name: updated[i].name,
              bbox: bbox,
              proximity: areaCoord ? `${areaCoord[0]},${areaCoord[1]}` : undefined,
              area: areaName || undefined,
              provider: geocoderProvider,
            } 
          });
          
          if (res.data.coord) {
            updated[i].coord = [res.data.coord[0], res.data.coord[1]];
            updated[i].place_name = res.data.place_name;
          } else {
            hasError = true;
          }
        } catch (err: any) {
          console.error(`Geocoding error for ${updated[i].name}:`, err);
          hasError = true;
        }
      }
    }
    if (hasError) {
      setErrorLog("【座標取得】 一部の施設の座標が見つかりませんでした。施設名を修正して「座標を再取得」を試してください。");
    }
    setFacilities([...updated]);
  };

  // 1. テキストから抽出 + 座標取得
  const handleExtract = async () => {
    if (!inputText) return;
    setLoading(true);
    setErrorLog(null);
    setResult(null);
    try {
      const res = await axios.post(`${API_BASE}/extract`, { text: inputText });
      const extractedAreaName = res.data.area || '';
      const extractedFacilities: Facility[] = res.data.facilities;
      setFacilities(extractedFacilities);
      setAreaName(extractedAreaName);
      
      // まずエリアの座標を取得
      let extractedAreaCoord: [number, number] | undefined = undefined;
      if (extractedAreaName) {
        try {
          const areaRes = await axios.get(`${API_BASE}/geocode`, { params: { name: extractedAreaName, provider: geocoderProvider } });
          if (areaRes.data.coord) {
            extractedAreaCoord = [areaRes.data.coord[0], areaRes.data.coord[1]];
            setAreaCoord(extractedAreaCoord);
            setMapCenter([areaRes.data.coord[1], areaRes.data.coord[0]]);
          }
        } catch (e) { console.error("Area geocoding failed", e); }
      }

      // 範囲を絞って施設を検索
      await runGeocoding(extractedFacilities, extractedAreaCoord, extractedAreaName);
    } catch (err: any) {
      console.error("Extraction error:", err);
      setErrorLog(`【抽出失敗】 ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleManualGeocode = () => {
    setLoading(true);
    setErrorLog(null);
    runGeocoding(facilities, areaCoord, areaName).finally(() => setLoading(false));
  };

  const handleEstimate = async () => {
    const validFacilities = facilities.filter(f => f.coord);
    if (validFacilities.length < 2) {
      setErrorLog("【推定失敗】 座標が取得できている施設が2つ以上必要です。");
      return;
    }
    setLoading(true);
    setErrorLog(null);
    try {
      const res = await axios.post(`${API_BASE}/estimate`, { facilities: validFacilities });
      setResult(res.data);
      setMapCenter([res.data.coord[1], res.data.coord[0]]);
    } catch (err: any) {
      console.error("Estimation error:", err);
      setErrorLog(`【推定失敗】 ${err.response?.data?.detail || err.message}`);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-gray-50 p-4 md:p-8 font-sans text-gray-900">
      <header className="mb-8 flex items-center gap-2">
        <MapPin className="text-blue-600 w-8 h-8" />
        <h1 className="text-3xl font-bold tracking-tight">koko-dayo</h1>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-8 max-w-7xl mx-auto">
        <div className="space-y-6">
	          <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
	            <h2 className="text-lg font-semibold mb-3">1. テキストを貼り付け</h2>
	            <textarea
	              className="w-full h-40 p-4 bg-gray-50 border border-gray-200 rounded-lg focus:ring-2 focus:ring-blue-500 outline-none transition-all"
	              placeholder="不動産サイトの近隣施設情報をここに貼り付けてください..."
	              value={inputText}
	              onChange={(e) => setInputText(e.target.value)}
	            />
	            <div className="mt-3 flex items-center gap-3">
	              <label className="text-xs text-gray-600 font-medium">座標取得プロバイダ</label>
	              <select
	                value={geocoderProvider}
	                onChange={(e) => setGeocoderProvider(e.target.value as GeocoderProvider)}
	                className="text-sm bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 outline-none focus:ring-2 focus:ring-blue-500"
	              >
	                <option value="auto">自動（推奨）</option>
	                <option value="google">Google Places（New）</option>
	                <option value="mapbox">Mapbox</option>
	              </select>
	            </div>
	            <button
	              onClick={handleExtract}
	              disabled={loading || !inputText}
	              className="mt-4 w-full bg-blue-600 text-white py-3 rounded-lg font-medium hover:bg-blue-700 disabled:bg-gray-300 flex items-center justify-center gap-2 transition-colors"
	            >
              {loading ? <Loader2 className="animate-spin" /> : <Search size={20} />}
              施設情報を抽出して解析
            </button>

            {errorLog && (
              <div className="mt-4 p-3 bg-red-50 border border-red-200 rounded-lg">
                <p className="text-xs font-mono text-red-600 break-all select-all cursor-text">
                  {errorLog}
                </p>
                <p className="text-[10px] text-red-400 mt-1">※メッセージはコピーして共有できます</p>
              </div>
            )}
          </section>

	          {facilities.length > 0 && (
	            <section className="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
	              <h2 className="text-lg font-semibold mb-3">2. 施設リストの確認</h2>
	              <div className="space-y-2 mb-4 max-h-60 overflow-y-auto">
	                {facilities.map((f, i) => (
	                  <div key={i} className="flex items-center justify-between p-3 bg-gray-50 rounded-lg border border-gray-100">
                    <div className="flex-1">
                      <input 
                        type="text" 
                        value={f.name} 
                        onChange={(e) => {
                          const updated = [...facilities];
                          updated[i].name = e.target.value;
                          setFacilities(updated);
                        }}
                        className="font-medium text-sm bg-transparent border-none focus:ring-0 w-full p-0 outline-none"
                      />
                      <p className="text-xs text-gray-500">
                        {f.dist_m}m {f.coord ? `✅ (${f.place_name || '取得済み'})` : '❌ (座標なし)'}
                      </p>
                    </div>
                    <button 
                      onClick={() => setFacilities(facilities.filter((_, idx) => idx !== i))}
                      className="text-gray-400 hover:text-red-500 p-1"
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
              </div>
              <div className="flex gap-2">
                <button
                  onClick={handleManualGeocode}
                  disabled={loading}
                  className="flex-1 bg-gray-800 text-white py-2 rounded-lg text-sm hover:bg-gray-900 transition-colors"
                >
                  座標を再取得
                </button>
                <button
                  onClick={handleEstimate}
                  disabled={loading}
                  className="flex-1 bg-green-600 text-white py-2 rounded-lg text-sm hover:bg-green-700 transition-colors"
                >
                  位置を推定
                </button>
              </div>
            </section>
          )}

          {result && (
            <section className="bg-green-50 p-6 rounded-xl border border-green-100">
              <h2 className="text-green-800 font-bold mb-2">推定結果</h2>
              <p className="text-green-700 text-sm mb-4">平均誤差: {result.avg_error.toFixed(1)}m</p>
              <a 
                href={result.google_maps} 
                target="_blank" 
                rel="noreferrer"
                className="inline-flex items-center gap-2 bg-white text-green-700 px-4 py-2 rounded-lg border border-green-200 shadow-sm hover:bg-green-100 transition-all font-medium"
              >
                Google マップで開く <ExternalLink size={16} />
              </a>
            </section>
          )}
        </div>

        <div className="h-[600px] lg:h-auto sticky top-4">
          <div className="h-full w-full bg-white rounded-2xl shadow-lg border border-gray-200 overflow-hidden relative">
            <MapContainer center={mapCenter} zoom={15} style={{ height: '100%', width: '100%' }}>
              <ChangeView center={mapCenter} />
              <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
              
              {facilities.filter(f => f.coord).map((f, i) => (
                <React.Fragment key={i}>
                  <Marker position={[f.coord![1], f.coord![0]]}>
                    <Popup>{f.name} ({f.dist_m}m)</Popup>
                  </Marker>
                  <Circle 
                    center={[f.coord![1], f.coord![0]]} 
                    radius={f.dist_m} 
                    pathOptions={{ color: 'blue', weight: 1, fillOpacity: 0.05 }} 
                  />
                </React.Fragment>
              ))}

              {result && (
                <Marker position={[result.coord[1], result.coord[0]]}>
                  <Popup>推定地点 (平均誤差: {result.avg_error.toFixed(1)}m)</Popup>
                </Marker>
              )}
            </MapContainer>
            {loading && (
              <div className="absolute inset-0 bg-white/60 backdrop-blur-sm z-[1000] flex items-center justify-center">
                <Loader2 className="animate-spin text-blue-600 w-12 h-12" />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export default App;
