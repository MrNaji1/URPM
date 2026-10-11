"""The launcher and install.sh: the parts that decide what code runs on your machine."""

import hashlib
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "urpm" / "data" / "urpm-launcher.sh.in"
INSTALL = ROOT / "install.sh"


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


class LauncherTests(unittest.TestCase):
    def test_current_folder_cannot_hijack_the_launcher(self):
        """S3: running `urpm` inside a folder that has its own urpm/ must not run it."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            write(tmp / "real" / "urpm" / "__init__.py", "")
            write(tmp / "real" / "urpm" / "__main__.py", "print('REAL')")
            write(tmp / "evil" / "urpm" / "__init__.py", "print('HIJACKED')")
            write(tmp / "evil" / "urpm" / "__main__.py", "print('HIJACKED')")
            launcher = tmp / "urpm"
            launcher.write_text(LAUNCHER.read_text().replace("@PREFIX@", str(tmp / "real")))
            env = {**os.environ, "PYTHONPATH": str(tmp / "evil")}  # ignored by -I too
            out = subprocess.run(["sh", str(launcher)], cwd=tmp / "evil", env=env,
                                 capture_output=True, text=True, check=True).stdout
        self.assertEqual(out.strip(), "REAL")


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class InstallScriptTests(unittest.TestCase):
    """Runs install.sh's download_release() against local files instead of GitHub."""

    def run_download(self, tag: str, tamper: bool = False) -> subprocess.CompletedProcess:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        tmp = Path(holder.name)
        serve, work = tmp / "serve", tmp / "work"
        serve.mkdir()
        work.mkdir()
        version = "9.8.7"  # what the fake GitHub release contains
        payload = tmp / f"urpm-{version}"
        write(payload / "urpm" / "__init__.py", f'__version__ = "{version}"\n')
        tarball = serve / f"urpm-{version}.tar.gz"
        with tarfile.open(tarball, "w:gz") as tar:
            tar.add(payload, arcname=payload.name)
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        (serve / "SHA256SUMS").write_text(f"{digest}  {tarball.name}\n")
        if tamper:
            with open(tarball, "ab") as fh:
                fh.write(b"tampered")
        script = (
            f"source <(sed '$d' {INSTALL})\n"  # load the functions, skip `main "$@"`
            f'fetch() {{ cp "{serve}/$(basename "$1")" "$2"; }}\n'
            f'URPM_VERSION="$1" download_release MrNaji1/URPM {work}\n'
        )
        return subprocess.run(["bash", "-c", script, "bash", tag], capture_output=True,
                              text=True, check=False)

    def test_verified_release_is_unpacked(self):
        result = self.run_download("v9.8.7")
        self.assertEqual(result.returncode, 0, result.stderr)
        folder = Path(result.stdout.strip().splitlines()[-1])
        self.assertTrue((folder / "urpm" / "__init__.py").exists())
        self.assertIn("Checksum verified", result.stderr)

    def test_tampered_download_is_rejected(self):
        """S4: a download that doesn't match SHA256SUMS installs nothing."""
        result = self.run_download("v9.8.7", tamper=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Checksum mismatch", result.stderr)

    def test_odd_release_names_are_rejected(self):
        for tag in ("v1.0; rm -rf ~", "../../x", "v1.4.0-evil", "main"):
            result = self.run_download(tag)
            self.assertNotEqual(result.returncode, 0, tag)
            self.assertIn("Unexpected release name", result.stderr, tag)

    def test_cut_off_script_runs_nothing(self):
        """S4: if the download stops half way, main() never runs."""
        text = INSTALL.read_text()
        with tempfile.TemporaryDirectory() as home:
            for cut in (len(text) // 3, len(text) // 2, len(text) - 20):
                subprocess.run(["bash"], input=text[:cut], env={**os.environ, "HOME": home},
                               capture_output=True, text=True, check=False)
                self.assertEqual(os.listdir(home), [], f"something ran when cut at {cut}")


if __name__ == "__main__":
    unittest.main()
