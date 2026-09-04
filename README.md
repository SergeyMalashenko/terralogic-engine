# TerraLogic Engine

TerraLogic Engine is the central land-parcel processing service. It coordinates
source acquisition, immutable case storage, deterministic spatial analytics,
versioned report generation, and read-only visualization while keeping the
source adapters independent:

```text
cadastral number -> mcp-pynspd -> parcel contour -> minimum enclosing circle
       |                              |                    |
       |                              |              + configurable margin
       |                              |                    |
       |                              +-----------> mcp-osm + mcp-2gis
       |                                                   |
       +-- region 50 and configured --> mcp-pyrgis         |
                               |                           |
                               +-------------+-------------+
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
- HTTP MCP clients for NSPD, OSM, 2GIS, and optional regional RGIS MO;
- a high-level MCP server for Hermes that prepares a case, returns a bounded
  factual report context, and persists the generated Markdown report;
- a read-only map viewer for every normalized feature and raw snapshot.

The source repositories remain independent. `terralogic_engine` imports neither
`pynspd`, `pyosm-agents`, `py2gis-agents`, nor `pyrgis-agents`; it relies only
on their MCP contracts.

## Installation

For local development:

```bash
python -m pip install -e '.[mcp,viewer]'
```

When upgrading an environment that previously contained the distribution under
its old name, remove its stale package metadata once before reinstalling:

```bash
python -m pip uninstall terralogic-acquisition
python -m pip install -e '.[mcp,viewer]'
```

The public CLI commands remain `terralogic-collect`, `terralogic-analyze`,
`terralogic-mcp`, and `terralogic-view`. Python imports now use the
`terralogic_engine` namespace.

## Collection

Start `pynspd-mcp` on port 8001, `pyosm-mcp` on port 8002, and `py2gis-mcp`
on port 8003. For parcels in cadastral region `50`, you may additionally start
`pyrgis-mcp` on port 8005. Then run:

```bash
terralogic-collect 52:26:0040002:3823 \
  --case-id case-52-26-0040002-3823 \
  --store ./case-store \
  --nspd-url http://127.0.0.1:8001/mcp \
  --osm-url http://127.0.0.1:8002/mcp \
  --dgis-url http://127.0.0.1:8003/mcp \
  --rgis-url http://127.0.0.1:8005/mcp \
  --margin-m 1000 \
  --refresh-policy always
```

The fixed collection profile stores:

- NSPD: parcel information, its contour, and ZOUIT restrictions;
- OSM: forest, waterbody, and river contours plus stream and road lines;
- 2GIS: social infrastructure and public-transport/transport-hub objects.
- RGIS MO, when configured and applicable: the regional parcel passport plus
  restriction/special and urban-planning layer blocks.

`--rgis-url` is optional. Even when configured, RGIS is called only for a
cadastral number beginning with `50:`. Changing RGIS availability invalidates
a previously reusable receipt so that a region-50 case cannot silently reuse a
collection made without the regional source.

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

## Hermes report workflow

Keep the three required source MCP services running on ports 8001-8003. For
region `50`, optionally keep `pyrgis-mcp` on port 8005, then start the
high-level TerraLogic server:

```bash
terralogic-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8004 \
  --store ~/TerraLogicX/case-store \
  --nspd-url http://127.0.0.1:8001/mcp \
  --osm-url http://127.0.0.1:8002/mcp \
  --dgis-url http://127.0.0.1:8003/mcp \
  --rgis-url http://127.0.0.1:8005/mcp
```

It exposes exactly four high-level tools:

- `terralogic_prepare_case` collects source data and runs analytics;
- `terralogic_get_report_context` returns compact facts without GeoJSON;
- `terralogic_get_report_template` returns an independent, immutable report
  structure identified by `template_id`, version, and SHA-256;
- `terralogic_save_report` persists the complete model-generated Markdown.

Report context version 1.2 includes an `urban_planning` block for RGIS-backed
region-50 cases: parcel planning zones and permitted uses, GPZU, PZZ
territorial zones, planning projects, and surveying projects. The default
`full_land_report` template is version 1.1; immutable version 1.0 remains
available for previously generated reports.

Configure Hermes to use `http://127.0.0.1:8004/mcp`. For this workflow, expose
only the TerraLogic server to the model; the NSPD, OSM, 2GIS, and optional RGIS
servers remain running as internal dependencies but can be disabled in the
Hermes MCP list.
This prevents the model from bypassing CaseStore and the deterministic
analytics stage.

Suggested Hermes request:

```text
Подготовь полный отчёт по земельному участку 50:32:0000000:38218.
Сначала вызови terralogic_prepare_case, затем получи report context и
шаблон full_land_report версии 1.1.
Используй только факты и числа из контекста, не выполняй вычисления сам.
Заполни шаблон, не меняя обязательные заголовки, и сохрани его через
terralogic_save_report с теми же template_id и template_version.
Верни краткое резюме и идентификатор отчёта.
```

A production-oriented, section-by-section task template is available in
[`examples/hermes-full-land-report-v1.1.md`](examples/hermes-full-land-report-v1.1.md).
It can be passed directly to Hermes with:

```bash
hermes -z "$(<examples/hermes-full-land-report-v1.1.md)"
```

The exact report contract, Hermes configuration, and troubleshooting commands
are documented in [`docs/hermes-reporting.md`](docs/hermes-reporting.md).

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
            ├── dgis/
            └── rgis/
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
- parcel, analysis circle, NSPD, OSM, 2GIS, and RGIS layers on an interactive
  map;
- filters by source and feature class, source/class summaries, distances,
  feature attributes, collection warnings, and run history;
- a dedicated analytics tab with tables for ZOUIT coverage, natural-resource
  intersections, nearest social infrastructure, and nearest natural objects;
- a report tab that renders and downloads the newest Markdown artifact for the
  selected collection run;
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
