import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "modded_tools"
spec = importlib.util.spec_from_file_location("deobfuscate_mod", ROOT / "bin/deobfuscate_mod.py")
deobf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deobf)


class DeobfuscationCliTests(unittest.TestCase):
    def test_help_and_bad_arguments_do_not_open_menu(self):
        for args, status, expected in [
            (["--help"], 0, "--deobf"),
            (["--deobf", "--help"], 0, "--mappings-dir"),
            (["--deobf-1.7.10", "--help"], 0, "--mappings-dir"),
            (["--deobf"], 2, "required"),
            (["--unknown"], 2, "Unknown argument"),
        ]:
            with self.subTest(args=args):
                result = subprocess.run([str(LAUNCHER), *args], stdin=subprocess.DEVNULL,
                                        capture_output=True, text=True, timeout=10, cwd="/tmp")
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn(expected, result.stdout + result.stderr)
                self.assertNotIn("Select one option", result.stdout)

    def test_input_output_validation_precedes_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "input.jar"
            original.write_bytes(b"original")
            existing = root / "existing.jar"
            existing.write_bytes(b"existing")
            alias = root / "alias.jar"
            alias.symlink_to(original)
            for source, output, force, message in [
                (root / "missing.jar", existing, False, "existing .jar"),
                (original, original, True, "different files"),
                (original, alias, True, "different files"),
                (original, existing, False, "already exists"),
                (original, root / "out.txt", False, ".jar extension"),
            ]:
                with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                    deobf.deobfuscate(source, output, root / "cache", None, force)
            self.assertFalse((root / "cache").exists())
            self.assertEqual(original.read_bytes(), b"original")
            self.assertEqual(existing.read_bytes(), b"existing")


class DeobfuscationBytecodeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Integration tests stay offline. Run --deobf once to populate the cache.
        cls.cache = ROOT / "cache/deobf-1.7.10"
        if not (cls.cache / deobf.RFG_NAME).exists():
            raise unittest.SkipTest("Run modded_tools --deobf once to cache RetroFuturaGradle.")
        try:
            cls.java, cls.javac = deobf.java_command("java"), deobf.java_command("javac")
        except ValueError as error:
            raise unittest.SkipTest(str(error))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="deobf test ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.mappings = self.root / "maps"
        self.mappings.mkdir()
        (self.mappings / "methods.csv").write_text("searge,name,side,desc\nfunc_149739_a,getUnlocalizedName,2,\n")
        (self.mappings / "fields.csv").write_text("searge,name,side,desc\nfield_149782_v,blockHardness,2,\n")
        self.source = self.root / "Example.java"
        self.source.write_text('''
class Base {
    public int field_149782_v = 7;
    public String func_149739_a() { return "mapped"; }
}
public class Example extends Base {
    public String func_149739_a() { return super.func_149739_a() + "!"; }
    public static void main(String[] args) throws Exception {
        Example example = new Example();
        System.out.println(example.func_149739_a() + example.field_149782_v);
        System.out.println(Example.class.getMethod("getUnlocalizedName").invoke(example));
        System.out.println(Base.class.getField("blockHardness").get(example));
        System.out.println("func_149739_a");
    }
}
''')
        subprocess.run([self.javac, "-source", "8", "-target", "8", "-Xlint:-options", str(self.source)],
                       check=True, capture_output=True)
        self.input = self.root / "test mod.jar"
        self.output = self.root / "test mod-dev.jar"
        with zipfile.ZipFile(self.input, "w") as archive:
            for path in self.root.glob("*.class"):
                archive.write(path, path.name)
            archive.writestr("assets/example/data.bin", b"\x00\xfffunc_149739_a")
            archive.writestr("assets/example/keep.RSA", b"not a signature")
            archive.writestr("META-INF/MANIFEST.MF", (
                "Manifest-Version: 1.0\r\nFMLCorePlugin: example.Plugin\r\n\r\n"
                "Name: Example.class\r\nSHA-256-Digest: invalid\r\n\r\n"
                "Name: assets/example/data.bin\r\nCustom: retained\r\nSHA1-Digest: invalid\r\n\r\n"))
            archive.writestr("META-INF/TEST.SF", b"invalid signature")
            archive.writestr("META-INF/TEST.RSA", b"invalid signature")

    def run_cli(self, *args):
        return subprocess.run([str(LAUNCHER), "--deobf", str(self.input),
                               "--mappings-dir", str(self.mappings), *args],
                              cwd="/tmp", capture_output=True, text=True, timeout=30)

    def test_remaps_overrides_inherited_references_and_preserves_resources_and_strings(self):
        before = self.input.read_bytes()
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.input.read_bytes(), before)
        execution = subprocess.run([self.java, "-Xverify:all", "-cp", str(self.output), "Example"],
                                   check=True, capture_output=True, text=True)
        self.assertEqual(execution.stdout.splitlines(), ["mapped!7", "mapped!", "7", "func_149739_a"])
        with zipfile.ZipFile(self.output) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("assets/example/data.bin"), b"\x00\xfffunc_149739_a")
            self.assertEqual(archive.read("assets/example/keep.RSA"), b"not a signature")
            self.assertNotIn("META-INF/TEST.SF", archive.namelist())
            self.assertNotIn("META-INF/TEST.RSA", archive.namelist())
            manifest = archive.read("META-INF/MANIFEST.MF").decode()
            self.assertNotIn("Digest", manifest)
            self.assertIn("FMLCorePlugin: example.Plugin", manifest)
            self.assertIn("Custom: retained", manifest)

    def test_failure_preserves_existing_output_even_with_force(self):
        self.output.write_bytes(b"previous output")
        with zipfile.ZipFile(self.input, "w") as archive:
            archive.writestr("Broken.class", b"invalid bytecode")
        result = self.run_cli("--force")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b"previous output")
        self.assertEqual(list(self.root.glob(".modded-tools-deobf-*")), [])

    def test_force_replaces_existing_output(self):
        self.output.write_bytes(b"previous output")
        result = self.run_cli("--force")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(zipfile.is_zipfile(self.output))


if __name__ == "__main__":
    unittest.main()
