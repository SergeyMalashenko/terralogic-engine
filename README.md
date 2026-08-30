# TerraLogic Acquisition

TerraLogic Acquisition coordinates deterministic collection and storage of land-parcel
data. It keeps the source adapters independent:

```text
cadastral number -> mcp-pynspd -> parcel contour -> minimum enclosing circle
                                      |                    |
                                      |              + configurable margin
                                      |                    |
                                      +-----------> mcp-osm + mcp-2gis
                                                           |
                                                           v
                                                   Local CaseStore
```

The current iteration provides:

- domain contracts for cases, immutable source snapshots, AOI, features, runs,
  and collection receipts;
- `LocalCaseStore`, backed by one SQLite database and raw gzip files per case;
- `AcquisitionPipeline`, which obtains the parcel and NSPD restrictions, builds
  one circular analysis area, and collects OSM and 2GIS concurrently;
- HTTP MCP clients for all three source services;
- a read-only map viewer for every normalized feature and raw snapshot.

The source repositories remain independent. `terralogic_acquisition` imports neither
`pynspd`, `pyosm-agents`, nor the `py2gis-agents` repository (whose Python
module is named `py2gis_agents`); it relies only on their MCP contracts.

## Installation

For local development:

```bash
python -m pip install -e '.[mcp]'
```

## Collection

Start `pynspd-mcp` on port 8001, `pyosm-mcp` on port 8002, and `py2gis-mcp`
on port 8003, then run:

```bash
terralogic-collect 52:26:0040002:3823 \
  --case-id case-52-26-0040002-3823 \
  --store ./case-store \
  --nspd-url http://127.0.0.1:8001/mcp \
  --osm-url http://127.0.0.1:8002/mcp \
  --dgis-url http://127.0.0.1:8003/mcp \
  --margin-m 1000 \
  --refresh-policy always
```

The fixed collection profile stores:

- NSPD: parcel information, its contour, and ZOUIT restrictions;
- OSM: forest, lake, and river contours plus stream and road lines;
- 2GIS: social infrastructure and public-transport/transport-hub objects.

The parcel's minimum enclosing radius plus `--margin-m` defines the shared
analysis circle. The margin defaults to 1000 metres. OSM receives the parcel
contour and this margin; 2GIS receives the calculated centre and final radius.

## Stored case

```text
case-store/
└── cases/
    └── <case_id>/
        ├── case.sqlite
        ├── manifest.json
        └── raw/
            ├── nspd/
            ├── osm/
            └── dgis/
```

Raw source responses are immutable gzip snapshots. Geometry is stored as WKB,
with CRS and bounding coordinates in separate columns.

## Case viewer

Install the optional Streamlit and Folium dependencies:

```bash
python -m pip install -e '.[viewer]'
```

Start the read-only viewer:

```bash
terralogic-view \
  --store ./case-store \
  --host 127.0.0.1 \
  --port 8501
```

It provides:

- case and snapshot selection in a sidebar;
- parcel, analysis circle, NSPD, OSM, and 2GIS layers on an interactive map;
- filters by source and feature class, source/class summaries, distances,
  feature attributes, collection warnings, and run history;
- dedicated forest, lake, and river contour styles with polygon-hole rendering,
  a map legend, natural-contour counters, and full-screen map mode;
- direct reading through the `CaseStore` interface rather than raw SQL.

The application binds to localhost by default. For a remote server, create an
SSH tunnel and then open `http://127.0.0.1:8501` locally:

```bash
ssh -L 8501:127.0.0.1:8501 user@remote-server
```

The viewer is intentionally an optional extra so collection and analytics do
not depend on a UI framework. Its screens and alternatives are described in
[`docs/case-viewer.md`](docs/case-viewer.md).
