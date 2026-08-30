#!/usr/bin/env python3
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

try:
    ET.fromstring("<x/>")
except ImportError:
    # Broken expat (e.g. Python compiled against newer libexpat than the
    # system provides).  Monkey-patch ET.fromstring with a minimal regex
    # parser – sufficient for the simple Maven metadata XML we handle.
    def _fromstring_fallback(text, parser=None):  # type: ignore[override]
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        text = re.sub(r"<\?xml[^?]*\?>", "", text).strip()
        root = None
        stack: list[ET.Element] = []
        for m in re.finditer(r"<(/?)(\w[\w.\-]*)([^>]*?)(/?)>", text):
            is_close, tag, self_closing = m.group(1) == "/", m.group(2), m.group(4) == "/"
            if not is_close:
                elem = ET.Element(tag)
                if stack:
                    stack[-1].append(elem)
                else:
                    root = elem
                if not self_closing:
                    inner_start = m.end()
                    next_lt = text.find("<", inner_start)
                    if next_lt > inner_start:
                        elem.text = text[inner_start:next_lt].strip() or None
                    stack.append(elem)
            elif stack and stack[-1].tag == tag:
                stack.pop()
        if root is None:
            raise ET.ParseError("no root element")
        return root

    ET.fromstring = _fromstring_fallback  # type: ignore[assignment]
from typing import Callable, Dict, List, Optional, Tuple


DEFAULT_MAVEN_BASES = [
    "https://nexus.gtnewhorizons.com/repository/public",
    "https://repo1.maven.org/maven2",
    "https://jitpack.io",
    "https://maven.minecraftforge.net",
]

CURSE_MAVEN_BASES = [
    "https://www.cursemaven.com/curse/maven",
]

MODRINTH_MAVEN_BASES = [
    "https://api.modrinth.com/maven",
]

PROJECT_REPOSITORY_FILES = [
    "repositories.gradle",
    "repositories.gradle.kts",
]

COORD_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.+\-]+$")
PRE_SUFFIX_RE = re.compile(r"(?i)(?:^|[._-])pre$")
REPOSITORY_URL_PATTERNS = [
    re.compile(r"""\burl\s*=\s*(?:uri\s*\(\s*)?["']([^"']+)["']\s*\)?"""),
    re.compile(r"""\burl\s+(?:uri\s*\(\s*)?["']([^"']+)["']\s*\)?"""),
    re.compile(r"""\bsetUrl\s*\(\s*(?:uri\s*\(\s*)?["']([^"']+)["']\s*\)?\s*\)"""),
]


def local_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def first_tag_text(root: ET.Element, target_name: str) -> Optional[str]:
    for element in root.iter():
        if local_name(element.tag) == target_name:
            text = (element.text or "").strip()
            if text:
                return text
    return None


def all_tag_texts(root: ET.Element, target_name: str) -> List[str]:
    values: List[str] = []
    for element in root.iter():
        if local_name(element.tag) == target_name:
            text = (element.text or "").strip()
            if text:
                values.append(text)
    return values


def is_pre_suffix_version(version: str) -> bool:
    return bool(PRE_SUFFIX_RE.search(version.strip()))


def select_metadata_candidate(
    release: Optional[str], latest: Optional[str], versions: List[str]
) -> Optional[str]:
    for candidate in (release, latest):
        if candidate and not is_pre_suffix_version(candidate):
            return candidate

    stable_versions = [version for version in versions if not is_pre_suffix_version(version)]
    if stable_versions:
        return stable_versions[-1]

    return None


def find_comment_start(line: str) -> int:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if (in_single or in_double) and char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if not in_single and not in_double and char == "/" and index + 1 < len(line) and line[index + 1] == "/":
            return index
    return len(line)


def iter_string_literals(segment: str) -> List[Tuple[int, int, str]]:
    literals: List[Tuple[int, int, str]] = []
    index = 0
    while index < len(segment):
        char = segment[index]
        if char not in ("'", '"'):
            index += 1
            continue

        quote = char
        index += 1
        content_start = index
        escaped = False

        while index < len(segment):
            current = segment[index]
            if escaped:
                escaped = False
                index += 1
                continue
            if current == "\\":
                escaped = True
                index += 1
                continue
            if current == quote:
                literals.append((content_start, index, segment[content_start:index]))
                index += 1
                break
            index += 1
        else:
            break
    return literals


