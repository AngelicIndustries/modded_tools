#!/usr/bin/env bash
# Shared functions and constants for ModdedTools scripts

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Resolve TOOLS_DIR (the ModdedTools root) from this script's location
TOOLS_DIR="$(cd -P "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$TOOLS_DIR/venv/bin/python3"
CACHE_DIR="$TOOLS_DIR/cache"
# PROJECT_DIR defaults to cwd; override with env var if needed
PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"

# Interactive arrow-key menu. Returns selected index via $?.
# Usage: select_option "opt1" "opt2" "opt3"; choice=$?
# All UI output goes to /dev/tty so the menu works inside $() command substitutions.
function select_option {
    local ESC key idx selected option_count
    ESC=$(printf "\033")

    option_count=$#
    selected=0

    # Reserve lines for the menu once, then redraw in place.
    for _ in "$@"; do
        printf "\n" >/dev/tty
    done

    trap 'printf "%b[?25h\n" "'"$ESC"'" >/dev/tty; stty echo; exit' INT
    printf "%b[?25l" "$ESC" >/dev/tty

    while true; do
        # Move up by number of options and redraw all lines.
        printf "%b[%dA" "$ESC" "$option_count" >/dev/tty

        idx=0
        for option in "$@"; do
            if [ "$idx" -eq "$selected" ]; then
                # Clear line, then print highlighted option
                printf "%b[2K  %b[7m %s %b[27m\n" "$ESC" "$ESC" "$option" "$ESC" >/dev/tty
            else
                printf "%b[2K   %s\n" "$ESC" "$option" >/dev/tty
            fi
            idx=$((idx + 1))
        done

        # Read up/down/enter from terminal
        IFS= read -rsn3 key </dev/tty 2>/dev/null || key=""

        if [[ "$key" == "$ESC[A" ]]; then
            selected=$((selected - 1))
            if [ "$selected" -lt 0 ]; then
                selected=$((option_count - 1))
            fi
        elif [[ "$key" == "$ESC[B" ]]; then
            selected=$((selected + 1))
            if [ "$selected" -ge "$option_count" ]; then
                selected=0
            fi
        else
            break
        fi
    done

    printf "%b[?25h\n" "$ESC" >/dev/tty
    return "$selected"
}

select_java_task_suffix() {
    local prompt="${RUN_JAVA_PROMPT:-1}"
    local version="${RUN_JAVA_VERSION:-}"

    if [ "$prompt" = "0" ] || [ "$prompt" = "false" ]; then
        version="${version:-25}"
    else
        echo "Select Java version:" >/dev/tty
        local java_choice=0
        select_option "Java 25 (default)" "Java 21" "Java 17" "Java 8" || java_choice=$?

        case "$java_choice" in
            0) version="25" ;;
            1) version="21" ;;
            2) version="17" ;;
            3) version="8" ;;
            *) version="25" ;;
        esac
    fi

    if [ -z "$version" ]; then
        version="25"
    fi

    case "$version" in
        25) echo "25" ;;
        21) echo "21" ;;
        17) echo "17" ;;
        8) echo "" ;;
        *)
            echo -e "${RED}Invalid Java version '${version}'. Expected 8, 17, 21, or 25.${NC}" >&2
            return 1
            ;;
    esac
}

