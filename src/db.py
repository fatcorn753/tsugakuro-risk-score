# -*- coding: utf-8 -*-
"""通学路データ×交通事故オープンデータの突き合わせ・集計と、SQLiteへの格納。"""

import csv
import json
import os
import sqlite3
from collections import defaultdict

import shapefile
from pyproj import Transformer
from shapely.geometry import LineString, Point

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "app.db")
DATA_SRC_DIR = os.path.join(os.path.dirname(__file__), "data_src")
ROUTE_SHP = os.path.join(DATA_SRC_DIR, "gakko_route")
ACCIDENT_CSV = os.path.join(DATA_SRC_DIR, "ichikawa_accidents_2024.csv")

# 通学路と事故地点が「近接している」とみなす距離（メートル）
NEAR_THRESHOLD_M = 25.0

# 危険度スコアの重み（死亡事故を最重視、歩行者・自転車が絡む事故を次に重視）
SCORE_WEIGHT_FATAL = 10
SCORE_WEIGHT_PED = 3
SCORE_WEIGHT_OTHER = 1

_to_wgs84 = Transformer.from_crs("EPSG:6677", "EPSG:4326", always_xy=True)
_to_planar = Transformer.from_crs("EPSG:4326", "EPSG:6677", always_xy=True)

# 警察庁「当事者種別」コード：61=歩行者、51=軽車両（自転車）
PEDESTRIAN_PARTY_CODES = {"61", "51"}
# 警察庁「事故内容」コード：1=死亡事故
FATAL_ACCIDENT_CODE = "1"

# 通学路データの「レイヤ名」が空だった場合に割り当てる学校名（集計対象からは除外する）
UNKNOWN_SCHOOL = "不明"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _dms_to_deg(raw_value, deg_digits):
    """警察庁オープンデータの緯度・経度（度分秒を数値結合した形式）を10進度に変換する。"""
    value = int(raw_value)
    s = str(value).zfill(deg_digits + 7)
    deg = int(s[:deg_digits])
    minute = int(s[deg_digits:deg_digits + 2])
    sec = int(s[deg_digits + 2:deg_digits + 7]) / 1000
    return deg + minute / 60 + sec / 3600


def _load_routes():
    """市川市オープンデータ「小学校通学ルート」を読み込み、平面直角座標系のまま返す。"""
    sf = shapefile.Reader(ROUTE_SHP, encoding="cp932")
    routes = []
    for sr in sf.shapeRecords():
        school = (sr.record["レイヤ名"] or "").strip() or UNKNOWN_SCHOOL
        # データ内の表記ゆれ「学校名○○小学校」を正規化する
        school = school.removeprefix("学校名")
        category = (sr.record["通学路区分"] or "").strip()
        points_planar = sr.shape.points
        if len(points_planar) < 2:
            continue
        line = LineString(points_planar)
        points_wgs84 = [
            list(reversed(_to_wgs84.transform(x, y))) for x, y in points_planar
        ]  # [lat, lng] の順に（Leaflet用）
        routes.append({
            "school": school,
            "category": category,
            "line": line,
            "length_m": line.length,
            "points_wgs84": points_wgs84,
        })
    return routes


