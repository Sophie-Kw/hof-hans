"""Flask app for Hof Hans: field data, map geometries, downloads and page."""
from __future__ import annotations

import tempfile
import time
import zipfile
from pathlib import Path

from flask import (Flask, abort, jsonify, render_template, request,
                   send_file)

from . import geo

SECTIONS = [25, 50, 100, 300]  # sprayer section widths in cm
FIELD_KEYS = ("id", "name", "file_name", "area_ha", "flight_date",
              "plant_type", "plants_in_field", "covered_area_ha",
              "categories", "border")


def create_app() -> Flask:
    geo.validate_data()
    app = Flask(__name__)
    app.data = {
        "fields": geo.load_fields(),
        "terminals": geo.load_terminals(),
        "raw_fields": geo.load_raw_field_ids(),
    }
    app.field_by_id = {f["id"]: f for f in app.data["fields"]}
    # temp dir for download zips; cleaned up on teardown
    app.tmp_dir = Path(tempfile.mkdtemp(prefix="hofhans_"))

    @app.teardown_appcontext
    def _clean_tmp(_exc):
        now = time.time()
        for f in app.tmp_dir.iterdir():
            if now - f.stat().st_mtime > 600:  # keep recent files
                f.unlink(missing_ok=True)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/api/fields")
    def api_fields():
        fields = []
        for f in app.data["fields"]:
            item = {k: f[k] for k in FIELD_KEYS}
            item["has_raw_data"] = f["id"] in app.data["raw_fields"]
            fields.append(item)
        return jsonify({
            "fields": fields,
            "terminals": app.data["terminals"]["terminals"],
            "maps": app.data["terminals"]["maps"],
            "sections": SECTIONS,
        })

    def _get_field(field_id: str):
        field = app.field_by_id.get(field_id)
        if field is None:
            abort(404)
        return field

    def _get_map_name(field: dict) -> str:
        map_name = request.args.get("map")
        if map_name not in field.get("available_maps", []):
            abort(404)
        return map_name

    @app.get("/api/fields/<field_id>/map")
    def api_field_map(field_id: str):
        field = _get_field(field_id)
        # the displayed map is always the John Deere map of the field
        map_geom = geo.get_field_map(geo.FIELDS_DIR / field_id)
        # one FeatureCollection: border + (if any) the map itself
        features = []
        if field["border"] is not None:
            features.append({"type": "Feature",
                             "properties": {"kind": "border"},
                             "geometry": field["border"]})
        if map_geom is not None:
            for feat in map_geom["fc"]["features"]:
                feat["properties"] = {"kind": "map"}
                features.append(feat)
        return jsonify({
            "kind": map_geom["kind"] if map_geom else None,
            "total": map_geom["total"] if map_geom else 0,
            "shown": map_geom["shown"] if map_geom else 0,
            "fc": {"type": "FeatureCollection", "features": features},
        })

    @app.get("/api/raw/<field_id>/detections")
    def api_raw_detections(field_id: str):
        _get_field(field_id)
        if field_id not in app.data["raw_fields"]:
            abort(404)
        try:
            values = [float(value) for value in request.args["bbox"].split(",")]
            zoom = int(request.args.get("zoom", 14))
        except (KeyError, TypeError, ValueError):
            abort(400, description="bbox must be min_lon,min_lat,max_lon,max_lat")
        if len(values) != 4 or values[0] >= values[2] or values[1] >= values[3]:
            abort(400, description="invalid bbox")
        return jsonify(geo.get_raw_points(field_id, tuple(values), zoom))

    @app.get("/api/fields/<field_id>/download")
    def api_download(field_id: str):
        field = _get_field(field_id)
        map_name = _get_map_name(field)
        field_dir = geo.FIELDS_DIR / field_id
        zip_path = app.tmp_dir / f"{field['file_name']}_{map_name}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for folder_name in (map_name, "Field_border"):
                folder = field_dir / folder_name
                for src in sorted(folder.rglob("*")):
                    if src.is_file() and not src.name.startswith("."):
                        arc = Path(folder_name) / src.relative_to(folder)
                        zf.write(src, arc)

        return send_file(zip_path, as_attachment=True,
                         download_name=f"{field['file_name']}_{map_name}.zip")

    return app