def parse_coordinate(literal_value: str) -> Optional[Tuple[str, str, str, Tuple[str, ...]]]:
    if "${" in literal_value:
        return None

    parts = literal_value.split(":")
    if len(parts) < 3:
        return None

    group = parts[0].strip()
    artifact = parts[1].strip()
    version = parts[2].strip()
    extras = tuple(part.strip() for part in parts[3:])

    if not group or not artifact or not version:
        return None
    if "/" in group or "/" in artifact or "/" in version:
        return None
    if not COORD_TOKEN_RE.match(group):
        return None
    if not COORD_TOKEN_RE.match(artifact):
        return None
    if not COORD_TOKEN_RE.match(version):
        return None
    for extra in extras:
        if not extra or not COORD_TOKEN_RE.match(extra):
            return None

    return group, artifact, version, extras


def unique_in_order(values: List[str]) -> List[str]:
    seen = set()
    unique_values = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        unique_values.append(value)
    return unique_values


def normalize_repository_url(url: str) -> Optional[str]:
    normalized = url.strip().rstrip("/")
    if not normalized.startswith(("http://", "https://")):
        return None
    if "$" in normalized:
        return None
    return normalized


def mask_gradle_comments(content: str) -> str:
    """Replace comments with spaces while preserving offsets and line breaks."""
    masked = list(content)
    index = 0
    quote: Optional[str] = None
    escaped = False

    while index < len(content):
        char = content[index]

        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            elif char in "\r\n":
                # Gradle's ordinary single- and double-quoted strings do not
                # continue across lines. Keep malformed input from hiding the
                # remainder of the file from the scanner.
                quote = None
            index += 1
            continue

        if char in ("'", '"'):
            quote = char
            index += 1
            continue

        if content.startswith("//", index):
            while index < len(content) and content[index] not in "\r\n":
                masked[index] = " "
                index += 1
            continue

        if content.startswith("/*", index):
            masked[index] = " "
            masked[index + 1] = " "
            index += 2
            while index < len(content):
                if content.startswith("*/", index):
                    masked[index] = " "
                    masked[index + 1] = " "
                    index += 2
                    break
                if content[index] not in "\r\n":
                    masked[index] = " "
                index += 1
            continue

        index += 1

    return "".join(masked)


def strip_gradle_comments(content: str) -> str:
    return mask_gradle_comments(content)


def extract_maven_repository_urls(content: str) -> List[str]:
    uncommented = strip_gradle_comments(content)
    matches: List[Tuple[int, str]] = []
    for pattern in REPOSITORY_URL_PATTERNS:
        for match in pattern.finditer(uncommented):
            normalized = normalize_repository_url(match.group(1))
            if normalized:
                matches.append((match.start(), normalized))
    urls = [url for _, url in sorted(matches, key=lambda item: item[0])]
    return unique_in_order(urls)


def environment_extra_maven_repositories() -> List[str]:
    raw_extra = os.environ.get("DEPENDENCY_CHECK_EXTRA_MAVEN_REPOS", "")
    if not raw_extra.strip():
        return []

    urls = []
    for entry in raw_extra.split(","):
        normalized = normalize_repository_url(entry)
        if normalized:
            urls.append(normalized)
    return unique_in_order(urls)


def discover_project_maven_repositories(deps_file: str) -> List[str]:
    deps_dir = os.path.dirname(os.path.abspath(deps_file))
    urls: List[str] = []

    for file_name in PROJECT_REPOSITORY_FILES:
        repo_file = os.path.join(deps_dir, file_name)
        if not os.path.isfile(repo_file):
            continue

        try:
            with open(repo_file, "r", encoding="utf-8") as handle:
                urls.extend(extract_maven_repository_urls(handle.read()))
        except OSError:
            continue

    return unique_in_order(urls)


def metadata_urls(group: str, artifact: str, extra_repos: Optional[List[str]] = None) -> List[str]:
    if group == "curse.maven":
        return [f"{base.rstrip('/')}/{artifact}/maven-metadata.xml" for base in CURSE_MAVEN_BASES]

    if group == "maven.modrinth":
        return [f"{base.rstrip('/')}/{group.replace('.', '/')}/{artifact}/maven-metadata.xml" for base in MODRINTH_MAVEN_BASES]

    if extra_repos is None:
        extra_repos = environment_extra_maven_repositories()

    group_path = group.replace(".", "/")
    all_bases = unique_in_order(DEFAULT_MAVEN_BASES + extra_repos)
    return [f"{base.rstrip('/')}/{group_path}/{artifact}/maven-metadata.xml" for base in all_bases]


