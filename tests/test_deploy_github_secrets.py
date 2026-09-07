import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "deploy_github_secrets.py"


class DeployGitHubSecretsTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.tool_dir = self.root / "tools"
        self.project_dir = self.root / "mod checkout"
        self.fake_bin = self.root / "fake-bin"
        (self.tool_dir / "bin").mkdir(parents=True)
        self.project_dir.mkdir()
        self.fake_bin.mkdir()
        self.script = self.tool_dir / "bin" / SCRIPT.name
        shutil.copy2(SCRIPT, self.script)
        self.log = self.root / "gh-calls.jsonl"
        self.values = {
            "MODRINTH_TOKEN": "modrinth-example-token",
            "CURSEFORGE_TOKEN": "$2a$example-curseforge-token",
            "MAVEN_USER": "example-publisher",
            "MAVEN_PASSWORD": "literal$(touch should-not-exist) # !=secret",
            "MINECRAFT67_TOKEN": "minecraft67-example-token",
            "MAVEN_URL": "https://maven.example.invalid/releases",
        }
        self.write_env()
        (self.project_dir / ".env").write_text("MODRINTH_TOKEN=wrong-project-token\n")
        self.environment = os.environ.copy()
        for name in ("PROJECT_DIR", "GH_TOKEN", "GITHUB_TOKEN", "GH_TEST_EXPECTED_TOKEN"):
            self.environment.pop(name, None)
        self.environment.update({
            "PATH": f"{self.fake_bin}{os.pathsep}{os.environ['PATH']}",
            "GH_TEST_LOG": str(self.log),
            "GH_REPO": "wrong-owner/wrong-repo",
            "GH_HOST": "wrong.example.invalid",
            "GH_TEST_FAIL": "",
            "CURSEFORGE_TOKEN": "wrong-environment-token",
        })
        fake_gh = self.fake_bin / "gh"
        fake_gh.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "body = sys.stdin.read() if args[0] in ('secret', 'variable') else None\n"
            "expected = os.environ.get('GH_TEST_EXPECTED_TOKEN')\n"
            "token_matches = os.environ.get('GH_TOKEN') == expected\n"
            "with open(os.environ['GH_TEST_LOG'], 'a') as log:\n"
            "    log.write(json.dumps({'args': args, 'body': body, 'token_matches': token_matches}) + '\\n')\n"
            "if expected and not token_matches:\n"
            "    sys.exit(1)\n"
            "if os.environ.get('GH_TEST_FAIL') in args:\n"
            "    print('simulated failure', file=sys.stderr)\n"
            "    sys.exit(1)\n"
        )
        fake_gh.chmod(0o755)
        self.git("init", "--quiet")
        self.git("remote", "add", "origin", "git@github.com:mod-owner/test-mod.git")
        self.git("remote", "add", "upstream", "https://github.com/other/upstream.git")

    def git(self, *args: str) -> None:
        subprocess.run(
            ["git", "-C", str(self.project_dir), *args],
            check=True, capture_output=True, text=True,
        )

    def write_env(self) -> None:
        (self.tool_dir / ".env").write_text(
            "# Publishing credentials\n" + "".join(
                f'{key}="{value}"\n' for key, value in self.values.items()
            )
            + "UNRELATED_TOKEN=do-not-upload\n",
            encoding="utf-8",
        )

    def run_deploy(self, cwd=None) -> subprocess.CompletedProcess:
        if self.log.exists():
            self.log.unlink()
        result = subprocess.run(
            [str(self.script)], cwd=cwd or self.project_dir,
            env=self.environment, capture_output=True, text=True,
        )
        for value in self.values.values():
            if value:
                self.assertNotIn(value, result.stdout + result.stderr)
        return result

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_deploys_only_requested_settings_to_origin_via_stdin(self) -> None:
        # Unquoted dollar signs and optional export/inline comments also stay literal.
        with (self.tool_dir / ".env").open("a") as handle:
            handle.write(f"export CURSEFORGE_TOKEN={self.values['CURSEFORGE_TOKEN']} # token\n")
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = self.calls()
        self.assertEqual(calls[0]["args"], ["auth", "status", "--active", "--hostname", "github.com"])
        expected = {
            "MODRINTH_TOKEN": self.values["MODRINTH_TOKEN"],
            "CURSEFORGE_TOKEN": self.values["CURSEFORGE_TOKEN"],
            "MAVEN_USER": self.values["MAVEN_USER"],
            "MAVEN_PASSWORD": self.values["MAVEN_PASSWORD"],
            "SIX_SEVEN_TOKEN": self.values["MINECRAFT67_TOKEN"],
            "MAVEN_PUBLISHING_URL": self.values["MAVEN_URL"],
        }
        self.assertEqual({call["args"][2]: call["body"] for call in calls[1:]}, expected)
        self.assertEqual(len(calls), 7)
        for call in calls[1:]:
            name = call["args"][2]
            kind = "variable" if name == "MAVEN_PUBLISHING_URL" else "secret"
            args = [kind, "set", name, "--repo", "github.com/mod-owner/test-mod"]
            if kind == "secret":
                args.extend(["--app", "actions"])
            self.assertEqual(call["args"], args)
        self.assertFalse((self.project_dir / "should-not-exist").exists())

    def test_env_github_token_authenticates_every_call_and_is_not_uploaded(self) -> None:
        self.values["GITHUB_TOKEN"] = "tool-specific-test-token"
        self.write_env()
        self.environment["GH_TEST_EXPECTED_TOKEN"] = self.values["GITHUB_TOKEN"]
        for inherited in (False, True):
            with self.subTest(inherited=inherited):
                if inherited:
                    self.environment["GH_TOKEN"] = "wrong-inherited-gh-token"
                    self.environment["GITHUB_TOKEN"] = "wrong-inherited-github-token"
                result = self.run_deploy()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(self.calls()), 7)
                for call in self.calls():
                    self.assertTrue(call["token_matches"])
                    self.assertNotIn("GITHUB_TOKEN", call["args"])
                    self.assertNotIn(self.values["GITHUB_TOKEN"], call["args"])
                    self.assertNotEqual(call["body"], self.values["GITHUB_TOKEN"])

    def test_empty_env_github_token_preserves_existing_authentication(self) -> None:
        self.values["GITHUB_TOKEN"] = ""
        self.write_env()
        self.environment["GH_TOKEN"] = "existing-test-token"
        self.environment["GH_TEST_EXPECTED_TOKEN"] = "existing-test-token"
        result = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(call["token_matches"] for call in self.calls()))

    def test_invalid_env_github_token_prevents_uploads(self) -> None:
        self.values["GITHUB_TOKEN"] = "invalid-test-token"
        self.write_env()
        self.environment["GH_TEST_EXPECTED_TOKEN"] = "valid-test-token"
        result = self.run_deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Check GITHUB_TOKEN", result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_project_override_and_explicit_six_seven_token(self) -> None:
        self.environment["PROJECT_DIR"] = str(self.project_dir)
        self.values["SIX_SEVEN_TOKEN"] = "preferred-six-seven-token"
        self.write_env()
        for remote in (
            "https://github.com/mod-owner/test-mod.git",
            "ssh://git@github.com/mod-owner/test-mod.git",
        ):
            with self.subTest(remote=remote):
                self.git("remote", "set-url", "origin", remote)
                result = self.run_deploy(cwd=self.tool_dir)
                self.assertEqual(result.returncode, 0, result.stderr)
                call = next(c for c in self.calls() if "SIX_SEVEN_TOKEN" in c["args"])
                self.assertEqual(call["body"], "preferred-six-seven-token")

    def test_missing_or_empty_value_prevents_all_github_calls(self) -> None:
        for value in (None, "", "   "):
            with self.subTest(value=value):
                self.values.pop("CURSEFORGE_TOKEN", None)
                if value is not None:
                    self.values["CURSEFORGE_TOKEN"] = value
                self.write_env()
                result = self.run_deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("CURSEFORGE_TOKEN", result.stderr)
                self.assertEqual(self.calls(), [])

    def test_missing_tool_env_prevents_all_github_calls(self) -> None:
        (self.tool_dir / ".env").unlink()
        result = self.run_deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Missing", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_non_github_origin_prevents_all_github_calls(self) -> None:
        self.git("remote", "set-url", "origin", "git@gitlab.com:owner/mod.git")
        result = self.run_deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("origin", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_authentication_failure_prevents_uploads(self) -> None:
        self.environment["GH_TEST_FAIL"] = "auth"
        result = self.run_deploy()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("gh auth login", result.stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_upload_failure_stops_and_reports_partial_updates(self) -> None:
        for setting, expected_calls in (("MAVEN_PASSWORD", 5), ("MAVEN_PUBLISHING_URL", 7)):
            with self.subTest(setting=setting):
                self.environment["GH_TEST_FAIL"] = setting
                result = self.run_deploy()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f"Failed to set", result.stderr)
                self.assertIn(setting, result.stderr)
                self.assertIn("Earlier successful updates remain applied", result.stderr)
                self.assertNotIn("GitHub secrets and variables deployed.", result.stdout)
                self.assertEqual(len(self.calls()), expected_calls)


if __name__ == "__main__":
    unittest.main()
