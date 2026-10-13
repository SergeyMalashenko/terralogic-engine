#!/usr/bin/env bash
# Hermes CLI в ролях Report QA (критик и writer) TerraLogicX — gemma3:12b.
#
# В системе два профиля Hermes: ~/.hermes-discovery (документный контур,
# Qwen + tools) и ~/.hermes-critic (этот — Report QA: критик и writer,
# gemma3:12b без tools). Профиль ~/.hermes-critic отключает все toolsets:
# gemma3:12b не поддерживает tools на стороне Ollama, а ролям QA
# инструменты не нужны — контекст передаётся промптом (read-only по
# построению, §69 ТЗ).
#
# Использование (env terralogic-engine):
#   TERRALOGIC_CRITIC_CMD="<путь>/hermes-qa.sh"
#   TERRALOGIC_WRITER_CMD="<путь>/hermes-qa.sh"
# Промпт передаётся последним аргументом (значением hermes -z).
set -euo pipefail

export HERMES_HOME="${TERRALOGIC_QA_HOME:-$HOME/.hermes-critic}"
# Локальной Ollama ключ не нужен, но Hermes требует переменную в конфиге.
export HERMES_CUSTOM_127_0_0_1_11434_API_KEY="${HERMES_CUSTOM_127_0_0_1_11434_API_KEY:-dummy}"

exec hermes -z "$1"
