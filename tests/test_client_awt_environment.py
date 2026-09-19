import contextlib
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path


BIN = Path(__file__).resolve().parents[1] / "bin"


class ClientAwtEnvironmentTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        wayland = self.stack.enter_context(socket.socket(socket.AF_UNIX))
        wayland.bind(str(self.root / "wayland-9"))
        self.env = os.environ.copy()
        for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS", "RUNCLIENT_AWT_WAYLAND"):
            self.env.pop(name, None)
        self.env.update(
            XDG_RUNTIME_DIR=str(self.root), WAYLAND_DISPLAY="wayland-9", DISPLAY=":77"
        )
        self.mock_bin = self.root / "bin"
        self.mock_bin.mkdir()
        self.write_script(self.mock_bin / "timeout", 'shift\nexec "$@"\n')
        self.write_script(self.mock_bin / "xdpyinfo", 'exit "${X11_RESULT:-1}"\n')
        self.env["PATH"] = f"{self.mock_bin}:{self.env['PATH']}"

    @staticmethod
    def write_script(path, body):
        path.write_text("#!/usr/bin/env bash\n" + body)
        path.chmod(0o755)

    def argument(self, version="25"):
        return subprocess.run(
            ["bash", "-eu", "-c", 'source "$1"; client_awt_wayland_arg "$2"',
             "bash", str(BIN / "common.sh"), version],
            env=self.env, text=True, capture_output=True, check=True,
        ).stdout.strip()

    def test_broken_x11_selects_wayland_for_supported_java_versions(self):
        for version in ("21", "25"):
            with self.subTest(version=version):
                self.assertEqual(self.argument(version), "-Dawt.toolkit.name=WLToolkit")

    def test_healthy_x11_is_preserved(self):
        self.env["X11_RESULT"] = "0"
        self.assertEqual(self.argument(), "")

    def test_no_wayland_or_older_java_is_preserved(self):
        for version in ("", "17"):
            self.assertEqual(self.argument(version), "")
        self.env["WAYLAND_DISPLAY"] = "missing"
        self.assertEqual(self.argument(), "")

    def test_explicit_toolkit_and_opt_out_are_preserved(self):
        self.env["RUNCLIENT_AWT_WAYLAND"] = "0"
        self.assertEqual(self.argument(), "")
        del self.env["RUNCLIENT_AWT_WAYLAND"]
        for name in ("JAVA_TOOL_OPTIONS", "_JAVA_OPTIONS"):
            self.env[name] = "-Dawt.toolkit.name=XToolkit"
            self.assertEqual(self.argument(), "")
            del self.env[name]

    def test_absolute_wayland_socket_without_display(self):
        self.env["WAYLAND_DISPLAY"] = str(self.root / "wayland-9")
        self.env.pop("DISPLAY")
        self.assertEqual(self.argument(), "-Dawt.toolkit.name=WLToolkit")

    def test_launcher_preserves_debug_and_offline_arguments(self):
        self.write_script(self.root / "gradlew", "printf '<%s>\\n' \"$@\"\n")
        self.env.update(
            PROJECT_DIR=str(self.root), RUN_JAVA_PROMPT="0", RUN_JAVA_VERSION="25",
            CLIENT_DEBUG_PROMPT="0", CLIENT_DEBUG_PORT="5007", CLIENT_DEBUG_SUSPEND="n",
        )
        # Keep this test independent of the machine's real Hyprland session.
        self.write_script(self.mock_bin / "id", "printf '999999\\n'\n")
        for mode in ("1", "2"):
            with self.subTest(mode=mode):
                self.env["RUNCLIENT_PRESET_MODE"] = mode
                result = subprocess.run(
                    ["bash", str(BIN / "runclient.sh")], input="Tester\n",
                    env=self.env, text=True, capture_output=True, check=True,
                )
                self.assertIn("<runClient25>", result.stdout)
                self.assertIn("<--username=Tester>", result.stdout)
                self.assertEqual(result.stdout.count("<--mcJvmArgs="), 1)
                self.assertIn("-Dawt.toolkit.name=WLToolkit>", result.stdout)
                if mode == "1":
                    self.assertIn("suspend=n,address=*:5007 ", result.stdout)
                else:
                    self.assertIn("<--offline>\n<--configuration-cache>", result.stdout)


if __name__ == "__main__":
    unittest.main()
