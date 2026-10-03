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

## Workspace bootstrap

All repositories belong to one GitHub account (`SergeyMalashenko`) and deploy
as siblings in a single workspace directory. Code-level dependencies (uv path
dependencies) form this hierarchy:

```text
geodocs-store 0.2.0  (package "geodocs", shared document SQLite store)
        ^                        ^
        | editable               | editable
        |                        |
pynspd-agents 0.4.0        pyrgis-agents 0.6.0  (pyrgis-mcp :8005)
(pynspd-mcp :8001)         deps: pyrgis 0.6.0  (editable ../pyrgis),
deps: geodocs (editable    geodocs (editable ../geodocs-store),
      ../geodocs-store),    mcp
      pynspd>=1.1.13 from PyPI

pyosm-agents 0.4.1 (:8002), py2gis-agents 0.1.0 (:8003) — no internal deps
terralogic-engine 0.9.0 — imports none of the above; HTTP MCP only
SergeyMalashenko/pynspd — reference fork; the runtime resolves pynspd from PyPI
```

Runtime topology after bootstrap:

```text
Hermes / CLI
    |
    v
terralogic-mcp :8004 ----HTTP MCP----> pynspd-mcp :8001
    |                                    |     \
    |---> pyosm-mcp  :8002               |      \
    |---> py2gis-mcp :8003               |       \
    |---> pyrgis-mcp :8005               |        \
    |                                 shared GEODOCS_HOME (SQLite, WAL)
    v                                 = one document cache for both services
terralogic-view :8501 (read-only Streamlit viewer over the case store)
```

One-time deployment from scratch (any machine with `git`, `uv`, and SSH access
to GitHub):

```bash
git clone git@github.com:SergeyMalashenko/terralogic-engine.git
cd terralogic-engine
scripts/bootstrap-workspace.sh          # clones siblings, uv sync --all-extras in dependency order,
                                        # smoke-checks every venv; use --pull to refresh existing checkouts
GEODOCS_HOME=~/.geodocs scripts/run-local-stack.sh --engine
```

For a full-workspace deploy from one clone, prefer the `TerraLogicX`
meta-repository (all components as pinned submodules + `Makefile`):
`git clone --recurse-submodules git@github.com:SergeyMalashenko/TerraLogicX.git && cd TerraLogicX && make up`.

Two non-obvious requirements baked into the scripts:

- every agent is synced with `uv sync --all-extras` — without the extras the
  `mcp` (and the engine's `viewer`) optional dependencies are missing and the
  `*-mcp` / `terralogic-view` entry points fail with `ImportError`;
- `GEODOCS_HOME` must name one shared directory for `pynspd-mcp` and
  `pyrgis-mcp` — it is the common document cache, so both services must see
  the same path.

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

## Local stack

The full system is this engine plus four independent source services
(`pynspd-mcp`, `pyosm-mcp`, `py2gis-mcp`, `pyrgis-mcp`; the last one covers
cadastral region 50 only). Each service is a separate MCP server; the engine
reaches them over HTTP and imports none of their code. A launcher starts the
whole stack from sibling checkouts:

```bash
# pynspd :8001, pyosm :8002, py2gis :8003, pyrgis :8005
GEODOCS_HOME=~/.geodocs scripts/run-local-stack.sh
# the same plus terralogic-mcp on :8004
GEODOCS_HOME=~/.geodocs scripts/run-local-stack.sh --engine
```

| Service | Port | Needs |
|---|---|---|
| pynspd-mcp | 8001 | — |
| pyosm-mcp | 8002 | — |
| py2gis-mcp | 8003 | `PY2GIS_API_KEY` in `py2gis-agents/.env` |
| pyrgis-mcp | 8005 | `GEODOCS_HOME` for the document contour |
| terralogic-mcp | 8004 | `--store` for the case store |

`GEODOCS_HOME` (default `~/.geodocs`) is the shared document store used by
`pyrgis-mcp` and `pynspd-mcp`: documents are downloaded once per municipality
and reused across parcels. Both services must see the same directory.

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
- Documents, when the source services use the shared `GEODOCS_HOME`:
  `pyrgis-mcp` discovers and downloads urban-planning documents (PZZ,
  general plans, GPZU) into the shared store and extracts zone VRI tables;
  `pynspd-mcp` registers the legal acts behind ZOUIT zones and their regimes.
  Document failures degrade the run to `partial` instead of failing it, and
  the extracted rows surface in the report context (`documents` section).

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
