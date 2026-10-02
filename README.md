# Hof Hans

This is a web application for farmer Hans: it shows his fields on a map with the drone
detection results, lets him pick his sprayer terminal and section width, and
downloads the spray map in the exact format and folder structure his terminal
expects.

## Getting Started (Docker)

Docker (with the `docker compose` plugin) is the only requirement.

1. Clone the repository.
2. Put the data into place (see [Data](#data)).
3. Start:

   ```
   docker compose up --build
   ```

4. Open http://localhost:8080 and stop with `Ctrl+C` (or `docker compose down`).

<!-- Notes:
- the first Raw Data request on field 19 builds an internal SQLite cache from
  the ~1.4 M detection points (can take a minute or two). It is stored in the
  Docker volume `raw-cache`, so later starts reuse it; it is rebuilt
  automatically if the source shapefile changes.
- the map background (tiles) and the Leaflet scripts are loaded from the
  internet, so an internet connection is needed.
- port 8080 must be free (close a local development instance if one is running). -->

## Data

The geodata is **not in the repository** and not part of the Docker image. It is provided
separately. Extract it so that the repo looks like:

```
hof-hans/
├── app/
├── data/                     
│   ├── fields/field_01 … field_20/
│   ├── big/field_19_detections.{shp,shx,dbf,prj,cpg}
│   └── terminals.json
├── Dockerfile
├── compose.yaml
└── README.md
```

The app only reads `data/`, it never writes to it.
If the data (or parts of it) is missing, the app does not start with an
empty map but fails with a clear message.

## Local run without Docker

Python 3.11+ is required.

```
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
python -m app
```

Then open http://127.0.0.1:8080.

## Decisions & Assumptions
- the displayed map is always the John Deere map of the field; the selected
  terminal only changes the savings numbers and the downloaded map folder
- the download ZIP contains the whole map folder of the selected terminal
  plus the field border, so it works as-is on the terminal
