# TerraLogic Acquisition

TerraLogic Acquisition coordinates deterministic collection and storage of land-parcel
data. It keeps the source adapters independent:

```text
cadastral number -> mcp-pynspd -> parcel contour -> mcp-osm
                                      |
                                      v
                              Local Case Store
```

The current iteration provides:

- domain contracts for cases, immutable source snapshots, AOI, features, runs,
  and collection receipts;
- `LocalCaseStore`, backed by one SQLite database and raw gzip files per case;
- `AcquisitionPipeline`, which obtains the parcel and NSPD layer blocks, sends
  the exact WGS84 contour to OSM, and preserves complete or partial results;
- HTTP MCP clients for the two source services.

The source repositories remain independent. `terralogic_acquisition` imports neither
`pynspd` nor `pyosm-agents`; it relies only on their MCP contracts.

## Installation

For local development:

```bash
python -m pip install -e '.[mcp]'
```

## Collection

Start `pynspd-mcp` on port 8001 and the contour-based `pyosm-mcp` on port 8002,
then run:

```bash
terralogic-collect 52:26:0040002:3823 \
  --case-id case-52-26-0040002-3823 \
  --store ./case-store \
  --nspd-url http://127.0.0.1:8001/mcp \
  --osm-url http://127.0.0.1:8002/mcp
```

The OSM client targets `osm_analyze_area`, whose input is the parcel GeoJSON,
not a cadastral number. This matches the independent contour-based contract in
`pyosm-agents>=0.3.0`.

## Stored case

```text
case-store/
└── cases/
    └── <case_id>/
        ├── case.sqlite
        ├── manifest.json
        └── raw/
            ├── nspd/
            └── osm/
```

Raw source responses are immutable gzip snapshots. Geometry is stored as WKB,
with CRS and bounding coordinates in separate columns.

## Proposed viewer

The recommended viewer is a read-only Streamlit application with Folium:

- case and snapshot selection in a sidebar;
- parcel, NSPD and OSM layers on an interactive map;
- layer filters, feature attributes, collection warnings and run history;
- direct reading through the `CaseStore` interface rather than raw SQL.

It is intentionally an optional extra (`terralogic-acquisition[viewer]`) so collection and
analytics do not depend on a UI framework. Its implementation is planned after
the acquisition contracts are exercised against the updated OSM MCP server.
The proposed screens and alternatives are described in
[`docs/case-viewer.md`](docs/case-viewer.md).
