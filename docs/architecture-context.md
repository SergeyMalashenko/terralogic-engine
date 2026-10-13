# TerraLogic Engine — архитектурный контекст для анализа

Документ описывает текущее состояние `terralogic-engine` (v0.9.0) — центрального
сервиса обработки земельных участков в workspace TerraLogicX. Предназначен для
передачи внешнему анализатору (Codex) как самодостаточный контекст.

## 1. Назначение

По кадастровому номеру участка сервис:

1. собирает данные из внешних источников (НСПД, OSM, 2GIS, РГИС МО) через
   независимые MCP-сервисы;
2. складывает неизменяемые (immutable) снапшоты источников в локальный
   CaseStore (SQLite + gzip-файлы);
3. выполняет детерминированную пространственную аналитику (пересечения,
   расстояния) по снапшотам одного collection run;
4. строит клиентский экспресс-отчёт со скорингом (методика 6.1) и сервисный
   отчёт-протокол для разработчиков — полностью детерминированно, без LLM;
5. обогащает сведения нормативным классификатором ВРИ (Приказ Росреестра
   П/0412), также без LLM;
6. предоставляет read-only вьювер кейсов (Streamlit).

Принцип: движок не импортирует код сервисов-источников, работает только по их
HTTP MCP-контрактам. LLM участвует только снаружи (Hermes как оркестратор
вызовов), внутри конвейера — детерминированный код.

## 2. Workspace и runtime-топология

```
Hermes / CLI
    |
    v
terralogic-mcp :8004 ----HTTP MCP----> pynspd-mcp :8001 ─┐
    |---> pyosm-mcp  :8002                               │
    |---> py2gis-mcp :8003                               ├─ shared GEODOCS_HOME
    |---> pyrgis-mcp :8005 ──────────────────────────────┤  (SQLite, WAL) —
    |---> geodocs-mcp :8006 (второй контур документов) ──┘  общий кэш документов
    v
terralogic-view :8501 (read-only Streamlit поверх CaseStore)
```

- `pynspd-agents` (:8001) и `pyrgis-agents` (:8005) редактируемо зависят от
  `geodocs-store` (пакет `geodocs`) — общий SQLite-стор документов (ПЗЗ,
  генпланы, регламенты ЗОУИТ).
- `pyosm-agents` (:8002), `py2gis-agents` (:8003) — без внутренних зависимостей.
- Мета-репозиторий TerraLogicX содержит все компоненты сабмодулями;
  `scripts/bootstrap-workspace.sh` клонирует соседей и делает
  `uv sync --all-extras` в порядке зависимостей; `scripts/run-local-stack.sh`
  поднимает стек (`--engine` — включая движок).
- Обязательно: `GEODOCS_HOME` должен указывать на один каталог для всех
  сервисов (общий кэш документов); sync каждого агента — с `--all-extras`,
  иначе entry points `*-mcp` падают с ImportError.

## 3. Структура пакета `src/terralogic_engine/` (~11.5k строк)

