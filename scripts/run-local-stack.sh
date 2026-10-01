#!/usr/bin/env bash
# Поднимает локальный стек сервисов для terralogic-engine.
#
# Использование:
#   scripts/run-local-stack.sh            # источники: :8001 :8002 :8003 :8005
#   scripts/run-local-stack.sh --engine   # + terralogic-mcp на :8004
#
# Ожидает раскладку соседних checkout'ов:
#   <root>/pynspd-agents  pyosm-agents  py2gis-agents  pyrgis-agents  terralogic-engine
#
# GEODOCS_HOME (по умолчанию ~/.geodocs) — общая база документов pyrgis-mcp и
# pynspd-mcp; оба сервиса обязаны видеть один каталог.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
export GEODOCS_HOME="${GEODOCS_HOME:-$HOME/.geodocs}"
mkdir -p "$GEODOCS_HOME"

WITH_ENGINE=0
[ "${1:-}" = "--engine" ] && WITH_ENGINE=1

pids=()

# start <каталог> <команда> [аргументы...]
start() {
    local dir="$1"; shift
    local runner
    if [ -x "${ROOT}/${dir}/.venv/bin/$1" ]; then
        runner="${ROOT}/${dir}/.venv/bin/$1"
    else
        runner="uv run --directory ${ROOT}/${dir} $1"
    fi
    shift
    # shellcheck disable=SC2068
    (cd "${ROOT}/${dir}" && exec ${runner} $@) &
    pids+=($!)
    echo "started ${dir} (pid $!)"
}

trap 'kill ${pids[@]} 2>/dev/null || true' EXIT

start pynspd-agents pynspd-mcp --transport streamable-http --host 127.0.0.1 --port 8001
start pyosm-agents pyosm-mcp --transport streamable-http --host 127.0.0.1 --port 8002
start py2gis-agents py2gis-mcp --transport streamable-http --host 127.0.0.1 --port 8003
start pyrgis-agents pyrgis-mcp --transport streamable-http --host 127.0.0.1 --port 8005

if [ "$WITH_ENGINE" -eq 1 ]; then
    start terralogic-engine terralogic-mcp \
        --transport streamable-http --host 127.0.0.1 --port 8004 \
        --nspd-url http://127.0.0.1:8001/mcp \
        --osm-url http://127.0.0.1:8002/mcp \
        --dgis-url http://127.0.0.1:8003/mcp \
        --rgis-url http://127.0.0.1:8005/mcp \
        --store ./case-store
fi

cat <<EOF

Stack is starting (GEODOCS_HOME=$GEODOCS_HOME):
  nspd :8001   osm :8002   2gis :8003   rgis :8005$( [ "$WITH_ENGINE" -eq 1 ] && echo '   engine :8004' )
Hermes endpoint: http://127.0.0.1:8004/mcp
Collect a case:
  terralogic-collect <cadastral-number> --store ./case-store \\
    --nspd-url http://127.0.0.1:8001/mcp --osm-url http://127.0.0.1:8002/mcp \\
    --dgis-url http://127.0.0.1:8003/mcp --rgis-url http://127.0.0.1:8005/mcp
Ctrl-C останавливает все сервисы.
EOF

wait
