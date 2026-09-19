import importlib.util
import argparse
import contextlib
import io
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlparse


MODULE_PATH = Path(__file__).resolve().parents[1] / "bin" / "dependency_update_checker.py"
SPEC = importlib.util.spec_from_file_location("dependency_update_checker", MODULE_PATH)
checker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(checker)


class DependencyUpdateCheckerTests(unittest.TestCase):
    def write_temp_deps(self, content: str) -> str:
        with tempfile.NamedTemporaryFile("w", suffix=".gradle", delete=False) as handle:
            handle.write(content)
            deps_file = handle.name

        self.addCleanup(Path(deps_file).unlink, missing_ok=True)
        return deps_file

    def test_select_metadata_candidate_ignores_pre_suffix_versions(self) -> None:
        candidate = checker.select_metadata_candidate(
            "2.2.10-GTNH-pre",
            "2.2.10-GTNH-pre",
            ["2.2.9-GTNH", "2.2.10-GTNH-pre"],
        )

        self.assertEqual(candidate, "2.2.9-GTNH")

    def test_ipv4_addresses_are_tried_before_ipv6(self) -> None:
        ipv6_first = [
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.1", 443)),
            (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::2", 443, 0, 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.2", 443)),
        ]

        sorted_addresses = checker.ipv4_first_address_info(ipv6_first)

        self.assertEqual(
            [address[4][0] for address in sorted_addresses],
            ["192.0.2.1", "192.0.2.2", "2001:db8::1", "2001:db8::2"],
        )

    def test_scan_dependencies_does_not_downgrade_current_pre_version(self) -> None:
        deps_file = self.write_temp_deps(
            'implementation "com.github.GTNewHorizons:Baubles-Expanded:2.2.10-GTNH-pre:dev"\n'
        )

        with mock.patch.object(
            checker,
            "fetch_latest_from_metadata",
            return_value=(
                "2.2.9-GTNH",
                "https://example.invalid/maven-metadata.xml",
                ["2.2.9-GTNH", "2.2.10-GTNH-pre"],
            ),
        ):
            state = checker.scan_dependencies(deps_file, timeout_seconds=0.01)

        self.assertEqual(state["candidates"], [])

    def test_scan_dependencies_keeps_latest_stable_when_newest_is_pre(self) -> None:
        deps_file = self.write_temp_deps(
            'implementation "com.github.GTNewHorizons:Baubles-Expanded:2.2.8-GTNH:dev"\n'
        )

        with mock.patch.object(
            checker,
            "fetch_latest_from_metadata",
            return_value=(
                "2.2.9-GTNH",
                "https://example.invalid/maven-metadata.xml",
                ["2.2.8-GTNH", "2.2.9-GTNH", "2.2.10-GTNH-pre"],
            ),
        ):
            state = checker.scan_dependencies(deps_file, timeout_seconds=0.01)

        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(state["candidates"][0]["latest_version"], "2.2.9-GTNH")

    def test_scan_dependencies_ignores_commented_coordinates(self) -> None:
        deps_file = self.write_temp_deps(
            """
            /*
             * implementation("example.group:example-artifact:1.0.0")
             */
            // implementation("line.comment:ignored:1.0.0")
            implementation("real.group:real-artifact:1.0.0")
            """
        )
        checked_urls = []

        def fetch_latest(urls, timeout_seconds):
            checked_urls.extend(urls)
            return (
                "2.0.0",
                "https://example.invalid/maven-metadata.xml",
                ["1.0.0", "2.0.0"],
            )

        with mock.patch.object(checker, "fetch_latest_from_metadata", side_effect=fetch_latest):
            state = checker.scan_dependencies(deps_file, timeout_seconds=0.01)

        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(state["candidates"][0]["group"], "real.group")
        occurrence = state["candidates"][0]["occurrences"][0]
        self.assertEqual(
            state["content"][occurrence["start"] : occurrence["end"]],
            "real.group:real-artifact:1.0.0",
        )
        self.assertTrue(all("example/group" not in url for url in checked_urls))
        self.assertTrue(all("line/comment" not in url for url in checked_urls))

    def test_scan_dependencies_reports_progress(self) -> None:
        deps_file = self.write_temp_deps(
            'implementation("real.group:first:1.0.0")\n'
            'implementation("real.group:second:1.0.0")\n'
        )
        progress = []

        with mock.patch.object(
            checker,
            "fetch_latest_from_metadata",
            return_value=(None, None, []),
        ):
            checker.scan_dependencies(
                deps_file,
                timeout_seconds=0.01,
                progress_callback=lambda index, total, dependency, version: progress.append(
                    (index, total, dependency, version)
                ),
            )

        self.assertEqual(
            progress,
            [
                (1, 2, "real.group:first", "1.0.0"),
                (2, 2, "real.group:second", "1.0.0"),
            ],
        )

    def test_extract_maven_repository_urls_supports_common_gradle_forms(self) -> None:
        urls = checker.extract_maven_repository_urls(
            """
            repositories {
                // url = "https://ignored.example/releases"
                maven { url = "https://maven.fentanylsolutions.org/releases/" }
                maven { url = uri("https://mvn.falsepattern.com/releases/") }
                maven { url "https://mvn.ventooth.com/releases/" }
            }
            """
        )

        self.assertEqual(
            urls,
            [
                "https://maven.fentanylsolutions.org/releases",
                "https://mvn.falsepattern.com/releases",
                "https://mvn.ventooth.com/releases",
            ],
        )

    def test_scan_dependencies_uses_repositories_gradle_next_to_deps_file(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        project_dir = Path(temp_dir.name)
        deps_file = project_dir / "dependencies.gradle"
        deps_file.write_text(
            'runtimeOnlyNonPublishable("org.fentanylsolutions.tabfaces:TabFaces:e023b1a-snapshot:dev")\n',
            encoding="utf-8",
        )
        (project_dir / "repositories.gradle").write_text(
            """
            repositories {
                maven {
                    name = "Fent Maven"
                    url = "https://maven.fentanylsolutions.org/releases"
                }
            }
            """,
            encoding="utf-8",
        )
        captured_urls = []

        def fetch_latest(urls, timeout_seconds):
            captured_urls.extend(urls)
            return (
                "1.0.16",
                "https://maven.fentanylsolutions.org/releases/org/fentanylsolutions/tabfaces/TabFaces/maven-metadata.xml",
                ["e023b1a-snapshot", "1.0.16"],
            )

        with mock.patch.object(checker, "fetch_latest_from_metadata", side_effect=fetch_latest):
            state = checker.scan_dependencies(str(deps_file), timeout_seconds=0.01)

        self.assertIn(
            "https://maven.fentanylsolutions.org/releases/org/fentanylsolutions/tabfaces/TabFaces/maven-metadata.xml",
            captured_urls,
        )
        self.assertEqual(len(state["candidates"]), 1)
        self.assertEqual(state["candidates"][0]["latest_version"], "1.0.16")


class ModrinthUpdateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.deps = self.root / "dependencies.gradle"
        (self.root / "gradle.properties").write_text("minecraftVersion = 1.7.10\n")

    @staticmethod
    def version(number, day, loader="forge", game="1.7.10", channel="release", **kwargs):
        return dict(id=f"id{day}{loader}", version_number=number, loaders=[loader],
                    game_versions=[game], version_type=channel,
                    date_published=f"2026-09-{day:02d}T12:00:00Z", **kwargs)

    def scan(self, versions, current="1.0", filtered=None):
        self.deps.write_text(f'implementation("maven.modrinth:example:{current}")\n')
        requests = []

        def response(request, timeout):
            requests.append(request.full_url)
            data = versions if len(requests) == 1 or filtered is None else filtered
            return io.BytesIO(json.dumps(data).encode())

        with mock.patch.object(checker.urllib.request, "urlopen", side_effect=response), \
                mock.patch.object(checker, "fetch_latest_from_metadata") as maven:
            state = checker.scan_dependencies(str(self.deps), 0.01)
            maven.assert_not_called()
        return state, requests

    def test_euphoria_uses_api_filters_and_rechecks_returned_compatibility(self):
        current = "1.10.1-r5.9.1-forge1.7.10"
        wanted = "1.10.5-r5.9.3-forge1.7.10"
        versions = [self.version("1.10.5-r5.9.3-fabric", 19, "fabric"),
                    self.version("1.10.5-r5.9.3-forge1.12.2", 18, game="1.12.2"),
                    self.version(wanted, 15), self.version(current, 11)]
        state, requests = self.scan(versions, current)
        candidate, = state["candidates"]
        self.assertEqual(candidate["latest_version"], wanted)
        self.assertEqual(candidate["new_literal"], f"maven.modrinth:example:{wanted}")
        self.assertFalse(candidate["best_effort"])
        self.assertEqual(candidate["compatibility"], "Minecraft 1.7.10; forge")
        query = parse_qs(urlparse(requests[1]).query)
        self.assertEqual(json.loads(query["loaders"][0]), ["forge"])
        self.assertEqual(json.loads(query["game_versions"][0]), ["1.7.10"])

    def test_wrong_loader_game_older_versions_and_newer_betas_are_not_updates(self):
        for alternative in [self.version("2.0", 19, "fabric"),
                            self.version("2.0", 19, "neoforge"),
                            self.version("2.0", 19, game="1.12.2"),
                            self.version("0.9", 1),
                            self.version("2.0-beta", 19, channel="beta")]:
            with self.subTest(alternative=alternative):
                state, _ = self.scan([alternative, self.version("1.0", 10)])
                self.assertEqual(state["candidates"], [])

    def test_order_is_by_publication_and_current_beta_can_update_to_release(self):
        state, _ = self.scan([self.version("1.0", 10, channel="beta"),
                             self.version("1.2", 12), self.version("1.1", 11),
                             self.version("2.0", 20, channel="alpha")])
        self.assertEqual(state["candidates"][0]["latest_version"], "1.2")

    def test_id_pins_and_duplicate_numbers_use_unique_version_ids(self):
        for current in ["1.0", "id10forge"]:
            with self.subTest(current=current):
                state, _ = self.scan([self.version("1.0", 10), self.version("2.0", 11),
                                      self.version("2.0", 12, "fabric")], current)
                self.assertEqual(state["candidates"][0]["latest_version"], "id11forge")

    def test_cli_shows_version_label_but_keeps_unique_id_in_apply_state(self):
        state, _ = self.scan([self.version("1.0", 10), self.version("2.0", 11),
                              self.version("2.0", 12, "fabric")])
        state_file = self.root / "state.json"
        with mock.patch.object(checker, "scan_dependencies", return_value=state), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            checker.cmd_scan(argparse.Namespace(deps_file=str(self.deps), timeout=0.01,
                                               state_file=str(state_file)))
        self.assertIn("|2.0 (ID: id11forge)|1|0|Minecraft 1.7.10; forge", output.getvalue())
        saved = json.loads(state_file.read_text())["candidates"][0]
        self.assertEqual(saved["new_literal"], "maven.modrinth:example:id11forge")

    def test_ambiguous_or_unknown_current_is_skipped(self):
        versions = [self.version("1.0", 10), self.version("1.0", 11, "fabric"),
                    self.version("2.0", 12)]
        for current in ["1.0", "missing"]:
            with self.subTest(current=current), contextlib.redirect_stderr(io.StringIO()) as errors:
                state, requests = self.scan(versions, current)
                self.assertEqual(state["candidates"], [])
                self.assertEqual(len(requests), 1)
                self.assertIn("Skipping", errors.getvalue())

    def test_maven_filter_suffix_resolves_duplicate_numbers(self):
        versions = [self.version("1.0", 10), self.version("1.0", 11, "fabric"),
                    self.version("2.0", 12)]
        state, _ = self.scan(versions, "1.0-forge,1.7.10")
        self.assertEqual(state["candidates"][0]["latest_version"], "2.0")

    def test_project_version_narrows_support_otherwise_all_support_is_preserved(self):
        current = self.version("1.0", 10)
        current["game_versions"] = ["1.7.10", "1.12.2"]
        versions = [current, self.version("2.0", 12), self.version("3.0", 13, game="1.12.2")]
        state, _ = self.scan(versions)
        self.assertEqual(state["candidates"][0]["latest_version"], "2.0")
        (self.root / "gradle.properties").unlink()
        state, requests = self.scan(versions)
        self.assertEqual(state["candidates"], [])
        self.assertEqual(json.loads(parse_qs(urlparse(requests[1]).query)["game_versions"][0]),
                         ["1.7.10", "1.12.2"])

    def test_multiloader_support_is_preserved_without_guessing(self):
        current = self.version("1.0", 10)
        current["loaders"] = ["forge", "fabric"]
        state, _ = self.scan([current, self.version("2.0", 12, "fabric")])
        self.assertEqual(state["candidates"], [])

    def test_missing_project_properties_uses_installed_metadata(self):
        (self.root / "gradle.properties").unlink()
        state, _ = self.scan([self.version("1.0", 10), self.version("2.0", 12)])
        self.assertEqual(state["candidates"][0]["latest_version"], "2.0")

    def test_api_errors_and_invalid_metadata_never_fall_back_to_maven(self):
        self.deps.write_text('implementation("maven.modrinth:example:1.0")\n')
        for response in [OSError("offline"), b"not json", b"{}", b"[{}]"]:
            with self.subTest(response=response):
                kwargs = ({"side_effect": response} if isinstance(response, Exception)
                          else {"return_value": io.BytesIO(response)})
                with mock.patch.object(checker.urllib.request, "urlopen", **kwargs), \
                        mock.patch.object(checker, "fetch_latest_from_metadata") as maven, \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    state = checker.scan_dependencies(str(self.deps), 0.01)
                self.assertEqual(state["candidates"], [])
                self.assertIn("Skipping", errors.getvalue())
                maven.assert_not_called()

    def test_empty_filtered_response_does_not_fall_back(self):
        state, _ = self.scan([self.version("1.0", 10), self.version("2.0", 12)], filtered=[])
        self.assertEqual(state["candidates"], [])

    def test_different_current_loaders_are_not_conflated_and_apply_preserves_classifiers(self):
        versions = [self.version("1.0-forge", 10), self.version("1.0-fabric", 10, "fabric"),
                    self.version("2.0-forge", 12), self.version("3.0-fabric", 13, "fabric")]
        content = ('implementation("maven.modrinth:example:1.0-forge:dev")\n'
                   'implementation("maven.modrinth:example:1.0-fabric")\n'
                   '// implementation("maven.modrinth:example:1.0-forge:dev")\n')
        self.deps.write_text(content)
        with mock.patch.object(checker, "fetch_modrinth_versions", return_value=versions) as api:
            state = checker.scan_dependencies(str(self.deps), 0.01)
        self.assertEqual(api.call_count, 3)  # Shared project list, separate filters.
        self.assertEqual([v["latest_version"] for v in state["candidates"]], ["2.0-forge", "3.0-fabric"])
        state_file = self.root / "state.json"
        state_file.write_text(json.dumps(state))
        with contextlib.redirect_stdout(io.StringIO()):
            checker.cmd_apply(argparse.Namespace(state_file=str(state_file), ids="0"))
        self.assertEqual(self.deps.read_text(), content.replace(":1.0-forge:dev", ":2.0-forge:dev", 1))


if __name__ == "__main__":
    unittest.main()
