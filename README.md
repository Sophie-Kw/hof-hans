# Hof Hans

This is a web application for farmer Hans: it shows his fields on a map with the drone
detection results, lets him pick his sprayer terminal and section width, and
downloads the spray map in the exact format and folder structure his terminal
expects.

## Features

**Fields & Results tab**
- overview of all fields on the left
- interactive map on the right
- terminal + section width selection (9 terminals, 25/50/100/300 cm)
- results panel: herbicide saved in %, sprayed area, plants + coverage
- download button: ZIP with the terminal folder (e.g. `John_Deere/Rx/`),
  ready for the USB stick

**Raw Data tab (lightweight, first step of the big detection view)**
- field 19 (Hohe Breite, 74.3 ha, 1,409,404 detections) is selectable, all
  other fields are greyed out with "no raw data"
- field 19 border on the map + info block from `field.yaml`
- the raw detection files in `data/big/` (~290 MB) are not accessed yet

## Technologies
- Python / Flask (backend)
- Leaflet (map, via CDN) with OpenStreetMap tiles
- pyshp + PyYAML for reading the shapefiles and `field.yaml`
- Docker 

## Getting Started (local run)

Python 3.11+ is required.

```
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python -m app
```

Then open http://127.0.0.1:8080.

Note: the map tiles and the Leaflet scripts are loaded from the internet, so internet connection is needed for the map background.

## Data

See `data/README.md`. The app only reads `data/`, it never writes to it.
The raw detection files in `data/big/` are not accessed by the app at this
stage.

## Decisions & Assumptions
- savings [%] depend on terminal and section width; default section width is
  25 cm (finest, most savings)
- detection point maps (Amazone) can be huge (field 19: ~1 M points), so the
  map shows a uniform sample of at most 20,000 points and says
  "showing X of Y points"
- the CCI folder sometimes contains a `.shp` in addition to the `.xml` ->
  the ZIP contains the whole folder; the map prefers the `.shp` when both exist
- CCI / Fendt_XML maps without a `.shp` are drawn from the `TASKDATA.xml`
  (ISO 11783 TaskData; its points already carry WGS84 coordinates). Obstacle
  polygons are skipped, and maps with more than 20,000 vertices are shown as
  a simplified outline
- the full big-detection map over `data/big/` is planned for a later step
