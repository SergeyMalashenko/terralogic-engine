#!/usr/bin/env bash
# Hermes CLI в роли Report QA critic'а TerraLogicX — gemma3:12b.
#
# В системе три профиля Hermes: ~/.hermes-discovery (документный контур,
# Qwen + tools), ~/.hermes-critic (этот — Report QA critic, gemma3:12b без
# tools) и ~/.hermes-writer (narrative writer QuickReport, Qwen без tools,
# scripts/hermes-writer.sh). Writer больше не использует общий
# critic-профиль: у него отдельный профиль и модель. Профиль
# ~/.hermes-critic отключает все toolsets: gemma3:12b не поддерживает tools
# на стороне Ollama, а критику инструменты не нужны — контекст передаётся
# промптом (read-only по построению, §69 ТЗ).
#
# Использование (env terralogic-engine):
#   TERRALOGIC_CRITIC_CMD="<путь>/hermes-qa.sh"
# Промпт передаётся последним аргументом (значением hermes -z).
set -euo pipefail

export HERMES_HOME="${TERRALOGIC_QA_HOME:-$HOME/.hermes-critic}"
# Локальной Ollama ключ не нужен, но Hermes требует переменную в конфиге.
export HERMES_CUSTOM_127_0_0_1_11434_API_KEY="${HERMES_CUSTOM_127_0_0_1_11434_API_KEY:-dummy}"

exec hermes -z "$1"
