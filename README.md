# Koko-Doko (物件立地推定システム)

このプロジェクトは、不動産情報から抽出した周辺施設への距離に基づき、物件の正確な所在地を推定するシステムです。

## OSRM (Open Source Routing Machine) のセットアップ

高精度かつ高速な推定を実現するため、ローカル環境で Docker を使用した OSRM サーバーを運用します。

### 1. 地図データの準備

メモリ消費を抑えるため、関東エリアのデータを推奨します。

```bash
mkdir -p osrm
cd osrm

# 関東地方のデータをダウンロード (約450MB)
wget http://download.geofabrik.de/asia/japan/kanto-latest.osm.pbf
```

### 2. データ加工（ビルド）

OSRM は生データをそのまま使えず、検索用のインデックス作成が必要です。
※メモリが 4GB 程度の場合は、事前に Swap 領域（8GB推奨）を作成してください。

```bash
# a. 経路情報の抽出 (徒歩プロファイル)
sudo docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-extract -p /opt/foot.lua /data/kanto-latest.osm.pbf

# b. パーティション作成
sudo docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-partition /data/kanto-latest.osrm

# c. 重み付けのカスタマイズ
sudo docker run -t -v "${PWD}:/data" osrm/osrm-backend osrm-customize /data/kanto-latest.osrm
```

### 3. サーバーの起動

加工したデータを使って、ポート 5000 でサーバーを起動します。

```bash
sudo docker run -d -p 5000:5000 --name osrm -v "${PWD}:/data" osrm/osrm-backend osrm-routed --algorithm mld /data/kanto-latest.osrm
```

### 4. バックエンドの設定

`backend/.env` を編集し、OSRM の向き先をローカルに変更します。

```env
OSRM_BASE_URL=http://localhost:5000/route/v1/foot/
OSRM_TABLE_BASE_URL=http://localhost:5000/table/v1/foot/
OSRM_MODE=route
```

---

## 運用コマンド

### サーバーの停止・再開
```bash
sudo docker stop osrm
sudo docker start osrm
```

### メモリ不足（OOM）時の Swap 作成例
```bash
sudo fallocate -l 8G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
```