```
domain/models.py        Стабильные доменные контракты: CollectionRequest,
                        CaseInfo, SourceSnapshot, AreaOfInterest, GeoFeature,
                        CaseFact, CollectionReceipt. SourceName =
                        nspd|osm|dgis|rgis|geodocs.

acquisition/            Сбор из источников:
  pipeline.py             AcquisitionPipeline — оркестрация NSPD + OSM + 2GIS
                          (+ опц. RGIS, geodocs): контур участка → минимальный
                          описанный круг + margin (0..10000 м) → конкурентный
                          сбор, снапшоты, факты, receipt.
  profiles.py             Версионируемые CollectionProfile (какие блоки
                          источников собирать); профиль 3.1 включает второй
                          контур документов.
  clients/                HTTP MCP-клиенты (base, mcp).
  normalize.py            Конвертация «конвертов» источников в GeoFeature /
                          CaseFact (классы фич: restriction_zone, ...).
  geodocs_adapter.py      Проекция ответов geodocs-mcp в формат удалённых
                          legacy-инструментов (rgis_get_document_vri,
                          nspd_get_zouit_regimes), чтобы normalize не менять.
  geometry.py             Гео-утилиты сбора.

store/                  CaseStore:
  base.py                 Протокол CaseStore: кейсы, run'ы, снапшоты, AOI,
                          фичи, факты, результаты аналитики, отчёты.
  local.py                SQLite + gzip-файлы на ФС; миграции в
                          store/migrations.

analytics/              Детерминированная пространственная аналитика:
  pipeline.py             Пересечения участка с ЗОУИТ (доли покрытия),
                          ближайшие объекты инфраструктуры; shapely + pyproj;
                          результат — AnalysisResult с sha256 привязкой к run.
  models.py               IntersectionItem/Summary, NearestObject,
                          AnalysisResult.
  cli.py                  `terralogic-analyze`.

reporting/              Сборка отчётного контекста из immutable run:
  context.py              build_report_context — паспорт участка, ЗОУИТ,
                          инфраструктура, градпланирование, документы,
                          доказательства источников.
  models.py               ReportContext и ~25 моделей контекста (вкл.
                          VriReportContext, ParcelReportContext.
                          permitted_use_vri).
  service.py              ReportingService — фасад: prepare_case,
                          prepare_quickreport, get_report_context, save_report;
                          сборка карты/PDF/сервисного отчёта.
  template_registry.py    Версионируемый реестр шаблонов отчётов.

quickreport/            Экспресс-отчёт со скорингом (без LLM):
  context.py              build_quick_context(_from_report) — производные:
                          классификация и слияние зон, факторы, U/A/C/G,
                          пробелы evidence (missing, §34).
  models.py               Контракты контекста + provenance-разметка
                          (FactOrigin SOURCE/DERIVED/DOCUMENT/LEGAL/STATIC,
                          FactState KNOWN/DERIVED/TO_VERIFY, §21/§41).
  model.py                QuickReportModel — продуктовая структура отчёта
                          (§40): identity, score, main_conclusion,
                          established_facts, attention_factors, passport,
                          environment, missing_information, limitations,
                          marketing (presentation layer).
  rules.py                Методика 6.1 из assets/scoring_rules.yaml +
                          zone_guidance.yaml; methodology_sha256();
                          MarketingConfig/load_marketing из marketing.yaml
                          (отдельный sha256, в методику не входит);
                          тексты missing — assets/missing_texts.yaml.
  geometry.py             Проверка размещения тестового контура дома
                          10×10/12×12 м после отступов (п. 4.4).
  render.py               Детерминированный рендер клиентского Markdown
                          из QuickReportModel; render_quickreport — фасад
                          обратной совместимости (контекст → модель).
  service.py              Сервисный отчёт — внутренний протокол скоринга
                          для разработчиков.
  mapimg.py, pdf.py       Карта (Pillow) и PDF (weasyprint) версии отчёта.

legal/                  Нормативный классификатор ВРИ (v1, коммит 2747277):
  importer.py             Offline DOCX→JSON импорт (stdlib: zipfile+xml).
  repository.py           In-memory lookup: по коду, точно по имени, по
                          алиасу без «(хвоста в скобках)».
  service.py              LegalKnowledgeService: resolve_vri(code),
                          match_vri(текст источника) → VriResolution;
                          путь к JSON — env TERRALOGIC_VRI_CLASSIFIER,
                          иначе пакетный legal/data/vri_classifier_2026-06-02.json;
                          без файла — UserWarning, обогащение отключено.
  models.py               VriSource, VriEntry, VriClassifier (валидация:
                          непустые код/имя, уникальность кодов).

qa/                     Report QA (§61–76 ТЗ): проверка отчёта после рендера.
  models.py               QaIssue / ValidatorResult / CriticResult (§73).
  validator.py            DeterministicValidator: балл=модель, статус,
                          площадь, ВРИ=классификатор, имена зон, обязательные
                          секции §23, цены=MarketingConfig, статичный
                          дисклеймер, трассировка факторов к протоколу.
  critic.py               LLM-критик (Hermes+Gemini): промпт с политикой §71,
                          read-only по построению (без инструментов), парсинг
                          === CRITIC_RESULT ===, fail-safe — любой сбой →
                          warning, отчёт сохраняется (§77). Конфигурация —
                          env TERRALOGIC_CRITIC_CMD; без него критик отключён.
                          Итоги — артефакт quickreport-{run}-qa.md в CaseStore.

mcp.py                  FastMCP-сервер `terralogic-mcp` :8004, два инструмента:
                        terralogic_prepare_case(cadastral_number, case_id?,
                        margin_m=1000, refresh_policy=never|if_stale|always)
                        → PrepareCaseResult;
                        terralogic_prepare_quickreport(case_id,
                        collection_run_id?) → QuickReportResult (markdown +
                        карта + PDF + service_markdown). create_reporting_service()
                        — фабрика для офлайн-использования без MCP.

viewer/                 Streamlit read-only вьювер кейсов (app, data, cli).
cli.py                  `terralogic-collect`.
scripts/import_vri_classifier.py   Офлайн-импорт классификатора ВРИ.
legal/sources/…docx                Immutable DOCX приказа П/0412 (ред. 02.06.2026).
```

## 4. Конвейер данных

