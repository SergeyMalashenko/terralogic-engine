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
- `AnalysisPipeline`, which calculates reproducible intersections and shortest
  distances for one immutable collection run and stores the result in CaseStore;
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
- OSM: forest, waterbody, and river contours plus stream and road lines;
- 2GIS: social infrastructure and public-transport/transport-hub objects.

The parcel's minimum enclosing radius plus `--margin-m` defines the shared
analysis circle. The margin defaults to 1000 metres. OSM receives the parcel
contour and this margin; 2GIS receives the calculated centre and final radius.

## Analytics

Run analytics after collection. By default, the newest collection receipt is
used:

```bash
terralogic-analyze case-52-26-0040002-3823 \
  --store ./case-store
```

To calculate metrics for a specific immutable source set, pass its collection
run identifier:

```bash
terralogic-analyze case-52-26-0040002-3823 \
  --run-id run-a5d215d9dd464441b4d8fb7e7ce381b1 \
  --store ./case-store
```

The persisted result contains:

- each ZOUIT intersection area, its percentage of the parcel, and its
  percentage of the zone; the combined coverage uses a polygon union and does
  not double-count overlapping zones;
- non-double-counted parcel intersection area for OSM forests, waterbodies,
  and river polygons, including an aggregate union for all areal water
  resources;
- the length of linear OSM streams inside the parcel (a line has no area);
- the shortest distance from the parcel boundary or interior to every social
  infrastructure category found by 2GIS;
- the shortest distance to each OSM natural-resource class and to the nearest
  water resource overall.

All calculations use the local metric CRS stored with the acquisition AOI.
Distances are measured from the parcel geometry, not from the search-circle
centre. A missing nearest object means only that no candidate was collected
inside the configured search circle.

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
- a dedicated analytics tab with tables for ZOUIT coverage, natural-resource
  intersections, nearest social infrastructure, and nearest natural objects;
- dedicated forest, waterbody, and river contour styles with polygon-hole rendering,
  a map legend, natural-contour counters, and full-screen map mode;
- separate road layers and colors for every collected OSM `highway` class,
  including a neutral fallback for unknown values;
- permanent name/number labels for motorway, trunk, primary, secondary, and
  tertiary roads; lower road classes remain unlabelled;
- optional permanent labels for 2GIS point objects only when `name` is present;
- direct reading through the `CaseStore` interface rather than raw SQL.

The application binds to localhost by default. For a remote server, create an
SSH tunnel and then open `http://127.0.0.1:8501` locally:

```bash
ssh -L 8501:127.0.0.1:8501 user@remote-server
```

The viewer is intentionally an optional extra so collection and analytics do
not depend on a UI framework. Its screens and alternatives are described in
[`docs/case-viewer.md`](docs/case-viewer.md).
