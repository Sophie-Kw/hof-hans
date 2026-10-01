"""Read-only access to data/: field master data, borders and map shapefiles.

All geodata is WGS84 (EPSG:4326), the same coordinate system Leaflet uses,
so no reprojection is needed anywhere.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import shapefile
import yaml

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FIELDS_DIR = DATA_DIR / "fields"
BIG_DIR = DATA_DIR / "big"

# The displayed map is always the John Deere map of the field. The selected
# terminal only changes the savings numbers and the downloadable map folder.
MAP_TYPE = "John_Deere"


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def _ring(points, start, end):
    ring = [[round(x, 7), round(y, 7)] for x, y in points[start:end]]
    if len(ring) >= 4 and ring[0] != ring[-1]:
        ring.append(ring[0])  # close the ring
    return ring


def _shape_to_geometry(shp):
    """Convert one pyshp shape record to a GeoJSON polygon geometry (or None)."""
    st = shp.shapeType
    if st not in (5, 25):  # Polygon, MultiPolygon
        return None
    points, parts = shp.points, shp.parts
    rings = []
    for i, start in enumerate(parts):
        end = parts[i + 1] if i + 1 < len(parts) else len(points)
        ring = _ring(points, start, end)
        if ring:
            rings.append(ring)
    if not rings:
        return None
    if st == 5:
        # one polygon: first ring is the outer ring, the rest are holes
        return {"type": "Polygon", "coordinates": rings}
    # MultiPolygon: parts cannot be reliably split into outer/inner,
    # so every ring is treated as its own outer ring
    return {"type": "MultiPolygon", "coordinates": [[r] for r in rings]}


# ---------------------------------------------------------------------------
# Static data (loaded once at startup)
# ---------------------------------------------------------------------------

def load_terminals() -> dict:
    return json.loads((DATA_DIR / "terminals.json").read_text(encoding="utf-8"))


def load_raw_field_ids() -> set:
    """Field ids that have a raw detections file in data/big/."""
    ids = set()
    if BIG_DIR.is_dir():
        for f in BIG_DIR.iterdir():
            m = re.match(r"(field_\d+)_detections\.shp$", f.name)
            if m:
                ids.add(m.group(1))
    return ids


def _border_geometry(field_dir: Path, file_name: str):
    shp = field_dir / "Field_border" / f"{file_name}.shp"
    if not shp.exists():
        return None
    reader = shapefile.Reader(str(shp))
    for i in range(reader.numRecords):
        geom = _shape_to_geometry(reader.shape(i))
        if geom:
            return geom
    return None


def load_fields() -> list:
    fields = []
    for d in sorted(FIELDS_DIR.iterdir()):
        yf = d / "field.yaml"
        if not d.is_dir() or not yf.exists():
            continue
        meta = yaml.safe_load(yf.read_text(encoding="utf-8"))
        meta["id"] = d.name
        meta["border"] = _border_geometry(d, meta["file_name"])
        fields.append(meta)
    return fields


# ---------------------------------------------------------------------------
# Map geometries (loaded lazily, cached)
# ---------------------------------------------------------------------------

_map_cache: dict = {}
_cache_lock = threading.Lock()


def _build_field_map(field_dir: Path):
    """Load the John Deere shapefile of one field as GeoJSON polygons."""
    folder = field_dir / MAP_TYPE
    candidates = [c for c in sorted(folder.rglob("*.shp")) if c.exists()]
    if not candidates:
        return None
    reader = shapefile.Reader(str(candidates[0]))
    features = []
    for i in range(reader.numRecords):
        geom = _shape_to_geometry(reader.shape(i))
        if geom:
            features.append({"type": "Feature", "properties": {},
                             "geometry": geom})
    if not features:
        return None
    return {
        "kind": "Polygon",
        "total": len(features),
        "shown": len(features),
        "fc": {"type": "FeatureCollection", "features": features},
    }


def get_field_map(field_dir: Path):
    key = str(field_dir)
    with _cache_lock:
        cached = _map_cache.get(key)
    if cached is None:
        cached = _build_field_map(field_dir)
        with _cache_lock:
            _map_cache[key] = cached
    return cached

