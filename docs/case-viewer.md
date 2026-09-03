# TerraLogic Case Explorer

## Implementation

The first CaseStore viewer is implemented as a read-only Streamlit application
with Folium. It can run beside Hermes on the same server and requires no
database or frontend service beyond the local Python process.

Command:

```bash
terralogic-view --store ./case-store --host 127.0.0.1 --port 8501
```

## First screen

The sidebar selects:

- case;
- collection run;
- source (`nspd`, `osm`, `dgis`, or `rgis` when collected);
- functional block or canonical feature class;
- visible map layers. The source snapshots belonging to the run are shown in
  the provenance tab.

The main area contains:

1. a summary by source and canonical feature class;
2. four analytics tables tied to the selected collection run;
3. the newest Hermes-generated Markdown report with a download button;
4. an interactive map with the parcel, shared analysis circle, NSPD and RGIS
   restrictions/zones, OSM contours/lines, and 2GIS infrastructure points;
5. a filtered feature table with distance, relation, category, and an attribute
   panel for the selected object;
6. snapshot provenance, adapter versions, warnings, errors, and feature counts;
7. a run timeline showing `complete`, `partial`, and `failed` collections.

The analytics tab is read-only and displays a result previously created with:

```bash
terralogic-analyze <case_id> --run-id <run_id> --store ./case-store
```

Its tables show per-zone ZOUIT intersection area and relative coverage,
non-double-counted forest/water intersection summaries, shortest distances to
2GIS social categories, and shortest distances to OSM forests and water
resources. Stream geometry is linear, so its table cell contains length inside
the parcel rather than a misleading area. Every absent nearest object is shown
explicitly as `Не найден в области поиска`.

The map uses separate switchable layers for forests, waterbodies, rivers,
streams, roads, restrictions, and each 2GIS category. Polygon holes stored in
forest, waterbody, and river contours remain visible because geometry is passed
to Folium as full GeoJSON rather than reduced to bounding boxes or centroids.

Natural contours use dedicated fill styles and an even-odd fill rule: forests
are green, waterbodies blue, and river polygons cyan. Untyped OSM water areas
(`natural=water` without `water=*`) are retained and shown as an unspecified
waterbody; a known `water=*` subtype is included in the tooltip and object
table. The map tab also shows contour counts, the number of preserved interior
rings, a legend, hover highlighting, and a full-screen control. Streams and
roads remain visually distinct line layers.

Roads are split into switchable layers by their exact OSM `highway` value.
Motorways, trunk, primary, secondary, tertiary, unclassified, residential,
living-street, service, and track roads have dedicated colors and widths; track
roads are dashed. Unknown values use a neutral fallback instead of being
discarded. The legend, tooltip, object table, and road summary show both the
localized class name and the original `highway` value.

Permanent road labels are limited to `motorway`, `trunk`, `primary`,
`secondary`, and `tertiary`. The label shows the OSM road name and reference;
if only the reference is available, it is shown alone. Reference tags are
checked in the order `ref`, `official_ref`, `nat_ref`, `int_ref`. Lower road
classes keep their colors and tooltips but do not receive permanent text.

2GIS points can display permanent text labels controlled by the sidebar option
`Подписи объектов 2GIS`. A label is created only when the object has a non-empty
`name`; addresses, categories, and other fields are not substituted. Text is
normalized and limited to 60 characters. The label stays in the same
switchable category layer as its point, so hiding the layer also hides its text.

## Data access

The viewer depends on the `CaseStore` interface and uses methods such as
`list_cases`, `list_snapshots`, `get_area_of_interest`, `load_features`, and
`get_analysis_result`. It does not issue source requests, edit SQLite directly,
or calculate/write analytics results.

Geometry is converted from stored WKB by `LocalCaseStore` and passed to Folium
as GeoJSON. The current MVP loads the geometries referenced by the selected
collection receipt. Bounding-box pagination can be added when real case sizes
require it.

## Why this option

- Streamlit provides filters, tables, status panels, and caching with little UI
  infrastructure.
- Folium supports GeoJSON, layer controls, popups, styling, and raster tile
  backgrounds.
- Both can remain optional dependencies, so the core engine stays
  lightweight.
- The viewer can later be replaced by a separate API and web frontend without
  changing CaseStore.

## Alternatives

- **QGIS export** is useful for expert GIS inspection, but requires a GeoPackage
  export because the MVP SQLite database is not SpatiaLite.
- **Datasette** is excellent for tables and provenance but needs a custom map
  plugin for WKB geometry.
- **Kepler.gl** handles large interactive datasets well but is less convenient
  for case status, snapshot metadata, and domain-specific panels.

For the MVP, Streamlit + Folium offers the best balance between map inspection
and operational diagnostics.
