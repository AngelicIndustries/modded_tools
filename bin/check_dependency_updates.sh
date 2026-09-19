#!/usr/bin/env bash
set -euo pipefail
source "$(cd -P "$(dirname "${BASH_SOURCE[0]}")" && pwd)/common.sh"

checker="$TOOLS_DIR/bin/dependency_update_checker.py"
if [ ! -f "$checker" ]; then
    echo -e "${RED}Missing checker script: $checker${NC}"
    exit 1
fi

prompt_yes_no_update() {
    local question="$1"
    local choice=0

    # Prefer the shared arrow-key selector when a controlling TTY is available.
    if [ -t 0 ] && [ -t 1 ] && { true >/dev/tty; } 2>/dev/null; then
        echo "$question"
        select_option "Yes" "No" || choice=$?
        [ "$choice" -eq 0 ]
        return
    fi

    local reply=""
    read -r -p "${question} [y/N]: " reply
    case "$reply" in
        y|Y|yes|YES) return 0 ;;
        *) return 1 ;;
    esac
}

deps_input="${1:-$PROJECT_DIR/dependencies.gradle}"
if [[ "$deps_input" == "~" ]]; then
    deps_input="$HOME"
elif [[ "$deps_input" == "~/"* ]]; then
    deps_input="$HOME/${deps_input#~/}"
fi

if [ -d "$deps_input" ]; then
    deps_input="$deps_input/dependencies.gradle"
fi

deps_file=""
if deps_file="$(cd "$(dirname "$deps_input")" 2>/dev/null && pwd)/$(basename "$deps_input")"; then
    :
else
    deps_file="$deps_input"
fi

if [ ! -f "$deps_file" ]; then
    echo -e "${YELLOW}Could not find dependencies file at:${NC} $deps_file"
    read -r -p "Path to dependencies.gradle: " deps_file
fi

if [ ! -f "$deps_file" ]; then
    echo -e "${RED}dependencies.gradle not found.${NC}"
    exit 1
fi

state_file="$(mktemp "${TMPDIR:-/tmp}/moddedtools-dep-check.XXXXXX.json")"
trap 'rm -f "$state_file"' EXIT

echo -e "${CYAN}Checking dependency updates in ${deps_file}...${NC}"
scan_output="$("$PYTHON" "$checker" scan --deps-file "$deps_file" --state-file "$state_file")"

if [[ "$scan_output" == "NO_UPDATES" ]]; then
    echo -e "${GREEN}No newer versions found via available metadata.${NC}"
    exit 0
fi

selected_ids=()
found_candidates=0
mapfile -t scan_lines <<< "$scan_output"
for line in "${scan_lines[@]}"; do
    [[ "$line" == CANDIDATE\|* ]] || continue
    found_candidates=1

    IFS='|' read -r _ candidate_id ga current_version latest_version occurrence_count best_effort compatibility <<< "$line"
    echo
    echo -e "${CYAN}${ga}${NC}"
    echo "Current: $current_version"
    echo "Latest:  $latest_version"
    echo "Occurrences in file: $occurrence_count"
    if [ -n "$compatibility" ]; then
        echo "Compatibility: $compatibility (Modrinth API)"
    fi
    if [ "$best_effort" = "1" ]; then
        echo -e "${YELLOW}Source type: best-effort metadata (Curse repository)${NC}"
    fi
    if prompt_yes_no_update "Update this dependency?"; then
        selected_ids+=("$candidate_id")
    fi
done

if [ "$found_candidates" -eq 0 ]; then
    echo -e "${YELLOW}Could not parse update candidates from scan output.${NC}"
    exit 1
fi

if [ "${#selected_ids[@]}" -eq 0 ]; then
    echo -e "${YELLOW}No updates selected.${NC}"
    exit 0
fi

ids_csv="$(IFS=,; echo "${selected_ids[*]}")"
apply_output="$("$PYTHON" "$checker" apply --state-file "$state_file" --ids "$ids_csv")"

applied_any=0
while IFS= read -r line; do
    case "$line" in
        APPLIED\|*)
            applied_any=1
            IFS='|' read -r _ candidate_id ga old_version new_version count <<< "$line"
            echo -e "${GREEN}Updated${NC} $ga: $old_version -> $new_version (occurrences: $count)"
            ;;
        UPDATED\|*)
            IFS='|' read -r _ candidate_count occurrence_count updated_file <<< "$line"
            echo -e "${GREEN}Applied ${candidate_count} dependency updates (${occurrence_count} replacements) in ${updated_file}.${NC}"
            ;;
    esac
done <<< "$apply_output"

if [ "$applied_any" -eq 0 ]; then
    echo -e "${YELLOW}No changes were written.${NC}"
fi