client_awt_wayland_arg() {
    # JetBrains Java 21/25 can run Swing directly on Wayland. This avoids
    # dependency-loader crashes when a terminal inherits an unusable DISPLAY.
    case "${1:-}" in 21|25) ;; *) return 0 ;; esac
    case "${RUNCLIENT_AWT_WAYLAND:-auto}" in 0|false) return 0 ;; esac
    case "${JAVA_TOOL_OPTIONS:-} ${_JAVA_OPTIONS:-}" in
        *-Dawt.toolkit.name=*) return 0 ;;
    esac

    local wayland_socket="${WAYLAND_DISPLAY:-}"
    [ -n "$wayland_socket" ] || return 0
    if [[ "$wayland_socket" != /* ]]; then
        [ -n "${XDG_RUNTIME_DIR:-}" ] || return 0
        wayland_socket="$XDG_RUNTIME_DIR/$wayland_socket"
    fi
    [ -S "$wayland_socket" ] || return 0

    if [ -n "${DISPLAY:-}" ]; then
        # Probe the connection, including authorization, without hanging on a
        # stale or remote display. If probing tools are absent, leave it alone.
        command -v timeout >/dev/null 2>&1 || return 0
        command -v xdpyinfo >/dev/null 2>&1 || return 0
        if timeout 2 xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
            return 0
        fi
    fi

    printf '%s\n' '-Dawt.toolkit.name=WLToolkit'
}

recover_hyprland_environment() {
    local user_id default_runtime_dir runtime_dir hypr_root
    local instance_dir candidate lock_file wayland_display
    local x11_socket x11_name

    user_id="$(id -u)"
    default_runtime_dir="/run/user/${user_id}"
    runtime_dir="${XDG_RUNTIME_DIR:-$default_runtime_dir}"

    # Agent/IDE terminals can carry an unrelated XDG_RUNTIME_DIR. Prefer the
    # current user's runtime directory when that is where Hyprland is running.
    if [ ! -d "$runtime_dir/hypr" ] && [ -d "$default_runtime_dir/hypr" ]; then
        runtime_dir="$default_runtime_dir"
    fi

    hypr_root="$runtime_dir/hypr"
    [ -d "$hypr_root" ] || return 1
    export XDG_RUNTIME_DIR="$runtime_dir"

    instance_dir=""
    if [ -n "${HYPRLAND_INSTANCE_SIGNATURE:-}" ]; then
        candidate="$hypr_root/$HYPRLAND_INSTANCE_SIGNATURE"
        if [ -S "$candidate/.socket.sock" ] && [ -r "$candidate/hyprland.lock" ]; then
            instance_dir="$candidate"
        fi
    fi

    # When no usable signature was inherited, select the newest live-looking
    # instance. Hyprland removes these sockets when an instance exits normally.
    if [ -z "$instance_dir" ]; then
        for candidate in "$hypr_root"/*; do
            [ -d "$candidate" ] || continue
            [ -S "$candidate/.socket.sock" ] || continue
            [ -r "$candidate/hyprland.lock" ] || continue

            if [ -z "$instance_dir" ] ||
                [ "$candidate/hyprland.lock" -nt "$instance_dir/hyprland.lock" ]; then
                instance_dir="$candidate"
            fi
        done
    fi

    [ -n "$instance_dir" ] || return 1
    export HYPRLAND_INSTANCE_SIGNATURE="${instance_dir##*/}"

    lock_file="$instance_dir/hyprland.lock"
    wayland_display="$(sed -n '2{s/\r$//;p;}' "$lock_file")"
    if [[ "$wayland_display" =~ ^wayland-[0-9]+$ ]] &&
        [ -S "$runtime_dir/$wayland_display" ]; then
        export WAYLAND_DISPLAY="$wayland_display"
    elif [[ "${WAYLAND_DISPLAY:-}" =~ ^wayland-[0-9]+$ ]] &&
        [ -S "$runtime_dir/$WAYLAND_DISPLAY" ]; then
        wayland_display="$WAYLAND_DISPLAY"
    else
        wayland_display=""
        for candidate in "$runtime_dir"/wayland-*; do
            [ -S "$candidate" ] || continue
            [[ "${candidate##*/}" =~ ^wayland-[0-9]+$ ]] || continue

            if [ -z "$wayland_display" ] ||
                [ "$candidate" -nt "$runtime_dir/$wayland_display" ]; then
                wayland_display="${candidate##*/}"
            fi
        done

        [ -n "$wayland_display" ] || return 1
        export WAYLAND_DISPLAY="$wayland_display"
    fi

    # Legacy Minecraft/LWJGL clients still open through Xwayland.
    if [ -z "${DISPLAY:-}" ]; then
        x11_socket=""
        for candidate in /tmp/.X11-unix/X*; do
            [ -S "$candidate" ] || continue
            x11_name="${candidate##*/}"
            [[ "$x11_name" =~ ^X[0-9]+$ ]] || continue

            if [ -z "$x11_socket" ] || [ "$candidate" -nt "$x11_socket" ]; then
                x11_socket="$candidate"
            fi
        done

        if [ -n "$x11_socket" ]; then
            x11_name="${x11_socket##*/}"
            export DISPLAY=":${x11_name#X}"
        fi
    fi

    if [ -z "${XAUTHORITY:-}" ] && [ -n "${HOME:-}" ] && [ -r "$HOME/.Xauthority" ]; then
        export XAUTHORITY="$HOME/.Xauthority"
    fi
    if [ -z "${DBUS_SESSION_BUS_ADDRESS:-}" ] && [ -S "$runtime_dir/bus" ]; then
        export DBUS_SESSION_BUS_ADDRESS="unix:path=$runtime_dir/bus"
    fi

    export XDG_SESSION_TYPE="wayland"
    export XDG_CURRENT_DESKTOP="Hyprland"
    export XDG_SESSION_DESKTOP="Hyprland"
    export DESKTOP_SESSION="Hyprland"
    export _JAVA_AWT_WM_NONREPARENTING=1
}
