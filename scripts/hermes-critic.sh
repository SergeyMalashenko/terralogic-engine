#!/usr/bin/env bash
# Hermes CLI в роли QA-критика/writer'а TerraLogicX (gemma3:12b, без tools).
#
# Профиль ~/.hermes-critic отключает все toolsets: gemma3:12b не поддерживает
# tools на стороне Ollama, а роли критика и writer'а инструменты не нужны —
# весь контекст передаётся промптом (read-only по построению, §69 ТЗ).
#
# Использование (env terralogic-engine):
#   TERRALOGIC_CRITIC_CMD="<путь>/hermes-critic.sh"
#   TERRALOGIC_WRITER_CMD="<путь>/hermes-critic.sh"
# Промпт передаётся последним аргументом (значением hermes -z).
set -euo pipefail

export HERMES_HOME="${TERRALOGIC_CRITIC_HOME:-$HOME/.hermes-critic}"
# Локальной Ollama ключ не нужен, но Hermes требует переменную в конфиге.
export HERMES_CUSTOM_127_0_0_1_11434_API_KEY="${HERMES_CUSTOM_127_0_0_1_11434_API_KEY:-dummy}"

exec hermes -z "$1"