def _load_accidents():
    """警察庁「交通事故統計情報」から抽出した市川市2024年分を読み込む。"""
    accidents = []
    with open(ACCIDENT_CSV, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lat = _dms_to_deg(row["lat_raw"], 2)
            lng = _dms_to_deg(row["lng_raw"], 3)
            x, y = _to_planar.transform(lng, lat)
            is_ped = row["party_a"] in PEDESTRIAN_PARTY_CODES or row["party_b"] in PEDESTRIAN_PARTY_CODES
            is_fatal = row["accident_type"] == FATAL_ACCIDENT_CODE
            accidents.append({
                "lat": lat, "lng": lng, "x": x, "y": y,
                "is_ped": is_ped, "is_fatal": is_fatal,
                "month": int(row["month"]), "hour": int(row["hour"]),
            })
    return accidents


def _match_and_score(routes, accidents):
    """通学路ごとにNEAR_THRESHOLD_M以内の事故を集計し、学校別の危険度スコアを算出する。

    戻り値の第2要素は、各事故がいずれかの通学路の近くで発生したかどうかを示す
    真偽値のリスト（accidentsと同じ順序）。summary()の集計にのみ使用する。

    通学路×事故の全組み合わせを総当たりで距離計算している（O(件数×本数)）。
    市川市規模（819本×958件、0.9秒程度）では問題ないが、対象を県全域に広げる等
    データ量が大きく増える場合は、shapely.STRtree等の空間インデックスで
    近傍候補を絞り込む方式への変更を検討すること。
    """
    known_routes = [r for r in routes if r["school"] != UNKNOWN_SCHOOL]
    school_stats = defaultdict(lambda: {"length_m": 0.0, "near_acc": 0, "ped_acc": 0, "fatal_acc": 0})
    for r in known_routes:
        school_stats[r["school"]]["length_m"] += r["length_m"]

    accident_is_near_route = []
    for a in accidents:
        p = Point(a["x"], a["y"])
        hit_schools = {r["school"] for r in known_routes if r["line"].distance(p) <= NEAR_THRESHOLD_M}
        accident_is_near_route.append(bool(hit_schools))
        for school in hit_schools:
            st = school_stats[school]
            st["near_acc"] += 1
            if a["is_ped"]:
                st["ped_acc"] += 1
            if a["is_fatal"]:
                st["fatal_acc"] += 1

    school_rows = []
    for school, st in school_stats.items():
        other_acc = st["near_acc"] - st["ped_acc"]
        score = st["fatal_acc"] * SCORE_WEIGHT_FATAL + st["ped_acc"] * SCORE_WEIGHT_PED + other_acc * SCORE_WEIGHT_OTHER
        density = score / (st["length_m"] / 1000) if st["length_m"] > 0 else 0.0
        school_rows.append({
            "name": school, "length_m": st["length_m"],
            "near_acc": st["near_acc"], "ped_acc": st["ped_acc"], "fatal_acc": st["fatal_acc"],
            "score": score, "density": density,
        })
    school_rows.sort(key=lambda r: -r["density"])
    return school_rows, accident_is_near_route


def init_db():
    conn = get_connection()
    conn.executescript(
        """
        DROP TABLE IF EXISTS schools;
        DROP TABLE IF EXISTS routes;
        DROP TABLE IF EXISTS accidents;

        CREATE TABLE schools (
            name TEXT PRIMARY KEY,
            length_m REAL,
            near_acc INTEGER,
            ped_acc INTEGER,
            fatal_acc INTEGER,
            score INTEGER,
            density REAL
        );

        CREATE TABLE routes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            school TEXT NOT NULL,
            category TEXT,
            length_m REAL,
            points_json TEXT NOT NULL
        );

        CREATE TABLE accidents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lat REAL, lng REAL,
            is_ped INTEGER, is_fatal INTEGER,
            month INTEGER, hour INTEGER,
            near_route INTEGER
        );
        """
    )

    routes = _load_routes()
    accidents = _load_accidents()
    school_rows, accident_is_near_route = _match_and_score(routes, accidents)

    conn.executemany(
        "INSERT INTO schools (name, length_m, near_acc, ped_acc, fatal_acc, score, density) "
        "VALUES (:name, :length_m, :near_acc, :ped_acc, :fatal_acc, :score, :density)",
        school_rows,
    )

    conn.executemany(
        "INSERT INTO routes (school, category, length_m, points_json) VALUES (?, ?, ?, ?)",
        [(r["school"], r["category"], r["length_m"], json.dumps(r["points_wgs84"])) for r in routes],
    )

    conn.executemany(
        "INSERT INTO accidents (lat, lng, is_ped, is_fatal, month, hour, near_route) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (a["lat"], a["lng"], int(a["is_ped"]), int(a["is_fatal"]), a["month"], a["hour"], int(is_near))
            for a, is_near in zip(accidents, accident_is_near_route)
        ],
    )

    conn.commit()
    conn.close()


def list_schools():
    conn = get_connection()
    rows = conn.execute("SELECT * FROM schools ORDER BY density DESC").fetchall()
    conn.close()
    return rows


def list_routes():
    conn = get_connection()
    rows = conn.execute("SELECT school, category, length_m, points_json FROM routes").fetchall()
    conn.close()
    return rows


def list_accidents():
    """地図に表示する事故地点を返す（near_routeはsummary()の集計にのみ使うためここでは取得しない）。"""
    conn = get_connection()
    rows = conn.execute("SELECT lat, lng, is_ped, is_fatal FROM accidents").fetchall()
    conn.close()
    return rows


def summary():
    conn = get_connection()
    row = conn.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM accidents) AS total_accidents,
            (SELECT COUNT(*) FROM accidents WHERE near_route = 1) AS near_route_accidents,
            (SELECT COUNT(*) FROM accidents WHERE is_ped = 1) AS ped_accidents,
            (SELECT COUNT(*) FROM accidents WHERE is_fatal = 1) AS fatal_accidents,
            (SELECT COUNT(DISTINCT school) FROM routes) AS school_count,
            (SELECT SUM(length_m) FROM schools) AS total_route_length_m
        """
    ).fetchone()
    conn.close()
    return row
