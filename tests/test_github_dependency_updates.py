import argparse
import contextlib
import http.client
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import urllib.error


SPEC = importlib.util.spec_from_file_location(
    "dependency_update_checker", Path(__file__).resolve().parents[1] / "bin/dependency_update_checker.py"
)
checker = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(checker)


class ArtifactResponse(io.BytesIO):
    def __init__(self, body=b"PK\x03\x04", status=206):
        super().__init__(body)
        self.status = status


class GitHubDependencyUpdateTests(unittest.TestCase):
    SOURCE = "https://maven.angelic.industries/github/jss2a98aj/BugTorch/maven-metadata.xml"
    ARTIFACT_BASE = "https://maven.angelic.industries/github/jss2a98aj/BugTorch/1.3.1/BugTorch-1.3.1"

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.deps = self.root / "dependencies.gradle"
        self.state_file = self.root / "state.json"
        self.write_deps("bugtorch-1.2.15-dev")
        (self.root / "repositories.gradle").write_text(
            'repositories { maven { url = "https://maven.angelic.industries/github/" } }\n'
        )
        metadata = mock.patch.object(
            checker, "fetch_latest_from_metadata",
            return_value=("1.3.1", self.SOURCE, ["1.2.15", "1.3.1"]),
        )
        self.metadata = metadata.start()
        self.addCleanup(metadata.stop)

    def write_deps(self, classifier):
        suffix = ":" + classifier if classifier else ""
        coordinate = "jss2a98aj:BugTorch:1.2.15" + suffix
        content = (f'implementation("{coordinate}")\n'
                   f'runtimeOnly("{coordinate}")\n'
                   f'// implementation("{coordinate}")\n')
        self.deps.write_text(content)
        return content

    def scan(self):
        return checker.scan_dependencies(str(self.deps), timeout_seconds=0.1)

    def apply(self, state, ids="0"):
        self.state_file.write_text(json.dumps(state))
        with contextlib.redirect_stdout(io.StringIO()) as output:
            checker.cmd_apply(argparse.Namespace(state_file=str(self.state_file), ids=ids))
        return output.getvalue()

    def test_bugtorch_1_2_15_to_1_3_1_migrates_dev_and_verifies_scan_and_apply(self):
        before = self.deps.read_text()
        with mock.patch.object(checker.urllib.request, "urlopen", side_effect=lambda *a, **k: ArtifactResponse()) as http:
            state = self.scan()
            candidate, = state["candidates"]
            self.assertEqual(candidate["new_literal"], "jss2a98aj:BugTorch:1.3.1:dev")
            self.assertEqual(candidate["classifier_change"], "bugtorch-1.2.15-dev -> dev")
            self.assertEqual(len(candidate["occurrences"]), 2)
            self.assertEqual(self.deps.read_text(), before)
            output = self.apply(state)
        self.assertEqual(http.call_count, 2)
        for call in http.call_args_list:
            request = call.args[0]
            self.assertEqual(request.full_url, self.ARTIFACT_BASE + "-dev.jar")
            self.assertEqual(request.get_header("Range"), "bytes=0-3")
            self.assertEqual(call.kwargs["timeout"], 0.1)
        self.assertIn(self.SOURCE, self.metadata.call_args.args[0])
        self.assertEqual(self.deps.read_text(), before.replace(
            "jss2a98aj:BugTorch:1.2.15:bugtorch-1.2.15-dev", "jss2a98aj:BugTorch:1.3.1:dev", 2,
        ))
        self.assertIn("UPDATED|1|2|", output)

    def test_stable_selectors_and_unclassified_artifacts_are_preserved_and_verified(self):
        for selector in ("dev", "sources", "javadoc", ""):
            with self.subTest(selector=selector):
                self.write_deps(selector)
                with mock.patch.object(checker.urllib.request, "urlopen", return_value=ArtifactResponse()) as http:
                    candidate, = self.scan()["candidates"]
                suffix = ":" + selector if selector else ""
                self.assertEqual(candidate["new_literal"], "jss2a98aj:BugTorch:1.3.1" + suffix)
                self.assertNotIn("classifier_change", candidate)
                self.assertEqual(http.call_args.args[0].full_url,
                                 self.ARTIFACT_BASE + ("-" + selector if selector else "") + ".jar")

    def test_recognized_sources_and_javadoc_filenames_migrate(self):
        for selector in ("sources", "javadoc"):
            with self.subTest(selector=selector):
                self.write_deps(f"BugTorch-1.2.15-{selector}.jar")
                with mock.patch.object(checker.urllib.request, "urlopen", return_value=ArtifactResponse()):
                    candidate, = self.scan()["candidates"]
                self.assertEqual(candidate["new_literal"], "jss2a98aj:BugTorch:1.3.1:" + selector)

    def test_unrecognized_or_ambiguous_filename_is_skipped_with_reason(self):
        for classifier in ("bugtorch-client-1.2.15-dev", "other-1.2.15-dev", "bugtorch-1.2.14-dev",
                           "bugtorch-1.2.15-dev-sources", "dev:extra"):
            with self.subTest(classifier=classifier):
                self.write_deps(classifier)
                with mock.patch.object(checker.urllib.request, "urlopen") as http, \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    state = self.scan()
                self.assertEqual(state["candidates"], [])
                self.assertIn("ambiguous", errors.getvalue())
                http.assert_not_called()

    def test_http_failures_and_server_ambiguity_skip_with_reason(self):
        for status in (404, 409, 500):
            with self.subTest(status=status):
                error = urllib.error.HTTPError(self.ARTIFACT_BASE + "-dev.jar", status, "failure", {}, None)
                with mock.patch.object(checker.urllib.request, "urlopen", side_effect=error), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    state = self.scan()
                self.assertEqual(state["candidates"], [])
                self.assertIn(f"HTTP {status}", errors.getvalue())
                if status == 409:
                    self.assertIn("ambiguous artifact selection", errors.getvalue())

    def test_network_failure_or_html_success_is_not_a_verified_jar(self):
        for result in (TimeoutError(), urllib.error.URLError("offline"), ArtifactResponse(b"<html>", 200),
                       http.client.IncompleteRead(b"PK", 2), ArtifactResponse(b"", 204)):
            with self.subTest(result=result):
                kwargs = {"side_effect": result} if isinstance(result, Exception) else {"return_value": result}
                with mock.patch.object(checker.urllib.request, "urlopen", **kwargs), \
                        contextlib.redirect_stderr(io.StringIO()) as errors:
                    state = self.scan()
                self.assertEqual(state["candidates"], [])
                self.assertIn("artifact verification", errors.getvalue())

    def test_get_without_range_support_is_accepted(self):
        with mock.patch.object(checker.urllib.request, "urlopen", return_value=ArtifactResponse(status=200)):
            self.assertEqual(len(self.scan()["candidates"]), 1)

    def test_disappearing_artifact_is_skipped_at_apply_without_changing_file(self):
        with mock.patch.object(checker.urllib.request, "urlopen", return_value=ArtifactResponse()):
            state = self.scan()
        before = self.deps.read_text()
        with mock.patch.object(checker.urllib.request, "urlopen", side_effect=TimeoutError()), \
                contextlib.redirect_stderr(io.StringIO()) as errors:
            output = self.apply(state)
        self.assertEqual(self.deps.read_text(), before)
        self.assertEqual(output, "NO_CHANGES\n")
        self.assertIn("Skipping jss2a98aj:BugTorch:1.3.1:dev", errors.getvalue())
        self.assertNotIn("APPLIED", output)

    def test_failed_candidate_does_not_prevent_other_selected_update(self):
        self.deps.write_text('implementation("jss2a98aj:BugTorch:1.2.15:dev")\n'
                             'implementation("jss2a98aj:BugTorch:1.2.15:sources")\n')
        with mock.patch.object(checker.urllib.request, "urlopen", side_effect=lambda *a, **k: ArtifactResponse()):
            state = self.scan()
        with mock.patch.object(checker.urllib.request, "urlopen", side_effect=[TimeoutError(), ArtifactResponse()]), \
                contextlib.redirect_stderr(io.StringIO()):
            output = self.apply(state, "0,1")
        self.assertIn("1.2.15:dev", self.deps.read_text())
        self.assertIn("1.3.1:sources", self.deps.read_text())
        self.assertIn("UPDATED|1|1|", output)

    def test_other_repository_does_not_migrate_or_verify_classifiers(self):
        for source in ("https://maven.angelic.industries/releases/", "https://example.invalid/github/",
                       "https://maven.angelic.industries/github-other/"):
            with self.subTest(source=source):
                self.metadata.return_value = ("1.3.1", source + "jss2a98aj/BugTorch/maven-metadata.xml", ["1.2.15", "1.3.1"])
                with mock.patch.object(checker.urllib.request, "urlopen") as http:
                    candidate, = self.scan()["candidates"]
                self.assertEqual(candidate["new_literal"], "jss2a98aj:BugTorch:1.3.1:bugtorch-1.2.15-dev")
                http.assert_not_called()


if __name__ == "__main__":
    unittest.main()