def ipv4_first_address_info(addresses: List[tuple]) -> List[tuple]:
    """Keep resolver order within each family, but try IPv4 before IPv6."""
    return sorted(addresses, key=lambda address: address[0] != socket.AF_INET)


@contextmanager
def prefer_ipv4_resolution():
    """Make urllib avoid long IPv6 failures while retaining IPv6 fallback."""
    system_getaddrinfo = socket.getaddrinfo

    def getaddrinfo_ipv4_first(*args, **kwargs):
        return ipv4_first_address_info(system_getaddrinfo(*args, **kwargs))

    # urllib ultimately resolves through socket.create_connection. The scan is
    # single-threaded, so this tightly scoped override cannot affect another
    # request in this process.
    socket.getaddrinfo = getaddrinfo_ipv4_first
    try:
        yield
    finally:
        socket.getaddrinfo = system_getaddrinfo


def fetch_latest_from_metadata(urls: List[str], timeout_seconds: float) -> Tuple[Optional[str], Optional[str], List[str]]:
    headers = {"User-Agent": "ModdedTools-DependencyChecker/1.0"}
    last_versions: List[str] = []

    for url in urls:
        request = urllib.request.Request(url, headers=headers)
        try:
            with prefer_ipv4_resolution():
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    status = getattr(response, "status", None)
                    if status is not None and (status < 200 or status >= 300):
                        continue
                    data = response.read()
        except (urllib.error.URLError, TimeoutError, OSError):
            continue

        try:
            root = ET.fromstring(data)
        except ET.ParseError:
            continue

        release = first_tag_text(root, "release")
        latest = first_tag_text(root, "latest")
        versions = all_tag_texts(root, "version")
        last_versions = versions

        candidate = select_metadata_candidate(release, latest, versions)
        if candidate:
            return candidate, url, versions

    return None, None, last_versions


def compare_with_known_order(current: str, candidate: str, versions: List[str]) -> int:
    if current == candidate:
        return 0
    if not versions:
        return 1
    try:
        current_index = versions.index(current)
        candidate_index = versions.index(candidate)
    except ValueError:
        return 1
    if candidate_index > current_index:
        return 1
    return -1


def scan_dependencies(
    deps_file: str,
    timeout_seconds: float,
    progress_callback: Optional[Callable[[int, int, str, str], None]] = None,
) -> Dict[str, object]:
    with open(deps_file, "r", encoding="utf-8") as handle:
        content = handle.read()

    extra_repos = unique_in_order(
        environment_extra_maven_repositories() + discover_project_maven_repositories(deps_file)
    )
    lines = mask_gradle_comments(content).splitlines(keepends=True)
    offset = 0

    grouped: Dict[Tuple[str, str, str, Tuple[str, ...]], Dict[str, object]] = {}
    ordered_keys: List[Tuple[str, str, str, Tuple[str, ...]]] = []

    for line_number, line in enumerate(lines, start=1):
        segment = line[: find_comment_start(line)]
        literals = iter_string_literals(segment)
        for content_start, content_end, literal_value in literals:
            parsed = parse_coordinate(literal_value)
            if parsed is None:
                continue

            group, artifact, version, extras = parsed
            key = (group, artifact, version, extras)
            if key not in grouped:
                grouped[key] = {
                    "group": group,
                    "artifact": artifact,
                    "current_version": version,
                    "extras": list(extras),
                    "occurrences": [],
                }
                ordered_keys.append(key)

            grouped[key]["occurrences"].append(
                {
                    "start": offset + content_start,
                    "end": offset + content_end,
                    "line": line_number,
                    "literal": literal_value,
                }
            )

        offset += len(line)

    cache: Dict[Tuple[str, str], Tuple[Optional[str], Optional[str], List[str]]] = {}
    candidates: List[Dict[str, object]] = []
    next_id = 0

    total_dependencies = len(ordered_keys)
    for dependency_index, key in enumerate(ordered_keys, start=1):
        entry = grouped[key]
        group = entry["group"]
        artifact = entry["artifact"]
        current_version = entry["current_version"]
        extras: List[str] = entry["extras"]
        cache_key = (group, artifact)
        is_best_effort = group in {"curse.maven", "maven.modrinth"}

        if progress_callback is not None:
            progress_callback(
                dependency_index,
                total_dependencies,
                f"{group}:{artifact}",
                current_version,
            )

        if cache_key not in cache:
            urls = metadata_urls(group, artifact, extra_repos)
            cache[cache_key] = fetch_latest_from_metadata(urls, timeout_seconds)

        latest_version, source_url, versions = cache[cache_key]
        if not latest_version:
            continue

        relation = compare_with_known_order(current_version, latest_version, versions)
        if relation <= 0:
            continue

        new_parts = [group, artifact, latest_version, *extras]
        new_literal = ":".join(new_parts)

        candidates.append(
            {
                "id": next_id,
                "group": group,
                "artifact": artifact,
                "current_version": current_version,
                "latest_version": latest_version,
                "source_url": source_url or "",
                "best_effort": is_best_effort,
                "new_literal": new_literal,
                "occurrences": entry["occurrences"],
            }
        )
        next_id += 1

    return {"deps_file": deps_file, "content": content, "candidates": candidates}


