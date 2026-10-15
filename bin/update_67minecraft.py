#!/usr/bin/env python3
"""
Update 67Minecraft project description from transform_readme.py output.

67Minecraft is a custom Modrinth instance hosted at
https://67minecraft.com/.

Usage:
    python update_67minecraft.py
    python update_67minecraft.py --project-id my_project
    python update_67minecraft.py -p AABBCCDD

If --project-id is not specified, reads 67minecraftProjectId from
gradle.properties. Requires MINECRAFT67_TOKEN in .env file in the
ModdedTools directory.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

import requests


API_BASE = "https://67minecraft.com/api/v2"
GRADLE_PROP = "67minecraftProjectId"
ENV_TOKEN_KEY = "MINECRAFT67_TOKEN"


def load_env_file() -> dict[str, str]:
    """Load environment variables from .env file in the tools directory."""
    env_file = Path(__file__).resolve().parent.parent / ".env"
    env_vars = {}

    if not env_file.is_file():
        return env_vars

    with open(env_file, "r") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                # Remove surrounding quotes if present
                if (value.startswith('"') and value.endswith('"')) or \
                   (value.startswith("'") and value.endswith("'")):
                    value = value[1:-1]
                env_vars[key] = value

    return env_vars


def get_project_id_from_gradle() -> str | None:
    """
    Extract 67Minecraft project ID from gradle.properties in current directory.

    Looks for a line like: 67minecraftProjectId = my_project
    """
    gradle_props = Path("gradle.properties")
    if not gradle_props.is_file():
        return None

    with open(gradle_props, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith(GRADLE_PROP):
                # Handle both "key = value" and "key=value" formats
                if "=" in line:
                    value = line.split("=", 1)[1].strip()
                    return value if value else None
    return None


def get_readme_markdown() -> str:
    """Run transform_readme.py and capture its stdout."""
    transform_script = Path(__file__).resolve().with_name("transform_readme.py")
    result = subprocess.run(
        [sys.executable, str(transform_script)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def update_67minecraft_description(project_id: str, markdown_body: str, token: str) -> requests.Response:
    """Send PATCH request to update 67Minecraft project description."""

    session = requests.Session()

    headers = {
        "Authorization": token,
        "User-Agent": "ReadmeUpdator/1.0 (https://github.com/user/ReadmeUpdator)",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    json_data = {
        "body": markdown_body,
    }

    url = f"{API_BASE}/project/{project_id}"

    response = session.patch(url, headers=headers, json=json_data)

    return response


def main():
    parser = argparse.ArgumentParser(
        description="Update 67Minecraft project description from transform_readme.py output",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
    %(prog)s
    %(prog)s --project-id my_project
    %(prog)s -p AABBCCDD --dry-run

If --project-id is not specified, the script will try to read it from
gradle.properties in the current directory ({GRADLE_PROP} = ...).

Requires {ENV_TOKEN_KEY} in .env file in the ModdedTools directory.
        """,
    )
    parser.add_argument(
        "-p", "--project-id",
        type=str,
        default=None,
        help=f"67Minecraft project ID or slug (default: read from gradle.properties)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the markdown that would be sent without making the request",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Print verbose output",
    )

    args = parser.parse_args()

    # Load .env file
    env_vars = load_env_file()

    # Get 67Minecraft token
    token = env_vars.get(ENV_TOKEN_KEY) or os.environ.get(ENV_TOKEN_KEY)
    if not token and not args.dry_run:
        print(f"Error: {ENV_TOKEN_KEY} not found in .env file or environment", file=sys.stderr)
        print(f"Create a .env file with: {ENV_TOKEN_KEY}=your_token_here", file=sys.stderr)
        print("Get your token from: https://67minecraft.com/settings/pats", file=sys.stderr)
        sys.exit(1)

    # Get project ID from args or gradle.properties
    project_id = args.project_id
    if project_id is None:
        project_id = get_project_id_from_gradle()
        if project_id is None:
            print(f"Error: No project ID specified and could not find {GRADLE_PROP} in gradle.properties", file=sys.stderr)
            sys.exit(1)
        if args.verbose:
            print(f"Using project ID '{project_id}' from gradle.properties", file=sys.stderr)

    # Get markdown from transform_readme.py
    if args.verbose:
        print("Running transform_readme.py...", file=sys.stderr)

    try:
        md_text = get_readme_markdown()
    except subprocess.CalledProcessError as e:
        print(f"Error running transform_readme.py: {e}", file=sys.stderr)
        print(f"stderr: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("Error: transform_readme.py not found in PATH", file=sys.stderr)
        sys.exit(1)

    if args.verbose:
        print(f"Got {len(md_text)} characters of markdown", file=sys.stderr)

    if args.dry_run:
        print("=== Markdown Output (dry run) ===")
        print(md_text)
        print("=== End Markdown Output ===")
        return

    # Update 67Minecraft
    if args.verbose:
        print(f"Updating project '{project_id}'...", file=sys.stderr)

    response = update_67minecraft_description(project_id, md_text, token)

    if response.status_code == 204:
        print(f"Successfully updated project '{project_id}'")
    elif response.ok:
        print(f"Successfully updated project '{project_id}' (status: {response.status_code})")
        if args.verbose:
            print(f"Response: {response.text}", file=sys.stderr)
    else:
        print(f"Error updating project: {response.status_code} {response.reason}", file=sys.stderr)
        try:
            error_data = response.json()
            print(f"Error: {error_data.get('error', 'Unknown')}", file=sys.stderr)
            print(f"Description: {error_data.get('description', response.text)}", file=sys.stderr)
        except Exception:
            print(f"Response body: {response.text}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
