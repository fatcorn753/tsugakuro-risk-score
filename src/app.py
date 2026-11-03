import json

from flask import Flask, render_template

import db

app = Flask(__name__)

# 学校ランキング・地図の色分けで「上位」とみなす密度スコアの割合
RANK_HIGH_PERCENTILE = 0.2  # 上位20%
RANK_MID_PERCENTILE = 0.5   # 上位50%


@app.route("/")
def index():
    schools = db.list_schools()
    routes = db.list_routes()
    accidents = db.list_accidents()
    summary = db.summary()

    # 学校ごとの密度スコアから色分け用のランクを付与（地図の凡例と対応させる）
    density_values = sorted((s["density"] for s in schools), reverse=True)
    high_cut = density_values[int(len(density_values) * RANK_HIGH_PERCENTILE)] if density_values else 0
    mid_cut = density_values[int(len(density_values) * RANK_MID_PERCENTILE)] if density_values else 0

    def rank_of(density):
        if density >= high_cut:
            return "high"
        if density >= mid_cut:
            return "mid"
        return "low"

    school_rank = {s["name"]: rank_of(s["density"]) for s in schools}
    schools = [dict(s, rank=school_rank[s["name"]]) for s in schools]

    route_features = [
        {
            "school": r["school"],
            "category": r["category"],
            "points": json.loads(r["points_json"]),
            "rank": school_rank.get(r["school"], "low"),
        }
        for r in routes
    ]
    accident_points = [
        {"lat": a["lat"], "lng": a["lng"], "is_ped": bool(a["is_ped"]), "is_fatal": bool(a["is_fatal"])}
        for a in accidents
    ]

    return render_template(
        "index.html",
        schools=schools,
        route_features=route_features,
        accident_points=accident_points,
        summary=summary,
        near_threshold_m=db.NEAR_THRESHOLD_M,
    )


if __name__ == "__main__":
    db.init_db()
    app.run(host="0.0.0.0", port=5000, debug=True)
