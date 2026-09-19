#!/usr/bin/env bash
set -euo pipefail
source "$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"
cd "$PROJECT_DIR"

echo -e "${CYAN}Run Client${NC}"

enable_debug=false
use_cached_gradle_config=false
mode="${RUNCLIENT_PRESET_MODE:-}"

if [ -z "$mode" ]; then
    echo "Select mode:"
    mode=0
    select_option "Normal" "Debug" "Cached Gradle config (offline)" || mode=$?
fi

case "$mode" in
    0)
        ;;
    1)
        enable_debug=true
        ;;
    2)
        use_cached_gradle_config=true
        ;;
    *)
        echo -e "${RED}Invalid RUNCLIENT_PRESET_MODE: ${mode}. Expected 0-2.${NC}"
        exit 1
        ;;
esac

java_suffix="$(select_java_task_suffix)" || exit 1
RUN_TASK="runClient${java_suffix}"

if [ -z "$java_suffix" ]; then
    echo -e "${GREEN}Using Gradle task ${RUN_TASK} (Java 8)${NC}"
else
    echo -e "${GREEN}Using Gradle task ${RUN_TASK} (Java ${java_suffix})${NC}"
fi

GRADLE_OPTIONS=()
GRADLE_ARGS=()
CLIENT_JVM_ARGS=()

if $use_cached_gradle_config; then
    GRADLE_OPTIONS+=(--offline --configuration-cache)
    echo -e "${YELLOW}Using cached Gradle configuration and dependencies (offline)${NC}"
fi

read -p "Enter a username (default: Developer): " USERNAME
USERNAME=${USERNAME:-Developer}
GRADLE_ARGS+=(--username="$USERNAME")

if $enable_debug; then
    DEFAULT_DEBUG_PORT="${CLIENT_DEBUG_PORT:-5005}"
    DEBUG_SUSPEND="${CLIENT_DEBUG_SUSPEND:-n}"
    DEBUG_PROMPT="${CLIENT_DEBUG_PROMPT:-1}"

    if [ "$DEBUG_PROMPT" = "0" ] || [ "$DEBUG_PROMPT" = "false" ]; then
        DEBUG_PORT="$DEFAULT_DEBUG_PORT"
    else
        read -p "Debug port (default: ${DEFAULT_DEBUG_PORT}): " DEBUG_PORT
        DEBUG_PORT=${DEBUG_PORT:-$DEFAULT_DEBUG_PORT}
    fi

    CLIENT_JVM_ARGS+=("-agentlib:jdwp=transport=dt_socket,server=y,suspend=${DEBUG_SUSPEND},address=*:${DEBUG_PORT}")
    echo -e "${YELLOW}Client debug enabled on port ${DEBUG_PORT} (suspend=${DEBUG_SUSPEND})${NC}"
fi

if recover_hyprland_environment; then
    echo -e "${GREEN}Using Hyprland session ${HYPRLAND_INSTANCE_SIGNATURE} (${WAYLAND_DISPLAY}, DISPLAY=${DISPLAY:-unset})${NC}"
fi

awt_arg="$(client_awt_wayland_arg "$java_suffix")"
if [ -n "$awt_arg" ]; then
    CLIENT_JVM_ARGS+=("$awt_arg")
    echo -e "${YELLOW}X11 is unavailable; using native Wayland for Swing windows (requires JetBrains Java 21/25).${NC}"
fi
if [ "${#CLIENT_JVM_ARGS[@]}" -gt 0 ]; then
    GRADLE_ARGS+=(--mcJvmArgs="${CLIENT_JVM_ARGS[*]}")
fi

echo -e "${CYAN}Launching client...${NC}"
./gradlew ${GRADLE_OPTIONS[@]+"${GRADLE_OPTIONS[@]}"} "$RUN_TASK" ${GRADLE_ARGS[@]+"${GRADLE_ARGS[@]}"}
