#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

ENV_FILE="$TOOLS_DIR/.env"

if [[ -f "$ENV_FILE" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

LOCAL_PATHS="${DEPLOY_LOCAL_PATHS:-}"
if [[ -z "${LOCAL_PATHS//[[:space:];]/}" ]]; then
    echo -e "${RED}ERROR: set DEPLOY_LOCAL_PATHS in .env to your mods directories (separated by semicolons).${NC}" >&2
    exit 1
fi

GRADLE_PROPS="$PROJECT_DIR/gradle.properties"
if [[ ! -f "$GRADLE_PROPS" ]]; then
    echo -e "${RED}ERROR: gradle.properties not found in $PROJECT_DIR${NC}" >&2
    exit 1
fi

MOD_ID="$(grep -E '^modId[[:space:]]*=' "$GRADLE_PROPS" | head -1 | sed -E 's/^modId[[:space:]]*=[[:space:]]*//' | tr -d '\r')"

if [[ -z "$MOD_ID" ]]; then
    echo -e "${RED}ERROR: could not determine modId from gradle.properties${NC}" >&2
    exit 1
fi

VERSION="${VERSION:-testerino}"
ARTIFACT="build/libs/${MOD_ID}-${VERSION}.jar"

if [[ ! -f "$PROJECT_DIR/$ARTIFACT" ]]; then
    echo -e "${RED}ERROR: artifact not found: $PROJECT_DIR/$ARTIFACT${NC}" >&2
    echo -e "${YELLOW}Build the mod first (with VERSION=$VERSION).${NC}" >&2
    exit 1
fi

IFS=';' read -r -a TARGETS <<< "$LOCAL_PATHS"

for target in "${TARGETS[@]}"; do
    [[ -z "$target" ]] && continue
    if [[ ! -d "$target" ]]; then
        echo -e "${YELLOW}Skipping (not a directory): $target${NC}" >&2
        continue
    fi
    echo -e "${GREEN}Deploying $ARTIFACT to $target${NC}"
    cp "$PROJECT_DIR/$ARTIFACT" "$target/"
done

echo -e "${GREEN}Done!${NC}"
