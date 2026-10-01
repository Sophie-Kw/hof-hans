"""Read-only access to data/: field master data, borders and map shapefiles.

All geodata is WGS84 (EPSG:4326), the same coordinate system Leaflet uses,
so no reprojection is needed anywhere.
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from math import cos, radians
from pathlib import Path

import shapefile
import yaml
from shapely.geometry import box, mapping, shape

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
FIELDS_DIR = DATA_DIR / "fields"
BIG_DIR = DATA_DIR / "big"
CACHE_DIR = DATA_DIR.parent / ".cache"

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

RAW_MAX_POINTS = 75000
RAW_CELL_METERS = 10
RAW_GRID_VERSION = "10m-wgs84-clipped-v3"
RAW_POINT_ZOOM = 19


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


def _raw_db_path(field_id: str) -> Path:
    return CACHE_DIR / f"{field_id}_detections.sqlite3"


def _raw_db_is_current(connection, source: Path) -> bool:
    values = dict(connection.execute("SELECT key, value FROM metadata"))
    stat = source.stat()
    return (values.get("source_size") == str(stat.st_size) and
            values.get("source_mtime") == str(stat.st_mtime_ns) and
            values.get("grid_version") == RAW_GRID_VERSION)


def _build_raw_database(field_id: str, source: Path) -> None:
    """Build a local R-tree cache without changing the source shapefile."""
    if not source.exists():
        raise FileNotFoundError(source)
    cache_path = _raw_db_path(field_id)
    CACHE_DIR.mkdir(exist_ok=True)
    connection = sqlite3.connect(cache_path)
    try:
        connection.executescript("""
            DROP TABLE IF EXISTS raw_points;
            DROP TABLE IF EXISTS raw_index;
            DROP TABLE IF EXISTS raw_cells;
            DROP TABLE IF EXISTS metadata;
            CREATE TABLE raw_points (
                id INTEGER PRIMARY KEY,
                x REAL NOT NULL,
                y REAL NOT NULL,
                class_info TEXT,
                confidence REAL,
                spray_r REAL
            );
            CREATE VIRTUAL TABLE raw_index USING rtree(
                id, min_x, max_x, min_y, max_y
            );
            CREATE TABLE raw_cells (
                id INTEGER PRIMARY KEY,
                min_x REAL NOT NULL,
                max_x REAL NOT NULL,
                min_y REAL NOT NULL,
                max_y REAL NOT NULL,
                x REAL NOT NULL,
                y REAL NOT NULL,
                count INTEGER NOT NULL,
                confidence REAL,
                geometry TEXT NOT NULL
            );
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        reader = shapefile.Reader(str(source))
        min_x, min_y, max_x, max_y = reader.bbox
        field_yaml = FIELDS_DIR / field_id / "field.yaml"
        field_meta = yaml.safe_load(field_yaml.read_text(encoding="utf-8"))
        border = _border_geometry(FIELDS_DIR / field_id,
                      field_meta["file_name"])
        field_shape = shape(border) if border else None
        center_lat = (min_y + max_y) / 2
        cell_height = RAW_CELL_METERS / 111_320
        cell_width = RAW_CELL_METERS / (111_320 * cos(radians(center_lat)))
        cells = {}
        point_rows = []
        index_rows = []
        for point_id, shape_record in enumerate(reader.iterShapeRecords(), 1):
            values = shape_record.record.as_dict()
            x, y = shape_record.shape.points[0]
            point_rows.append((point_id, x, y, values.get("class_info"),
                               values.get("confidence"), values.get("spray_r")))
            index_rows.append((point_id, x, x, y, y))
            column = int((x - min_x) / cell_width)
            row = int((y - min_y) / cell_height)
            cell = cells.setdefault((column, row), [0, 0.0, 0.0, 0.0, 0])
            cell[0] += 1
            cell[1] += x
            cell[2] += y
            if values.get("confidence") is not None:
                cell[3] += float(values["confidence"])
                cell[4] += 1
            if len(point_rows) >= 10000:
                connection.executemany("INSERT INTO raw_points VALUES (?, ?, ?, ?, ?, ?)",
                                       point_rows)
                connection.executemany("INSERT INTO raw_index VALUES (?, ?, ?, ?, ?)",
                                       index_rows)
                point_rows.clear()
                index_rows.clear()
        if point_rows:
            connection.executemany("INSERT INTO raw_points VALUES (?, ?, ?, ?, ?, ?)",
                                   point_rows)
            connection.executemany("INSERT INTO raw_index VALUES (?, ?, ?, ?, ?)",
                                   index_rows)
        cell_rows = []
        for (column, row), (count, x_sum, y_sum, confidence_sum,
                            confidence_count) in cells.items():
            cell_min_x = min_x + column * cell_width
            cell_min_y = min_y + row * cell_height
            cell_shape = box(cell_min_x, cell_min_y,
                             cell_min_x + cell_width,
                             cell_min_y + cell_height)
            if field_shape is not None:
                cell_shape = cell_shape.intersection(field_shape)
            if cell_shape.is_empty:
                continue
            clipped_min_x, clipped_min_y, clipped_max_x, clipped_max_y = cell_shape.bounds
            cell_rows.append((len(cell_rows) + 1, clipped_min_x,
                              clipped_max_x, clipped_min_y, clipped_max_y,
                              x_sum / count,
                              y_sum / count, count,
                              (confidence_sum / confidence_count
                               if confidence_count else None),
                              json.dumps(mapping(cell_shape))))
        connection.executemany("INSERT INTO raw_cells VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               cell_rows)
        stat = source.stat()
        connection.executemany("INSERT INTO metadata VALUES (?, ?)", [
            ("source_size", str(stat.st_size)),
            ("source_mtime", str(stat.st_mtime_ns)),
            ("grid_version", RAW_GRID_VERSION),
        ])
        connection.commit()
    finally:
        connection.close()


def _ensure_raw_database(field_id: str) -> Path:
    source = BIG_DIR / f"{field_id}_detections.shp"
    cache_path = _raw_db_path(field_id)
    if cache_path.exists():
        connection = sqlite3.connect(cache_path)
        try:
            if _raw_db_is_current(connection, source):
                return cache_path
        except sqlite3.OperationalError:
            pass
        finally:
            connection.close()
    _build_raw_database(field_id, source)
    return cache_path


def get_raw_points(field_id: str, bbox: tuple, zoom: int) -> dict:
    """Return raw detections in a bbox, aggregating dense views into cells."""
    with _cache_lock:
        cache_path = _ensure_raw_database(field_id)

    min_x, min_y, max_x, max_y = bbox
    connection = sqlite3.connect(cache_path)
    try:
        if zoom < RAW_POINT_ZOOM:
            rows = connection.execute("""
            SELECT geometry, count, confidence
                FROM raw_cells
                WHERE max_x >= ? AND min_x <= ?
                  AND max_y >= ? AND min_y <= ?
            """, (min_x, max_x, min_y, max_y))
            cell_rows = list(rows)
            count = sum(row[1] for row in cell_rows)
            features = _raw_cell_features(cell_rows)
            mode = "grid"
        else:
            count = connection.execute("""
                SELECT COUNT(*)
                FROM raw_index
                WHERE max_x >= ? AND min_x <= ? AND max_y >= ? AND min_y <= ?
            """, (min_x, max_x, min_y, max_y)).fetchone()[0]
            if zoom < RAW_POINT_ZOOM or count > RAW_MAX_POINTS:
                rows = connection.execute("""
                    SELECT geometry, count, confidence
                    FROM raw_cells
                    WHERE max_x >= ? AND min_x <= ?
                      AND max_y >= ? AND min_y <= ?
                """, (min_x, max_x, min_y, max_y))
                features = _raw_cell_features(list(rows))
                mode = "grid"
            else:
                rows = connection.execute("""
                SELECT p.x, p.y, p.class_info, p.confidence, p.spray_r
                FROM raw_index AS i
                JOIN raw_points AS p ON p.id = i.id
                WHERE i.max_x >= ? AND i.min_x <= ?
                  AND i.max_y >= ? AND i.min_y <= ?
                """, (min_x, max_x, min_y, max_y))
                features = [_raw_point_feature(row) for row in rows]
                mode = "points"
    finally:
        connection.close()
    return {
        "mode": mode,
        "total": count,
        "shown": len(features),
        "fc": {"type": "FeatureCollection", "features": features},
    }


def _raw_point_feature(point: tuple) -> dict:
    x, y, class_info, confidence, spray_r = point
    return {"type": "Feature", "geometry": {"type": "Point",
            "coordinates": [x, y]},
            "properties": {"class_info": class_info,
                           "confidence": confidence,
                           "spray_r": spray_r}}


def _raw_cell_features(rows) -> list:
    return [{
        "type": "Feature",
        "geometry": json.loads(geometry),
        "properties": {"count": count, "confidence": confidence},
    } for geometry, count, confidence in rows]


