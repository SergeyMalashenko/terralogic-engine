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
- source (`nspd`, `osm`, or `dgis`);
- functional block or canonical feature class;
- visible map layers. The source snapshots belonging to the run are shown in
  the provenance tab.

The main area contains:

1. a summary by source and canonical feature class;
2. an interactive map with the parcel, shared analysis circle, NSPD
   restrictions, OSM contours/lines, and 2GIS infrastructure points;
3. a filtered feature table with distance, relation, category, and an attribute
   panel for the selected object;
4. snapshot provenance, adapter versions, warnings, errors, and feature counts;
5. a run timeline showing `complete`, `partial`, and `failed` collections.

The map uses separate switchable layers for forests, lakes, rivers, streams,
roads, restrictions, and each 2GIS category. Polygon holes stored in forest,
lake, and river contours remain visible because geometry is passed to Folium as
full GeoJSON rather than reduced to bounding boxes or centroids.

## Data access

The viewer depends on the `CaseStore` interface and uses methods such as
`list_cases`, `list_snapshots`, `get_area_of_interest`, and `load_features`.
It does not issue source requests, edit SQLite directly, or write analytics
results.

Geometry is converted from stored WKB by `LocalCaseStore` and passed to Folium
as GeoJSON. The current MVP loads the geometries referenced by the selected
collection receipt. Bounding-box pagination can be added when real case sizes
require it.

## Why this option

- Streamlit provides filters, tables, status panels, and caching with little UI
  infrastructure.
- Folium supports GeoJSON, layer controls, popups, styling, and raster tile
  backgrounds.
- Both can remain optional dependencies, so the acquisition service stays
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
