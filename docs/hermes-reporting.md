# Hermes land-parcel reports

## Process topology

```text
Hermes
  |
  | MCP http://127.0.0.1:8004/mcp
  v
terralogic-mcp
  |-- pynspd-mcp  http://127.0.0.1:8001/mcp
  |-- pyosm-mcp   http://127.0.0.1:8002/mcp
  `-- py2gis-mcp  http://127.0.0.1:8003/mcp
        |
        v
  Local CaseStore
```

The model receives compact receipts and `ReportContext` separately from an
immutable `ReportTemplate`. Source GeoJSON, raw provider responses,
calculations, and generated Markdown stay in CaseStore.

`ReportContext` is the factual entity. `ReportTemplate` is an independent
format entity with its own `template_id`, version, required sections,
generation rules, Markdown skeleton, and SHA-256. A generated report records
references to both the analysis and exact template version.

## Start the server

Update and install the package in the Python environment used by the service:

```bash
cd ~/TerraLogicX/terralogic-engine
git switch main
git pull --ff-only
python -m pip uninstall terralogic-acquisition  # once, when migrating
python -m pip install -e '.[mcp,viewer]'
```

Make sure `pynspd-mcp`, `pyosm-mcp`, and `py2gis-mcp` are already running,
then start the aggregate server:

```bash
terralogic-mcp \
  --transport streamable-http \
  --host 127.0.0.1 \
  --port 8004 \
  --store ~/TerraLogicX/case-store \
  --nspd-url http://127.0.0.1:8001/mcp \
  --osm-url http://127.0.0.1:8002/mcp \
  --dgis-url http://127.0.0.1:8003/mcp
```

The server uses stateless Streamable HTTP by default. Its source services must
already be reachable from the same host.

## Verify with MCP Inspector

List tools:

```bash
npx -y @modelcontextprotocol/inspector \
  --cli http://127.0.0.1:8004/mcp \
  --transport http \
  --method tools/list
```

Prepare a case:

```bash
npx -y @modelcontextprotocol/inspector \
  --cli http://127.0.0.1:8004/mcp \
  --transport http \
  --method tools/call \
  --tool-name terralogic_prepare_case \
  --tool-args-json '{
    "cadastral_number": "52:24:0000000:2216",
    "margin_m": 1000,
    "refresh_policy": "if_stale"
  }'
```

Use the returned `case_id` and `collection_run_id` to inspect the context:

```bash
npx -y @modelcontextprotocol/inspector \
  --cli http://127.0.0.1:8004/mcp \
  --transport http \
  --method tools/call \
  --tool-name terralogic_get_report_context \
  --tool-args-json '{
    "case_id": "case-52-24-0000000-2216",
    "collection_run_id": "run-..."
  }'
```

Load the independent template:

```bash
npx -y @modelcontextprotocol/inspector \
  --cli http://127.0.0.1:8004/mcp \
  --transport http \
  --method tools/call \
  --tool-name terralogic_get_report_template \
  --tool-args-json '{
    "template_id": "full_land_report",
    "template_version": "1.0"
  }'
```

## Hermes configuration

Add one Streamable HTTP server using the same format as the existing entries:

```yaml
terralogic-http:
  enabled: true
  transport: http
  url: http://127.0.0.1:8004/mcp
```

Keep the source processes running, but disable their direct Hermes entries for
the report-generation workflow:

```yaml
pynspd-http:
  enabled: false

pyosm-http:
  enabled: false

py2gis-http:
  enabled: false
```

Test discovery:

```bash
hermes mcp test terralogic-http
```

Expected result: a successful connection and exactly four discovered tools.

## Prompt

```text
Подготовь полный отчёт по земельному участку 52:24:0000000:2216.

Порядок работы:
1. Вызови terralogic_prepare_case с margin_m=1000.
2. Передай полученные case_id и collection_run_id в
   terralogic_get_report_context.
3. Получи full_land_report версии 1.0 через
   terralogic_get_report_template.
4. Заполни markdown_skeleton только по данным report context.
5. Не вычисляй самостоятельно площади, проценты и расстояния.
6. Не интерпретируй «не найдено в области поиска» как отсутствие на местности.
7. Не меняй обязательные заголовки и не оставляй заполнители {{ ... }}.
8. Сохрани полный Markdown через terralogic_save_report, указав
   collection_run_id, model_name, template_id и template_version.
9. Верни краткое резюме и report_id.
```

## Stored result

Markdown is stored under:

```text
case-store/cases/<case_id>/reports/report-<uuid>.md
```

The artifact row records the collection run, analytics identifier, report
SHA-256, template identity/version/SHA-256, generation time, and optional model
name. Open the viewer, select the same collection run, and use the `Отчёт` tab
to read or download the newest Markdown report.