```
кадастровый номер
  → terralogic_prepare_case
      → AcquisitionPipeline: NSPD (участок, ЗОУИТ) → круг AOI (margin)
        → конкурентно OSM + 2GIS (+ RGIS для региона 50, + geodocs профиль 3.1)
      → снапшоты (gzip), фичи, факты, receipt → CaseStore
      → AnalysisPipeline → AnalysisResult
  → terralogic_prepare_quickreport
      → build_report_context (паспорт, зоны, документы)
      → обогащение ВРИ: LegalKnowledgeService.match_vri(permitted_use)
        → ParcelReportContext.permitted_use_vri (исходный текст сохраняется)
      → build_quick_context: классификация/слияние зон, скоринг,
        геометрический тест конфигурации
      → render: клиентский MD + сервисный MD + карта + PDF → CaseStore
```

Регенерация отчётов из существующих кейс-сторов — офлайн, ~10 с на 24 кейса
(через `create_reporting_service(store_path=...)` без MCP).

## 5. Скоринг (методика 6.1)

- Оценка = max(0; 100 − Σ вычетов групп ограничений (потолок 40/группа) −
  U − G), где U — неизвестность коммуникаций (4/3/3, макс 10),
  G = max(A; C): A — площадь на минимуме регламента (0/5), C — конфигурация
  (0/5/10 по геометрическому тесту).
- Статусы по приоритету: стоп-фактор → «Недостаточно данных» →
  «Требует повышенного внимания» (<85 или вычет группы ≥15) →
  «Можно продолжить рассмотрение».
- Веса, бэнды охвата, пороги, клиентские тексты — в
  `quickreport/assets/scoring_rules.yaml` и `zone_guidance.yaml`; отпечаток
  методики (sha256 обоих файлов) хранится с каждым отчётом.
- Дедупликация зон (коммит e901695): слияние «короткое ⊂ полное имя»
  (токен-префикс; конфликт реестровых номеров запрещает слияние) и удаление
  «эхо»-витрин (имя == zone_type первичной записи, без реестрового номера).
- Продуктовая семантика (итерация по ТЗ рефакторинга): provenance-разметка
  полей (FactOrigin SOURCE/DERIVED/DOCUMENT/LEGAL/STATIC, FactState
  KNOWN/DERIVED/TO_VERIFY), QuickReportModel как отдельный product contract
  (`quickreport/model.py`), блок «Что ещё нужно проверить» из
  детерминированных пробелов evidence (тексты — `assets/missing_texts.yaml`,
  в methodology_sha256 не входит), коммерческий «Следующий шаг» — только из
  `assets/marketing.yaml` (MarketingConfig, отдельный sha256); маркетинг в
  ReportContext не попадает.

## 6. Legal VRI v1 — ключевые решения

- NSPD отдаёт ВРИ текстом, не кодом → матчинг: точный (casefold + пробелы)
  или по алиасу «каноническое имя без хвоста в скобках»; неоднозначные
  алиасы отключаются; нечёткого поиска нет.
- Импортер stdlib-only, обрабатывает особенности DOCX КонсультантПлюс
  (строки номеров граф, ред.-примечания, строки-продолжения, коды 2–4 уровней
  `^\d+(\.\d+){1,3}$`); 172 позиции.
- Источник (DOCX) immutable; runtime работает только с JSON-снимком.
- Обогащение дополняет source fact, не заменяя исходное значение.

## 7. Тесты и качество

- `cd terralogic-engine && .venv/bin/python -m pytest tests/ -q` —
  113 green. Тесты: acquisition pipeline, analytics, case store, legal VRI
  (15), MCP, MCP-клиенты, quickreport (+карта), reporting, viewer; `fakes.py`.
- Ruff: новые файлы чисты; 4 pre-existing ошибки (UP037 models.py:90,
  PYI041 service.py:36, ISC004 service.py ×2).
- Стиль коммитов: `feat(scope): …` / `fix(scope): …` на русском.

## 8. Известные границы и хвосты

- `_target_use_prefix` в скоринге — эвристика префикса ВРИ; замена на
  классификатор осознанно отложена (не сдвигать баллы).
- Иерархия кодов ВРИ (2.0 включает 2.1…) в runtime не используется.
- Правило echo-drop: витрина категории теоретически может включать иной
  объект той же категории — видно по заметкам слияния в сервисном отчёте.
- geodocs-store: ветка `fix/hermes-harness-web-tools` не влита;
  `record_agent_fetch` падает с UNIQUE sha256 при повторной регистрации;
  cntd.ru → 302 на SSO после серии обращений.
- Зависимости: pydantic ≥2.10, pyproj, pyyaml, shapely; extras: mcp, map
  (Pillow), pdf (markdown+weasyprint), viewer (streamlit/folium).
- Python ≥3.11, пакетный менеджер — uv, сборка — hatchling.
