import importlib.util
import socket
import tempfile
import unittest
from pathlib import Path
from unittest import mock


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


if __name__ == "__main__":
    unittest.main()
