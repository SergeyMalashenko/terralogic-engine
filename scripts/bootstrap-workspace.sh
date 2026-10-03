#!/usr/bin/env bash
# Разворачивает workspace TerraLogicX с нуля.
#
# Использование:
#   scripts/bootstrap-workspace.sh           # клонировать недостающее + собрать venv
#   scripts/bootstrap-workspace.sh --pull    # то же, плюс git pull --ff-only в существующих
#
# Ожидает, что этот checkout terralogic-engine лежит рядом с будущими
# соседними репозиториями (workspace root = родительская директория):
#   <root>/terralogic-engine   <- этот скрипт
#   <root>/geodocs-store pyrgis pyrgis-agents pynspd-agents pyosm-agents py2gis-agents
#
# Порядок сборки повторяет иерархию зависимостей (листья первыми):
#   geodocs-store -> pyrgis -> {pyrgis-agents, pynspd-agents} -> pyosm/py2gis -> engine
# Каждый агент собирается с --all-extras: без этого в venv не попадёт
# optional-dependency "mcp" (и "viewer" у engine), и точки входа *-mcp
# падают с ImportError.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
GH="git@github.com:SergeyMalashenko"

PULL=0
[ "${1:-}" = "--pull" ] && PULL=1

# runtime-контур в порядке зависимостей
RUNTIME_REPOS=(geodocs-store pyrgis pyrgis-agents pynspd-agents pyosm-agents py2gis-agents terralogic-engine)
# форк-референс: runtime берёт пакет pynspd с PyPI, локальный клон не нужен для запуска
REFERENCE_REPOS=(pynspd)

clone_missing() {
    local repo="$1"
    local dir="${ROOT}/${repo}"
    if [ -d "${dir}/.git" ]; then
        if [ "$PULL" -eq 1 ]; then
            git -C "$dir" pull --ff-only
        else
            echo "exists  ${repo}"
        fi
    elif [ -e "$dir" ]; then
        echo "SKIP    ${repo}: ${dir} существует, но это не git-репозиторий" >&2
    else
        echo "clone   ${repo}"
        git clone -q "${GH}/${repo}.git" "$dir"
    fi
}

echo "== workspace root: ${ROOT}"
echo "== clone"
for repo in "${RUNTIME_REPOS[@]}" "${REFERENCE_REPOS[@]}"; do
    [ "$repo" = "terralogic-engine" ] && continue  # мы уже здесь
    clone_missing "$repo"
done

echo "== uv sync --all-extras (порядок зависимостей)"
for repo in "${RUNTIME_REPOS[@]}"; do
    echo "sync    ${repo}"
    (cd "${ROOT}/${repo}" && uv sync --all-extras -q)
done

echo "== smoke-check"
"${ROOT}/geodocs-store/.venv/bin/python" -c "import geodocs"
"${ROOT}/pyrgis/.venv/bin/python" -c "import pyrgis"
"${ROOT}/pyrgis-agents/.venv/bin/python" -c "import geodocs, pyrgis, pyrgis_agents, mcp"
"${ROOT}/pynspd-agents/.venv/bin/python" -c "import geodocs, pynspd, pynspd_agents, mcp"
"${ROOT}/pyosm-agents/.venv/bin/python" -c "import pyosm_agents, mcp"
"${ROOT}/py2gis-agents/.venv/bin/python" -c "import py2gis_agents, mcp"
"${ROOT}/terralogic-engine/.venv/bin/python" -c "import terralogic_engine, mcp, streamlit"
echo "OK      все импорты работают"

cat <<EOF

Workspace готов. Запуск стека:
  cd ${ROOT}/terralogic-engine
  GEODOCS_HOME=~/.geodocs scripts/run-local-stack.sh --engine
    nspd :8001   osm :8002   2gis :8003   rgis :8005   engine :8004

Viewer (из второго терминала):
  cd ${ROOT}/terralogic-engine && .venv/bin/terralogic-view --store case-store --port 8501

Сбор кейса (CLI, без MCP):
  terralogic-collect <кадастровый-номер> --case-id case-<...> --store ./case-store \\
    --nspd-url http://127.0.0.1:8001/mcp --osm-url http://127.0.0.1:8002/mcp \\
    --dgis-url http://127.0.0.1:8003/mcp --rgis-url http://127.0.0.1:8005/mcp --margin-m 1000
EOF
