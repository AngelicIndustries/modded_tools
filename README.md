# ModdedTools

Interactive CLI toolkit for Minecraft mod development. Build, run, publish, and manage your mod project from one menu.

## Setup

```sh
./setup_venv.sh
cp .env.example .env  # fill in your credentials
```

## Usage

```sh
./modded_tools
```

Select from: build, run client/server (with optional debug), apply Spotless, manage lang files, publish to Maven, update CurseForge/Modrinth readmes, and more.

Run Client also has a `Cached Gradle config (offline)` mode. It passes
`--offline --configuration-cache`, avoiding network checks for remote Gradle scripts and dependencies
that are already cached. The mode fails if the build needs an artifact that has not been cached yet.

## Configuration

All credentials and settings go in `.env`. See `.env.example` for available options.
