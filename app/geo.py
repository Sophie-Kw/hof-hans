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

# Point maps can be huge (field 19 has ~1M points); downsample before sending.
MAX_POINTS_SHOWN = 20_000

# XML polygon maps can be huge too (field 19 CCI has ~500k vertices);
# cap the number of vertices sent to the browser.
MAX_XML_VERTICES = 20_000


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def _ring(points, start, end):
    ring = [[round(x, 7), round(y, 7)] for x, y in points[start:end]]
    if len(ring) >= 4 and ring[0] != ring[-1]:
        ring.append(ring[0])  # close the ring
    return ring


def _shape_to_geometry(shp):
    """Convert one pyshp shape record to a GeoJSON geometry (or None)."""
    st = shp.shapeType
    if st == 1:  # Point
        x, y = shp.points[0]
        return {"type": "Point", "coordinates": [round(x, 7), round(y, 7)]}
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


def _load_points(reader):
    total = reader.numRecords
    step = max(1, (total + MAX_POINTS_SHOWN - 1) // MAX_POINTS_SHOWN)
    coords = []
    for i in range(0, total, step):
        s = reader.shape(i)
        if s.shapeType == 1:
            x, y = s.points[0]
            coords.append([round(x, 7), round(y, 7)])
    return coords, total


def _parse_taskdata_rings(path):
    """Parse an ISO 11783 TaskData XML into closed WGS84 rings.

    The points already carry real coordinates (<PNT C="lat" D="lon"/>).
    Each <LSG> is one closed ring; rings of <PLN B="Obstacle"> polygons
    (no-go areas) are skipped.  Duplicate rings are dropped.
    Returns (rings, vertices) where vertices counts the parsed points.
    """
    import xml.etree.ElementTree as ET

    rings, seen, ring, skip, vertices = [], set(), [], False, 0
    for event, el in ET.iterparse(str(path), events=("start", "end")):
        if el.tag == "PLN" and event == "start":
            skip = (el.get("B") or "").startswith("Obstacle")
        elif el.tag == "LSG" and event == "end":
            if ring and not skip:
                key = tuple(map(tuple, ring))
                if key not in seen:
                    seen.add(key)
                    rings.append(ring)
            ring = []
        # attributes are read on "start": clear() wipes them before "end"
        elif el.tag == "PNT" and event == "start" and not skip:
            try:
                c, d = float(el.get("C")), float(el.get("D"))
            except (TypeError, ValueError):
                pass
            else:
                ring.append([round(c, 7), round(d, 7)])
                vertices += 1
        el.clear()
    return rings, vertices


def _simplify_rings(rings, vertices):
    """Uniformly sample the vertices of every ring down to a cap."""
    step = (vertices + MAX_XML_VERTICES - 1) // MAX_XML_VERTICES
    out = []
    for ring in rings:
        body = ring[:-1] if ring[0] == ring[-1] else list(ring)
        sampled = [body[i] for i in range(0, len(body), step)]
        if len(sampled) < 4:
            continue
        out.append(sampled + [sampled[0]])  # close the ring
    return out


def _build_map_xml(path: Path):
    """Build a polygon map from a TASKDATA.xml file (no shapefile)."""
    rings, vertices = _parse_taskdata_rings(path)
    if not rings:
        return None
    vertices_shown = vertices
    if vertices > MAX_XML_VERTICES:
        rings = _simplify_rings(rings, vertices)
        if not rings:
            return None
        vertices_shown = sum(len(r) for r in rings)
    features = [{"type": "Feature", "properties": {},
                 "geometry": {"type": "Polygon", "coordinates": [r]}}
                for r in rings]
    return {
        "kind": "Polygon",
        "total": len(features),
        "shown": len(features),
        "fc": {"type": "FeatureCollection", "features": features},
        "vertices": vertices,
        "vertices_shown": vertices_shown,
    }


def _build_map(field_dir: Path, map_name: str, file_name: str):
    """Load the shapefile of one map type; None if the map has no shapefile.

    Returns a dict with kind, total, shown and a GeoJSON geometry that the
    frontend wraps into a feature.
    """
    folder = field_dir / map_name
    if not folder.is_dir():
        return None
    # shapefiles may sit in a subfolder (John_Deere/Rx, CCI/TASKDATA)
    if map_name in ("CCI", "Fendt_XML", "John_Deere"):
        candidates = sorted(folder.rglob("*.shp"))
    else:
        candidates = [folder / f"{file_name}.shp"]
    candidates = [c for c in candidates if c.exists()]
    if not candidates:
        # CCI/Fendt_XML: use the ISO 11783 TaskData XML when there is no .shp
        if map_name in ("CCI", "Fendt_XML"):
            xml = folder / "TASKDATA" / "TASKDATA.xml"
            if xml.exists():
                return _build_map_xml(xml)
        return None

    reader = shapefile.Reader(str(candidates[0]))
    if reader.shapeType == 1:  # point map -> uniform sample
        coords, total = _load_points(reader)
        return {
            "kind": "Point",
            "total": total,
            "shown": len(coords),
            "geometry": {"type": "MultiPoint", "coordinates": coords},
        }

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


def get_map_geometry(field_dir: Path, map_name: str, file_name: str):
    key = (str(field_dir), map_name)
    with _cache_lock:
        cached = _map_cache.get(key)
    if cached is None:
        cached = _build_map(field_dir, map_name, file_name)
        with _cache_lock:
            _map_cache[key] = cached
    return cached

