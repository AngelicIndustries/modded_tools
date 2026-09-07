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

From a mod's Git checkout, select **Deploy GitHub Secrets + Variables** to create or
update GitHub Actions settings on its GitHub `origin` repository. This requires
GitHub CLI (`gh`) and permission to manage the repository's Actions secrets and
variables. `PROJECT_DIR` can override the checkout directory.

Set `GITHUB_TOKEN=your_personal_access_token` in this tool's `.env` to authenticate
the deployment without `gh auth login`. This token takes precedence over inherited
tokens and saved credentials for the deployment's `gh` processes. It is used only
for authentication. If omitted or empty, the entry uses the usual `gh`
authentication (environment variables or `gh auth login`).

For a fine-grained personal access token, select the target repositories and grant
repository **Secrets: Read and write** and **Variables: Read and write** permissions
([secrets permissions](https://docs.github.com/en/rest/actions/secrets#create-or-update-a-repository-secret),
[variables permissions](https://docs.github.com/en/rest/actions/variables#create-a-repository-variable)).

Values come from this tool's `.env`:

| `.env` key | GitHub setting | Type |
| --- | --- | --- |
| `MODRINTH_TOKEN` | `MODRINTH_TOKEN` | Secret |
| `CURSEFORGE_TOKEN` | `CURSEFORGE_TOKEN` | Secret |
| `MAVEN_USER` | `MAVEN_USER` | Secret |
| `MAVEN_PASSWORD` | `MAVEN_PASSWORD` | Secret |
| `SIX_SEVEN_TOKEN` (fallback: `MINECRAFT67_TOKEN`) | `SIX_SEVEN_TOKEN` | Secret |
| `MAVEN_URL` | `MAVEN_PUBLISHING_URL` | Variable |

All values must be present before uploads begin. Values use `KEY=value` lines with
optional surrounding quotes; dollar signs are preserved literally. Secret values
are passed to `gh` through standard input. If an upload fails, earlier successful
updates remain applied; rerun the entry after fixing the error.

## Configuration

All credentials and settings go in `.env`. See `.env.example` for available options.
