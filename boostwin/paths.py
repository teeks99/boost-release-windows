"""Where everything lives inside the build root."""
import os
import shutil
from pathlib import Path

from .util import WINDOWS, find_sevenzip


def default_build_root(config):
    """Pick a build root: env var, then build.toml, then a sensible guess."""
    from_env = os.environ.get("BOOSTWIN_BUILD_ROOT")
    if from_env:
        return Path(from_env)
    if config.build.build_root:
        return Path(config.build.build_root)
    if WINDOWS:
        # Boost needs tens of gigabytes of scratch space, and on most build
        # machines (including GitHub's Windows runners) D: is the roomy one.
        for drive in ("D:\\", "C:\\"):
            if Path(drive).exists():
                return Path(drive) / "boostwin"
    return Path.cwd() / "_build"


class Workspace(object):
    """Directory layout for one build root.

    Everything a single configuration writes lives under ``work/<id>`` and
    ``stage/<id>``, so configurations never collide and can run in parallel.
    """

    def __init__(self, root, config):
        self.root = Path(root).resolve()
        self.config = config

    # -- shared across configurations ------------------------------------
    @property
    def downloads(self):
        return self.root / "downloads"

    @property
    def deps(self):
        return self.root / "deps"

    @property
    def source_parent(self):
        return self.root / "src"

    @property
    def source(self):
        return self.source_parent / self.config.release.source_name

    @property
    def b2(self):
        return self.source / ("b2.exe" if WINDOWS else "b2")

    @property
    def out(self):
        return self.root / "out"

    @property
    def stages(self):
        return self.root / "stage"

    # -- per configuration -----------------------------------------------
    def work(self, build_config):
        return self.root / "work" / build_config.id

    def build_dir(self, build_config):
        return self.work(build_config) / "bin.v2"

    def user_config(self, build_config):
        return self.work(build_config) / "user-config.jam"

    def setup_script(self, build_config):
        return self.work(build_config) / "setup-msvc.bat"

    def build_log(self, build_config):
        return self.meta(build_config) / "build.log"

    def stage(self, build_config):
        return self.stages / build_config.id

    def stage_lib(self, build_config):
        return self.stage(build_config) / build_config.lib_dir

    def meta(self, build_config):
        return self.stage(build_config) / "meta"

    def toolchain_json(self, build_config):
        return self.meta(build_config) / "toolchain.json"

    def test_json(self, build_config):
        return self.meta(build_config) / "test.json"

    # -- dependencies ----------------------------------------------------
    def python_root(self, arch):
        return self.deps / self.config.deps.python_dir(arch.python_arch)

    @property
    def zlib_root(self):
        return self.deps / self.config.deps.zlib_dir

    @property
    def bzip2_root(self):
        return self.deps / self.config.deps.bzip2_dir

    @property
    def sevenzip(self):
        """The 7-Zip binary from [deps], falling back to one on PATH."""
        base = self.deps / self.config.deps.sevenzip_dir
        candidates = [
            base / "x64" / "7za.exe",
            base / "7za.exe",
        ]
        return find_sevenzip(candidates)

    def ensure_layout(self):
        for directory in (self.downloads, self.deps, self.source_parent,
                          self.out, self.stages):
            directory.mkdir(parents=True, exist_ok=True)
        return self

    def free_bytes(self):
        probe = self.root
        while not probe.exists() and probe != probe.parent:
            probe = probe.parent
        return shutil.disk_usage(probe).free
