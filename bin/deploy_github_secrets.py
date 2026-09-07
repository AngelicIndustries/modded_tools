#!/usr/bin/env python3
"""Deploy the tool's publishing credentials to a mod's GitHub origin repository."""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
SECRET_NAMES = (
    "MODRINTH_TOKEN",
    "CURSEFORGE_TOKEN",
    "MAVEN_USER",
    "MAVEN_PASSWORD",
    "SIX_SEVEN_TOKEN",
)


def load_settings(env_file: Path) -> dict[str, str]:
    if not env_file.is_file():
        raise ValueError(f"Missing {env_file}. Copy .env.example and fill in the credentials.")

    values = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, value = (part.strip() for part in line.split("=", 1))
        # Parse values as data: CurseForge tokens can contain literal dollar signs.
        if value.startswith(("'", '"')):
            quoted = re.fullmatch(r"(['\"])(.*?)\1(?:\s+#.*)?", value)
            if quoted is None:
                raise ValueError(f"Invalid quoted value for {key} in {env_file}.")
            value = quoted.group(2)
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value

    values["SIX_SEVEN_TOKEN"] = values.get("SIX_SEVEN_TOKEN") or values.get("MINECRAFT67_TOKEN", "")
    required = (*SECRET_NAMES, "MAVEN_URL")
    missing = [name for name in required if not values.get(name, "").strip()]
    if missing:
        raise ValueError(f"Missing values in {env_file}: {', '.join(missing)}")
    settings = {name: values[name] for name in required}
    settings["GITHUB_TOKEN"] = values.get("GITHUB_TOKEN", "").strip()
    return settings


def github_repository(project_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(project_dir), "remote", "get-url", "origin"],
        capture_output=True, text=True,
    )
    if result.returncode:
        raise ValueError(f"{project_dir} must be a Git checkout with an origin remote.")

    match = re.fullmatch(
        r"(?:https?://github\.com/|git@github\.com:|ssh://git@github\.com(?::22)?/)"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/?",
        result.stdout.strip(),
    )
    if match is None:
        raise ValueError("The origin remote must be a GitHub HTTPS or SSH repository URL.")
    # Pin both repository and host, regardless of GH_REPO/GH_HOST or an upstream remote.
    return "github.com/" + match.group(1).removesuffix(".git")


def deploy(values: dict[str, str], repository: str) -> None:
    gh_environment = os.environ.copy()
    if values.get("GITHUB_TOKEN"):
        # GH_TOKEN takes precedence over GITHUB_TOKEN in gh. Override both only
        # for these child processes so the tool's .env selects the credentials.
        gh_environment["GH_TOKEN"] = values["GITHUB_TOKEN"]
        gh_environment["GITHUB_TOKEN"] = values["GITHUB_TOKEN"]
    result = subprocess.run(
        ["gh", "auth", "status", "--active", "--hostname", "github.com"],
        capture_output=True, text=True, env=gh_environment,
    )
    if result.returncode:
        raise ValueError(
            "GitHub authentication failed. Check GITHUB_TOKEN in the tool's .env, "
            "or leave it empty and run gh auth login --hostname github.com."
        )

    print(f"Deploying GitHub Actions settings to {repository}...", flush=True)
    settings = [("secret", name, values[name]) for name in SECRET_NAMES]
    settings.append(("variable", "MAVEN_PUBLISHING_URL", values["MAVEN_URL"]))
    for kind, name, value in settings:
        command = ["gh", kind, "set", name, "--repo", repository]
        if kind == "secret":
            command.extend(["--app", "actions"])
        result = subprocess.run(
            command, input=value, capture_output=True, text=True, env=gh_environment,
        )
        if result.returncode:
            raise ValueError(
                f"Failed to set {kind} {name} (gh exited {result.returncode}). "
                "Check GitHub connectivity and permission to manage Actions secrets and variables. "
                "Earlier successful updates remain applied; rerun after fixing the error."
            )
        print(f"Set {kind}: {name}", flush=True)
    print("GitHub secrets and variables deployed.")


def main() -> int:
    try:
        values = load_settings(ENV_FILE)
        for command in ("git", "gh"):
            if shutil.which(command) is None:
                raise ValueError(f"Required command not found: {command}")
        repository = github_repository(Path(os.environ.get("PROJECT_DIR") or Path.cwd()))
        deploy(values, repository)
    except (OSError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
