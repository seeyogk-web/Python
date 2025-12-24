from flask import Blueprint, request, jsonify, current_app, Response
from sqlalchemy import text
import csv, io

bp = Blueprint("api_generated", __name__)

def get_engine():
    engine = current_app.config.get("DB_ENGINE")
    if engine is None:
        raise RuntimeError("DB engine not configured")
    return engine

def paginate_params():
    try:
        page = int(request.args.get("page", 1))
    except:
        page = 1
    try:
        per_page = int(request.args.get("per_page", 25))
    except:
        per_page = 25
    if per_page > 100:
        per_page = 100
    offset = (page - 1) * per_page
    return page, per_page, offset

@bp.route("/results", methods=["GET"])
def get_results():
    engine = get_engine()
    page, per_page, offset = paginate_params()
    export = request.args.get("export", "").lower() == "csv"

    where = []
    params = {}
    for k, v in request.args.items():
        if k.startswith("filter__"):
            col = k.split("filter__",1)[1]
            where.append(f'"{col}" = :{col}')
            params[col] = v

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    sql = text(f'SELECT * FROM "public"."results" {where} ORDER BY 1 DESC LIMIT :limit OFFSET :offset'.replace("{where}", where_sql))
    params.update({"limit": per_page, "offset": offset})
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
            data = [dict(r) for r in rows]
            if export:
                if not data:
                    return jsonify({"data": []})
                output = io.StringIO()
                w = csv.DictWriter(output, fieldnames=list(data[0].keys()))
                w.writeheader()
                w.writerows(data)
                return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=results.csv"})
            return jsonify({"page": page, "per_page": per_page, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@bp.route("/jobs", methods=["GET"])
def get_jobs():
    engine = get_engine()
    page, per_page, offset = paginate_params()
    export = request.args.get("export", "").lower() == "csv"
    sql = text(f'SELECT * FROM "public"."interview" ORDER BY 1 DESC LIMIT :limit OFFSET :offset')
    try:
        with engine.connect() as conn:
            rows = conn.execute(sql, {"limit": per_page, "offset": offset}).mappings().all()
            data = [dict(r) for r in rows]
            if export:
                if not data:
                    return jsonify({"data": []})
                output = io.StringIO()
                w = csv.DictWriter(output, fieldnames=list(data[0].keys()))
                w.writeheader()
                w.writerows(data)
                return Response(output.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment;filename=jobs.csv"})
            return jsonify({"page": page, "per_page": per_page, "data": data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
