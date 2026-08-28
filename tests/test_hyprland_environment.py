import contextlib
import os
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path


COMMON_SH = Path(__file__).resolve().parents[1] / "bin" / "common.sh"


class HyprlandEnvironmentTests(unittest.TestCase):
    def test_recovers_graphical_session_from_hyprland_runtime_files(self) -> None:
        with (
            tempfile.TemporaryDirectory() as temp_dir,
            contextlib.ExitStack() as sockets,
        ):
            root = Path(temp_dir)
            runtime_dir = root / "runtime"
            instance_dir = runtime_dir / "hypr" / "test-signature"
            home_dir = root / "home"
            instance_dir.mkdir(parents=True)
            home_dir.mkdir()
            (home_dir / ".Xauthority").touch()
            (instance_dir / "hyprland.lock").write_text(
                "1234\nwayland-9\n",
                encoding="utf-8",
            )

            for path in (
                instance_dir / ".socket.sock",
                runtime_dir / "wayland-9",
                runtime_dir / "bus",
            ):
                unix_socket = sockets.enter_context(socket.socket(socket.AF_UNIX))
                unix_socket.bind(str(path))

            environment = os.environ.copy()
            for name in (
                "HYPRLAND_INSTANCE_SIGNATURE",
                "WAYLAND_DISPLAY",
                "XAUTHORITY",
                "DBUS_SESSION_BUS_ADDRESS",
                "XDG_SESSION_DESKTOP",
                "DESKTOP_SESSION",
            ):
                environment.pop(name, None)
            environment.update(
                {
                    "HOME": str(home_dir),
                    "XDG_RUNTIME_DIR": str(runtime_dir),
                    "XDG_SESSION_TYPE": "tty",
                    "XDG_CURRENT_DESKTOP": "gnome",
                    "DISPLAY": ":77",
                }
            )

            command = """
source "$1"
recover_hyprland_environment
printf '%s\n' \\
    "$XDG_RUNTIME_DIR" \\
    "$HYPRLAND_INSTANCE_SIGNATURE" \\
    "$WAYLAND_DISPLAY" \\
    "$DISPLAY" \\
    "$XDG_SESSION_TYPE" \\
    "$XDG_CURRENT_DESKTOP" \\
    "$XDG_SESSION_DESKTOP" \\
    "$DESKTOP_SESSION" \\
    "$_JAVA_AWT_WM_NONREPARENTING" \\
    "$XAUTHORITY" \\
    "$DBUS_SESSION_BUS_ADDRESS"
"""
            result = subprocess.run(
                ["bash", "-c", command, "bash", str(COMMON_SH)],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(
                result.stdout.splitlines(),
                [
                    str(runtime_dir),
                    "test-signature",
                    "wayland-9",
                    ":77",
                    "wayland",
                    "Hyprland",
                    "Hyprland",
                    "Hyprland",
                    "1",
                    str(home_dir / ".Xauthority"),
                    f"unix:path={runtime_dir}/bus",
                ],
            )


if __name__ == "__main__":
    unittest.main()
