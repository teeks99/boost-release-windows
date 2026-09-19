"""Small helpers shared by the rest of :mod:`boostwin`."""
import contextlib
import datetime
import hashlib
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path


WINDOWS = os.name == "nt"


def log(message):
    """Print a timestamped line and flush, so CI logs interleave correctly."""
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    print("[{}] {}".format(stamp, message), flush=True)


def fail(message):
    raise SystemExit("error: " + message)


class Timer(object):
    """Context manager that reports how long a phase took."""

    def __init__(self, name):
        self.name = name
        self.elapsed = None

    def __enter__(self):
        self.start = time.time()
        log("--- {} ---".format(self.name))
        return self

    def __exit__(self, exc_type, exc, tb):
        self.elapsed = time.time() - self.start
        log("--- {} finished in {} ---".format(
            self.name, format_duration(self.elapsed)))
        return False


def format_duration(seconds):
    return str(datetime.timedelta(seconds=int(seconds)))


def format_size(num_bytes):
    value = float(num_bytes)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return "{:.1f} {}".format(value, unit)
        value /= 1024.0


def run(cmd, cwd=None, env=None, log_file=None, check=True, quiet=False):
    """Run ``cmd``, streaming output to stdout and optionally to ``log_file``.

    Returns the exit code.  ``check`` raises on a non-zero exit code; callers
    that want to record a failure without aborting pass ``check=False``.
    """
    printable = cmd if isinstance(cmd, str) else subprocess.list2cmdline(cmd)
    log("run: " + printable)
    handle = None
    if log_file is not None:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handle = open(log_file, "a", encoding="utf-8", errors="replace")
        handle.write("\n=== {} ===\n".format(printable))
    try:
        process = subprocess.Popen(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            shell=isinstance(cmd, str),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        for raw in process.stdout:
            line = raw.decode("utf-8", errors="replace")
            if not quiet:
                sys.stdout.write(line)
            if handle is not None:
                handle.write(line)
        process.stdout.close()
        code = process.wait()
    finally:
        if not quiet:
            sys.stdout.flush()
        if handle is not None:
            handle.close()
    if check and code != 0:
        fail("command failed with exit code {}: {}".format(code, printable))
    return code


def capture(cmd, cwd=None, env=None, check=True):
    """Run ``cmd`` and return its stdout as text."""
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        env=env,
        shell=isinstance(cmd, str),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and completed.returncode != 0:
        printable = cmd if isinstance(cmd, str) else subprocess.list2cmdline(cmd)
        fail("command failed with exit code {}: {}\n{}".format(
            completed.returncode, printable,
            completed.stderr.decode("utf-8", errors="replace")))
    return completed.stdout.decode("utf-8", errors="replace")


def download(url, destination, force=False):
    """Fetch ``url`` to ``destination`` unless it is already there."""
    destination = Path(destination)
    if destination.exists() and destination.stat().st_size > 0 and not force:
        log("cached: {} ({})".format(
            destination.name, format_size(destination.stat().st_size)))
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    log("download: " + url)
    with urllib.request.urlopen(url) as response, open(partial, "wb") as out:
        shutil.copyfileobj(response, out, 1024 * 1024)
    partial.replace(destination)
    log("saved: {} ({})".format(
        destination.name, format_size(destination.stat().st_size)))
    return destination


def extract(archive, destination, sevenzip=None):
    """Extract a tar.*, .zip or .7z archive into ``destination``."""
    archive = Path(archive)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    log("extract: {} -> {}".format(archive.name, destination))
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(destination)
    elif name.endswith(".7z"):
        sevenzip = sevenzip or find_sevenzip()
        if not sevenzip:
            fail("cannot extract {}: no 7-Zip executable found".format(archive))
        run([sevenzip, "x", "-y", "-o" + str(destination), str(archive)],
            quiet=True)
    else:
        with tarfile.open(archive) as tf:
            _safe_extract_tar(tf, destination)
    return destination


def _safe_extract_tar(tf, destination):
    """tarfile.extractall with the 3.12+ data filter when it is available."""
    try:
        tf.extractall(destination, filter="data")
    except TypeError:  # Python < 3.12
        tf.extractall(destination)


def find_sevenzip(extra_paths=()):
    """Locate a 7-Zip command line binary, preferring an explicit path."""
    for candidate in extra_paths:
        if candidate and Path(candidate).exists():
            return str(candidate)
    for name in ("7za", "7zz", "7z"):
        found = shutil.which(name)
        if found:
            return found
    if WINDOWS:
        for candidate in (
            r"C:\Program Files\7-Zip\7z.exe",
            r"C:\Program Files (x86)\7-Zip\7z.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return None


def sha256(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def rmtree(path):
    """Remove a tree, coping with the read-only files b2 sometimes leaves."""
    path = Path(path)
    if not path.exists():
        return

    def on_error(func, name, _exc):
        with contextlib.suppress(OSError):
            os.chmod(name, 0o700)
            func(name)

    try:
        shutil.rmtree(path, onexc=on_error)
    except TypeError:  # Python < 3.12
        shutil.rmtree(path, onerror=lambda f, n, e: on_error(f, n, e))


def merge_tree(source, destination):
    """Copy ``source`` into ``destination``, merging with what is already there."""
    shutil.copytree(source, destination, dirs_exist_ok=True)


def github_output(name, value):
    """Append ``name=value`` to $GITHUB_OUTPUT when running under Actions."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return False
    with open(target, "a", encoding="utf-8") as handle:
        handle.write("{}={}\n".format(name, value))
    return True


def github_summary(markdown):
    """Append markdown to the Actions job summary when one is available."""
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if not target:
        return False
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(markdown.rstrip() + "\n")
    return True
