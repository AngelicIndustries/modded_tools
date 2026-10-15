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

The dependency checker uses Modrinth's version API with Minecraft-version and
loader filters for `maven.modrinth` dependencies. It reads `minecraftVersion`
(`minecraft_version` or `mcVersion` also work) from the adjacent `gradle.properties`
and gets the loader from the installed version's metadata. Without a project
Minecraft version, updates must retain all game versions supported by the current
file; all current loaders are retained too. Stable releases stay on releases,
beta versions can update to beta or release, and alpha versions can use any channel.
Only later publication dates count as updates. Unknown or ambiguous current
versions and API failures are reported and skipped without unfiltered Maven fallback.
Modrinth version IDs and Maven filter suffixes such as `1.0-forge,1.7.10` are
supported. If a proposed version number is shared by multiple files, the update
uses its unique version ID to avoid resolving the wrong loader in Maven.

For dependencies resolved through `https://maven.angelic.industries/github/`,
the checker migrates filename classifiers of the form `<artifact>-<current version>-dev`,
`-sources`, or `-javadoc` to the corresponding stable selector. Matching is case
insensitive and an optional `.jar` suffix is supported. Existing `dev`, `sources`,
and `javadoc` selectors are preserved. For example,
`jss2a98aj:BugTorch:1.2.15:bugtorch-1.2.15-dev` becomes
`jss2a98aj:BugTorch:1.3.1:dev`. The proposed JAR is checked through the repository,
following redirects and checking its ZIP header, both before offering the update
and immediately before applying it. Unrecognized or ambiguous classifiers and
failed artifact checks are skipped with an explanation.

Run Client also has a `Cached Gradle config (offline)` mode. It passes
`--offline --configuration-cache`, avoiding network checks for remote Gradle scripts and dependencies
that are already cached. The mode fails if the build needs an artifact that has not been cached yet.

For Java 21/25 clients on Wayland, Run Client checks X11 with `xdpyinfo` and
`timeout`. If X11 is unavailable, it selects JetBrains Runtime's native Wayland
toolkit for Swing windows, avoiding dependency-loader startup crashes. This
requires a JetBrains runtime with Wayland support. Set `RUNCLIENT_AWT_WAYLAND=0`
to disable the workaround; an explicit `awt.toolkit.name` in `JAVA_TOOL_OPTIONS`
or `_JAVA_OPTIONS` also takes precedence.

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

For local deployment, set `DEPLOY_LOCAL_PATHS` to your mods directories, separated
by semicolons (for example, `DEPLOY_LOCAL_PATHS="/path/to/instance/minecraft/mods"`).
There are no machine-specific default destinations.

Keep credentials and personal paths in the ignored `.env` file. Only the blank
`.env.example` template belongs in Git. Local environment variants, credential
files, caches, and editor/agent settings are also ignored. Ignore rules do not
remove files or values already committed to Git history.

## Deobfuscate a Minecraft 1.7.10 mod

```sh
./modded_tools --deobf /path/to/mod-1.7.10.jar
# Creates /path/to/mod-1.7.10-dev.jar
./modded_tools --deobf /path/to/mod.jar --output /tmp/mod-dev.jar
./modded_tools --deobf --help
```

This command runs directly, without opening the menu. It requires Python 3.10+
and a JDK (Java 8+), using `JAVA_HOME` when set or `java`/`javac` from `PATH`.
It does not require a mod checkout, Gradle, the Python virtual environment or `.env`.
The input must be a compiled Forge 1.7.10 mod using SRG names (`func_*` / `field_*`).
The output is an MCP-mapped binary development dependency, not decompiled source;
use the original JAR in a regular game installation.

The helper invokes RetroFuturaGradle 1.4.9's own `SimpleSrgRemapper` and bundled ASM,
following its [dependency deobfuscation transform](https://github.com/GTNewHorizons/RetroFuturaGradle/blob/1.4.9/plugin/src/main/java/com/gtnewhorizons/retrofuturagradle/modutils/DependencyDeobfuscationTransform.java).
It uses the method/field CSVs embedded in Forge 1.7.10 build 1614, RFG's default
for this Minecraft version. RFG 1.4.9 keeps the standalone helper compatible with
Java 8+. Classes and their member references are remapped, resources are preserved,
and invalidated JAR signatures and manifest digests are removed.

Dependencies are pinned with SHA-256 checks and cached in `cache/deobf-1.7.10/`;
verified artifacts already in Gradle's cache are reused. Forge's Maven is tried
first, with the Angelic Maven mirror as fallback. Subsequent runs work offline.
Use `--cache-dir DIR` to change the cache location or `--mappings-dir DIR` to use
your project's `methods.csv` and `fields.csv` (for example, when it disables RFG's
Forge embedded mappings). `--deobf-1.7.10` is an alias for `--deobf`.

The default `-dev.jar` suffix matches the Maven `dev` classifier used by RFG's
published development JARs. RFG's internal dependency transform instead names its
temporary output `-deobf.jar`. The original file is preserved; replacing an existing
output requires `--force`. This command only creates the local development JAR.