def cmd_scan(args: argparse.Namespace) -> int:
    def report_progress(index: int, total: int, dependency: str, version: str) -> None:
        print(
            f"Checking [{index}/{total}] {dependency}:{version}",
            file=sys.stderr,
            flush=True,
        )

    state = scan_dependencies(
        args.deps_file,
        args.timeout,
        progress_callback=report_progress,
    )
    with open(args.state_file, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)

    candidates: List[Dict[str, object]] = state["candidates"]
    if not candidates:
        print("NO_UPDATES")
        return 0

    for candidate in candidates:
        key = f"{candidate['group']}:{candidate['artifact']}"
        occurrence_count = len(candidate["occurrences"])
        print(
            "CANDIDATE|{id}|{key}|{current}|{latest}|{count}|{best_effort}".format(
                id=candidate["id"],
                key=key,
                current=candidate["current_version"],
                latest=candidate["latest_version"],
                count=occurrence_count,
                best_effort="1" if candidate.get("best_effort") else "0",
            )
        )
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    with open(args.state_file, "r", encoding="utf-8") as handle:
        state = json.load(handle)

    content: str = state["content"]
    deps_file: str = state["deps_file"]
    candidates: List[Dict[str, object]] = state.get("candidates", [])

    selected_ids = set()
    for raw_id in args.ids.split(","):
        raw_id = raw_id.strip()
        if not raw_id:
            continue
        try:
            selected_ids.add(int(raw_id))
        except ValueError:
            pass

    if not selected_ids:
        print("NO_CHANGES")
        return 0

    replacements: List[Tuple[int, int, str]] = []
    selected_candidates = 0
    selected_occurrences = 0
    for candidate in candidates:
        candidate_id = candidate.get("id")
        if candidate_id not in selected_ids:
            continue
        selected_candidates += 1
        new_literal = candidate["new_literal"]
        for occurrence in candidate["occurrences"]:
            replacements.append((occurrence["start"], occurrence["end"], new_literal))
            selected_occurrences += 1
        print(
            "APPLIED|{id}|{group}:{artifact}|{old}|{new}|{count}".format(
                id=candidate_id,
                group=candidate["group"],
                artifact=candidate["artifact"],
                old=candidate["current_version"],
                new=candidate["latest_version"],
                count=len(candidate["occurrences"]),
            )
        )

    if not replacements:
        print("NO_CHANGES")
        return 0

    for start, end, value in sorted(replacements, key=lambda item: item[0], reverse=True):
        content = content[:start] + value + content[end:]

    with open(deps_file, "w", encoding="utf-8") as handle:
        handle.write(content)

    print(f"UPDATED|{selected_candidates}|{selected_occurrences}|{deps_file}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scan and update dependency versions in dependencies.gradle")
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="Scan for available updates")
    scan_parser.add_argument("--deps-file", required=True, help="Path to dependencies.gradle")
    scan_parser.add_argument("--state-file", required=True, help="Path to temporary state JSON")
    scan_parser.add_argument("--timeout", type=float, default=8.0, help="HTTP timeout in seconds")
    scan_parser.set_defaults(func=cmd_scan)

    apply_parser = subparsers.add_parser("apply", help="Apply selected updates")
    apply_parser.add_argument("--state-file", required=True, help="Path to state JSON from scan step")
    apply_parser.add_argument("--ids", default="", help="Comma-separated candidate IDs to apply")
    apply_parser.set_defaults(func=cmd_apply)

    return parser


def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
