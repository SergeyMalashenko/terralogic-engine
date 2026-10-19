#!/usr/bin/env bash
# Hermes CLI в роли narrative-writer'а QuickReport (блок «Что уже удалось
# установить») — Qwen без tools, отдельный профиль.
#
# В системе три профиля Hermes: ~/.hermes-discovery (документный контур,
# Qwen + tools), ~/.hermes-critic (Report QA critic, gemma3:12b без tools,
# scripts/hermes-qa.sh) и этот — ~/.hermes-writer (narrative writer,
# qwen3.5:9b-q4_K_M без tools). Writer получает bounded input промптом и
# не нуждается в инструментах; --ignore-rules отсекает профильные
# SOUL/AGENTS/memory, чтобы они не влияли на юридически значимый текст;
# --reasoning none отключает reasoning-режим.
#
# Использование (env terralogic-engine):
#   TERRALOGIC_WRITER_CMD="<путь>/hermes-writer.sh"
#   TERRALOGIC_WRITER_HOME=...     # профиль (default ~/.hermes-writer)
#   TERRALOGIC_WRITER_MODEL=...    # модель (default qwen3.5:9b-q4_K_M)
#   TERRALOGIC_WRITER_USAGE_FILE=1 # executor подставит путь usage-файла
# Промпт передаётся последним аргументом (значением hermes -z).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export HERMES_HOME="${TERRALOGIC_WRITER_HOME:-$HOME/.hermes-writer}"

# Первый запуск: инициализация профиля из checked-in template.
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
  mkdir -p "$HERMES_HOME"
  cp "$SCRIPT_DIR/hermes-writer.config.yaml" "$HERMES_HOME/config.yaml"
fi

# Локальной Ollama ключ не нужен, но Hermes требует переменную в конфиге.
export HERMES_CUSTOM_127_0_0_1_11434_API_KEY="${HERMES_CUSTOM_127_0_0_1_11434_API_KEY:-dummy}"

MODEL="${TERRALOGIC_WRITER_MODEL:-qwen3.5:9b-q4_K_M}"
ARGS=(-m "$MODEL" --reasoning none --ignore-rules)
if [ -n "${TERRALOGIC_WRITER_USAGE_FILE:-}" ]; then
  ARGS+=(--usage-file "$TERRALOGIC_WRITER_USAGE_FILE")
fi

exec hermes "${ARGS[@]}" -z "$1"
