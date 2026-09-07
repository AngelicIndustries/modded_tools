#!/usr/bin/env python3
"""Deobfuscate Forge 1.7.10 mods with RetroFuturaGradle's own SRG remapper."""

import argparse
import csv
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile


TOOLS_DIR = Path(__file__).resolve().parent.parent
RFG_VERSION = "1.4.9"  # Its standalone remapper works on Java 8 and newer.
RFG_NAME = f"retrofuturagradle-{RFG_VERSION}.jar"
RFG_PATH = f"com/gtnewhorizons/retrofuturagradle/{RFG_VERSION}/{RFG_NAME}"
RFG_SHA256 = "4fe86b90d9c2fc47eabf78d4bd1c19eec3238722faca4ec86461ab758404c27a"
FORGE_VERSION = "1.7.10-10.13.4.1614-1.7.10"
FORGE_NAME = f"forge-{FORGE_VERSION}-userdev.jar"
FORGE_PATH = f"net/minecraftforge/forge/{FORGE_VERSION}/{FORGE_NAME}"
FORGE_SHA256 = "6ed364c0defdb481a41ef2d99c02d382f16fc42a6ea0ea1c2b849848d8466e91"


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        digest = hashlib.sha256()
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dependency(cache: Path, name: str, digest: str, urls: list[str], group: str, version: str) -> Path:
    """Reuse verified downloads, including Gradle's cache, and replace atomically."""
    target = cache / name
    if target.is_file() and sha256(target) == digest:
        return target
    gradle_home = Path(os.environ.get("GRADLE_USER_HOME", str(Path.home() / ".gradle")))
    candidates = (gradle_home / "caches/modules-2/files-2.1" / group / version).glob(f"*/{name}")
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="download-", dir=cache) as directory:
        temporary = Path(directory) / name
        for candidate in candidates:
            if sha256(candidate) == digest:
                shutil.copyfile(candidate, temporary)
                temporary.replace(target)
                return target
        failures = []
        for url in urls:
            print(f"Downloading {name} from {urllib.parse.urlsplit(url).hostname}...", flush=True)
            try:
                with urllib.request.urlopen(url, timeout=30) as response, temporary.open("wb") as output:
                    shutil.copyfileobj(response, output)
                if sha256(temporary) != digest:
                    raise ValueError(f"SHA-256 mismatch for {name}")
                temporary.replace(target)
                return target
            except (OSError, ValueError, urllib.error.URLError) as error:
                failures.append(str(error))
        raise ValueError(f"Cannot download {name}: {'; '.join(failures)}")


def load_mappings(cache: Path, mappings_dir: Path | None) -> dict[str, str]:
    if mappings_dir is not None:
        csv_data = [(mappings_dir / name).read_text(encoding="utf-8-sig")
                    for name in ("methods.csv", "fields.csv")]
    else:
        forge = dependency(cache, FORGE_NAME, FORGE_SHA256, [
            f"https://maven.minecraftforge.net/{FORGE_PATH}",
            f"https://maven.fentanylsolutions.org/releases/{FORGE_PATH}",
        ], "net.minecraftforge/forge", FORGE_VERSION)
        with zipfile.ZipFile(forge) as archive:
            csv_data = [archive.read(f"conf/{name}").decode("utf-8-sig")
                        for name in ("methods.csv", "fields.csv")]
    mappings = {}
    for data in csv_data:
        reader = csv.DictReader(io.StringIO(data))
        if not {"searge", "name"}.issubset(reader.fieldnames or []):
            raise ValueError("Mapping CSVs must have searge and name columns.")
        for row in reader:
            source, target = row["searge"], row["name"]
            if not source or not target or any(c in source + target for c in "\t\r\n"):
                raise ValueError("Empty or invalid name in mapping CSVs.")
            mappings[source] = target
    if not mappings:
        raise ValueError("Mapping CSVs contain no mappings.")
    return mappings


def java_command(name: str) -> str:
    java_home = os.environ.get("JAVA_HOME")
    command = str(Path(java_home) / "bin" / name) if java_home else name
    executable = shutil.which(command)
    if executable is None:
        raise ValueError(f"Required command not found: {command}. Install a JDK (Java 8+) or set JAVA_HOME.")
    return executable


def deobfuscate(input_jar: Path, output_jar: Path, cache: Path, mappings_dir: Path | None, force: bool) -> None:
    input_jar, output_jar = input_jar.resolve(), output_jar.resolve()
    if not input_jar.is_file() or input_jar.suffix.lower() != ".jar":
        raise ValueError(f"Input must be an existing .jar file: {input_jar}")
    if input_jar == output_jar or (output_jar.exists() and os.path.samefile(input_jar, output_jar)):
        raise ValueError("Input and output must be different files.")
    if output_jar.suffix.lower() != ".jar":
        raise ValueError("Output must have a .jar extension.")
    if output_jar.exists() and not force:
        raise ValueError(f"Output already exists: {output_jar}. Use --force to replace it.")
    with zipfile.ZipFile(input_jar) as archive:
        if not any(name.endswith(".class") for name in archive.namelist()):
            raise ValueError("Input JAR contains no compiled classes.")
    java, javac = java_command("java"), java_command("javac")
    mappings = load_mappings(cache, mappings_dir)
    rfg = dependency(cache, RFG_NAME, RFG_SHA256, [
        f"https://nexus.gtnewhorizons.com/repository/public/{RFG_PATH}",
    ], "com.gtnewhorizons/retrofuturagradle", RFG_VERSION)
    output_jar.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".modded-tools-deobf-", dir=output_jar.parent) as directory:
        work = Path(directory)
        mapping_file = work / "mappings.tsv"
        mapping_file.write_text("".join(f"{source}\t{target}\n" for source, target in mappings.items()), encoding="utf-8")
        subprocess.run([
            javac, "-source", "8", "-target", "8", "-Xlint:-options", "-proc:none",
            "-cp", str(rfg), "-d", str(work), str(TOOLS_DIR / "bin/java/DeobfuscateMod.java"),
        ], check=True)
        temporary = work / "output.jar"
        print(f"Deobfuscating {input_jar.name} with RFG {RFG_VERSION} ({len(mappings)} mappings)...", flush=True)
        subprocess.run([
            java, "-cp", os.pathsep.join((str(work), str(rfg))), "DeobfuscateMod",
            str(input_jar), str(temporary), str(mapping_file),
        ], check=True)
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("Deobfuscated JAR failed its ZIP integrity check.")
        if force:
            temporary.replace(output_jar)
        else:
            # Publish a complete result without clobbering a concurrently created file.
            os.link(temporary, output_jar)
    print(f"Created {output_jar}")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="modded_tools --deobf",
        description="Deobfuscate a Forge 1.7.10 mod from SRG to MCP names using RetroFuturaGradle.",
    )
    parser.add_argument("jar", type=Path, help="compiled Forge 1.7.10 mod JAR")
    parser.add_argument("-o", "--output", type=Path, help="output JAR (default: INPUT-dev.jar)")
    parser.add_argument("--mappings-dir", type=Path, help="directory containing methods.csv and fields.csv; defaults to RFG's Forge 1614 mappings")
    parser.add_argument("--cache-dir", type=Path, default=TOOLS_DIR / "cache/deobf-1.7.10", help="dependency download cache")
    parser.add_argument("--force", action="store_true", help="replace an existing output JAR")
    args = parser.parse_args()
    output = args.output or args.jar.with_name(args.jar.stem + "-dev.jar")
    try:
        deobfuscate(args.jar, output, args.cache_dir.resolve(), args.mappings_dir, args.force)
    except (OSError, ValueError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
