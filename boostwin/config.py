"""Load ``build.toml`` and expand it into the list of build configurations."""
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import repos
from .util import fail

DEFAULT_CONFIG_FILE = "build.toml"


@dataclass(frozen=True)
class Arch:
    """One target architecture, and how b2 / vcvars spell it."""

    key: str
    address_model: str
    architecture: str
    abi: str
    vcvars_target: str
    python_arch: str

    @property
    def lib_prefix(self):
        return "lib" + self.key


@dataclass(frozen=True)
class Toolset:
    """One MSVC toolset, and where it can be found."""

    name: str
    runner: str
    vs_version_range: str
    vs_display: str
    vcvars_ver: str
    archs: tuple
    install_components: tuple

    @property
    def b2_toolset(self):
        return "msvc-" + self.name


@dataclass(frozen=True)
class LinkCombo:
    link: str
    runtime_link: str


@dataclass(frozen=True)
class BuildConfig:
    """A single unit of work: one toolset, arch and library variant."""

    toolset: Toolset
    arch: Arch
    variant: str
    threading: str
    link: str
    runtime_link: str

    @property
    def id(self):
        parts = ["msvc", self.toolset.name, self.arch.key, self.variant,
                 self.link, self.runtime_link]
        name = "-".join(parts)
        if self.threading != "multi":
            name += "-" + self.threading
        return name

    @property
    def lib_dir(self):
        """Name of the staged library directory, e.g. ``lib64-msvc-14.3``."""
        return "{}-msvc-{}".format(self.arch.lib_prefix, self.toolset.name)

    @property
    def runner(self):
        return self.toolset.runner

    @property
    def b2_properties(self):
        return [
            "toolset=" + self.toolset.b2_toolset,
            "address-model=" + self.arch.address_model,
            "architecture=" + self.arch.architecture,
            "abi=" + self.arch.abi,
            "variant=" + self.variant,
            "link=" + self.link,
            "runtime-link=" + self.runtime_link,
            "threading=" + self.threading,
        ]

    def matrix_entry(self):
        return {
            "id": self.id,
            "runner": self.runner,
            "toolset": self.toolset.name,
            "arch": self.arch.key,
            "variant": self.variant,
            "link": self.link,
            "runtime_link": self.runtime_link,
            "lib_dir": self.lib_dir,
        }


@dataclass
class Release:
    """Which Boost source archive this build starts from."""

    version: str
    minor_version: str
    type: str
    repo: str
    beta: int
    rc: int
    source_extension: str

    def __post_init__(self):
        if self.repo not in repos.REPOS:
            fail("unknown repo {!r}; known: {}".format(
                self.repo, ", ".join(sorted(repos.REPOS))))
        if self.type not in repos.REPOS[self.repo]:
            fail("unknown release type {!r} for repo {!r}; known: {}".format(
                self.type, self.repo,
                ", ".join(sorted(repos.REPOS[self.repo]))))
        self.spec = repos.REPOS[self.repo][self.type]
        fields = {
            "version": self.version,
            "minor_version": self.minor_version,
            "beta": self.beta,
            "rc": self.rc,
            "file_extension": self.source_extension,
        }
        fields["archive_suffix"] = self.spec["archive_suffix"].format(**fields)
        self._fields = fields

    @property
    def is_git(self):
        return self.repo == "git"

    @property
    def source_name(self):
        """Directory name of the extracted source, e.g. ``boost_1_93_0``."""
        if self.is_git:
            return "boost"
        return "boost_1_{}_{}".format(self.version, self.minor_version)

    @property
    def archive_suffix(self):
        return self._fields["archive_suffix"]

    @property
    def release_name(self):
        """Name used for every published file, e.g. ``boost_1_93_0-snapshot``."""
        return self.source_name + self.archive_suffix

    @property
    def dotted_version(self):
        return "1.{}.{}".format(self.version, self.minor_version)

    @property
    def source_url(self):
        if self.is_git:
            return self.spec["url"]
        return self.spec["url"].format(**self._fields) + self.source_file

    @property
    def source_file(self):
        return self.spec["file"].format(**self._fields)

    @property
    def source_archive_output(self):
        """Top level directory name inside the downloaded archive."""
        return self.spec["source_archive_output"].format(**self._fields)

    @property
    def git_branch(self):
        return self.spec.get("branch")


@dataclass
class Deps:
    url: str
    python: str
    zlib: str
    bzip2: str
    sevenzip: str

    @property
    def python_tag(self):
        """``3.14.0`` -> ``314``, matching the Boost.Python library suffix."""
        major, minor = self.python.split(".")[:2]
        return major + minor

    @property
    def python_dotted(self):
        major, minor = self.python.split(".")[:2]
        return major + "." + minor

    def python_dir(self, arch_key):
        """Directory the archive unpacks to, e.g. ``Python314-64``."""
        return "Python{}-{}".format(self.python_tag, arch_key)

    def python_archive(self, arch_key):
        """Archive name on the dependency server, e.g.
        ``Python3.14.0-64.tar.xz`` -- note it does not match the directory."""
        return "Python{}-{}.tar.xz".format(self.python, arch_key)

    @property
    def zlib_dir(self):
        return "zlib-" + self.zlib

    @property
    def zlib_archive(self):
        return self.zlib_dir + ".tar.gz"

    @property
    def bzip2_dir(self):
        return "bzip2-" + self.bzip2

    @property
    def bzip2_archive(self):
        return self.bzip2_dir + ".tar.gz"

    @property
    def sevenzip_dir(self):
        return "7z" + self.sevenzip

    @property
    def sevenzip_archive(self):
        return self.sevenzip_dir + ".tar.xz"


@dataclass
class BuildOptions:
    b2_jobs: int = 0
    without: tuple = ()
    layout: str = "versioned"
    build_type: str = "complete"
    extra_b2_args: tuple = ()
    build_root: str = ""
    keep_intermediate: bool = False


@dataclass
class ConditionalLibs:
    """Libraries that are only expected for some build configurations."""

    libs: tuple
    variant: tuple = ()
    link: tuple = ()
    runtime_link: tuple = ()

    def applies_to(self, config):
        for attribute in ("variant", "link", "runtime_link"):
            allowed = getattr(self, attribute)
            if allowed and getattr(config, attribute) not in allowed:
                return False
        return True


@dataclass
class SmokeOptions:
    enabled: bool = True
    required_libs: tuple = ()
    conditional_libs: tuple = ()
    python_extension: bool = True
    std: str = "c++14"


@dataclass
class Config:
    path: Path
    release: Release
    deps: Deps
    build: BuildOptions
    smoke: SmokeOptions
    toolsets: list
    archs: dict
    variants: list = field(default_factory=list)
    threadings: list = field(default_factory=lambda: ["multi"])
    link_combos: list = field(default_factory=list)

    @property
    def root(self):
        """Repository root: everything else is resolved relative to this."""
        return self.path.parent

    def toolset(self, name):
        for toolset in self.toolsets:
            if toolset.name == name:
                return toolset
        fail("unknown toolset {!r}; known: {}".format(
            name, ", ".join(t.name for t in self.toolsets)))

    def matrix(self):
        """Every build configuration this release should produce."""
        configs = []
        for toolset in self.toolsets:
            for arch_key in toolset.archs:
                if arch_key not in self.archs:
                    fail("toolset {} refers to unknown arch {!r}".format(
                        toolset.name, arch_key))
                arch = self.archs[arch_key]
                for variant in self.variants:
                    for threading in self.threadings:
                        for combo in self.link_combos:
                            configs.append(BuildConfig(
                                toolset=toolset,
                                arch=arch,
                                variant=variant,
                                threading=threading,
                                link=combo.link,
                                runtime_link=combo.runtime_link,
                            ))
        return configs

    @property
    def arch_keys(self):
        """Architectures in play, in the order they appear in the config."""
        keys = []
        for toolset in self.toolsets:
            for arch_key in toolset.archs:
                if arch_key not in keys:
                    keys.append(arch_key)
        return keys

    @property
    def full_archive_name(self):
        """e.g. ``boost_1_93_0-snapshot-bin-msvc-all-32-64.7z``."""
        return "{}-bin-msvc-all-{}.7z".format(
            self.release.release_name, "-".join(self.arch_keys))


ENVIRONMENT_OVERRIDES = {
    "version": "BOOSTWIN_RELEASE_VERSION",
    "minor_version": "BOOSTWIN_RELEASE_MINOR_VERSION",
    "type": "BOOSTWIN_RELEASE_TYPE",
    "repo": "BOOSTWIN_RELEASE_REPO",
    "beta": "BOOSTWIN_RELEASE_BETA",
    "rc": "BOOSTWIN_RELEASE_RC",
}


def environment_overrides():
    """Release settings taken from the environment, for CI.

    An unset or empty variable means "leave build.toml alone", so a workflow
    can forward optional inputs without special-casing them.
    """
    overrides = {}
    for key, variable in ENVIRONMENT_OVERRIDES.items():
        value = os.environ.get(variable, "").strip()
        if value:
            overrides[key] = value
    return overrides


def _require(table, key, where):
    if key not in table:
        fail("missing key {!r} in [{}]".format(key, where))
    return table[key]


def load(path=None, overrides=None):
    """Read a ``build.toml`` and return a fully resolved :class:`Config`."""
    if path is None:
        path = os.environ.get("BOOSTWIN_CONFIG", DEFAULT_CONFIG_FILE)
    path = Path(path).resolve()
    if not path.exists():
        fail("config file not found: {}".format(path))
    with open(path, "rb") as handle:
        data = tomllib.load(handle)

    release_table = dict(_require(data, "release", "top level"))
    # Environment first, explicit command line flags second.
    for key, value in environment_overrides().items():
        release_table[key] = value
    for key, value in (overrides or {}).items():
        if value is not None and value != "":
            release_table[key] = value
    release = Release(
        version=str(_require(release_table, "version", "release")),
        minor_version=str(_require(release_table, "minor_version", "release")),
        type=str(_require(release_table, "type", "release")),
        repo=str(_require(release_table, "repo", "release")),
        beta=int(release_table.get("beta", 1)),
        rc=int(release_table.get("rc", 1)),
        source_extension=str(release_table.get("source_extension", "tar.bz2")),
    )

    deps_table = _require(data, "deps", "top level")
    deps = Deps(
        url=_require(deps_table, "url", "deps"),
        python=str(_require(deps_table, "python", "deps")),
        zlib=str(_require(deps_table, "zlib", "deps")),
        bzip2=str(_require(deps_table, "bzip2", "deps")),
        sevenzip=str(_require(deps_table, "sevenzip", "deps")),
    )

    build_table = data.get("build", {})
    build = BuildOptions(
        b2_jobs=int(build_table.get("b2_jobs", 0)),
        without=tuple(build_table.get("without", ())),
        layout=build_table.get("layout", "versioned"),
        build_type=build_table.get("build_type", "complete"),
        extra_b2_args=tuple(build_table.get("extra_b2_args", ())),
        build_root=build_table.get("build_root", ""),
        keep_intermediate=bool(build_table.get("keep_intermediate", False)),
    )

    smoke_table = data.get("smoke", {})
    conditional_libs = tuple(
        ConditionalLibs(
            libs=tuple(_require(entry, "libs", "smoke.conditional_libs")),
            variant=tuple(entry.get("variant", ())),
            link=tuple(entry.get("link", ())),
            runtime_link=tuple(entry.get("runtime_link", ())),
        )
        for entry in smoke_table.get("conditional_libs", ())
    )
    smoke = SmokeOptions(
        enabled=bool(smoke_table.get("enabled", True)),
        required_libs=tuple(smoke_table.get("required_libs", ())),
        conditional_libs=conditional_libs,
        python_extension=bool(smoke_table.get("python_extension", True)),
        std=smoke_table.get("std", "c++14"),
    )

    archs = {}
    for key, table in _require(data, "arch", "top level").items():
        archs[key] = Arch(
            key=key,
            address_model=str(_require(table, "address_model", "arch." + key)),
            architecture=str(_require(table, "architecture", "arch." + key)),
            abi=str(table.get("abi", "ms")),
            vcvars_target=str(_require(table, "vcvars_target", "arch." + key)),
            python_arch=str(table.get("python_arch", key)),
        )

    toolsets = []
    for table in _require(data, "toolset", "top level"):
        name = str(_require(table, "name", "toolset"))
        toolsets.append(Toolset(
            name=name,
            runner=str(_require(table, "runner", "toolset " + name)),
            vs_version_range=str(
                _require(table, "vs_version_range", "toolset " + name)),
            vs_display=str(table.get("vs_display", "")),
            vcvars_ver=str(table.get("vcvars_ver", "")),
            archs=tuple(str(a) for a in _require(
                table, "archs", "toolset " + name)),
            install_components=tuple(table.get("install_components", ())),
        ))

    variants_table = data.get("variants", {})
    variants = [str(v) for v in variants_table.get("variant",
                                                   ["debug", "release"])]
    threadings = [str(t) for t in variants_table.get("threading", ["multi"])]
    combos = variants_table.get("link_combos") or [
        {"link": "shared", "runtime_link": "shared"},
        {"link": "static", "runtime_link": "shared"},
        {"link": "static", "runtime_link": "static"},
    ]
    link_combos = []
    for combo in combos:
        link = str(_require(combo, "link", "variants.link_combos"))
        runtime_link = str(
            _require(combo, "runtime_link", "variants.link_combos"))
        if link == "shared" and runtime_link == "static":
            fail("link=shared with runtime-link=static is disabled by Boost; "
                 "remove it from [variants].link_combos")
        link_combos.append(LinkCombo(link=link, runtime_link=runtime_link))

    return Config(
        path=path,
        release=release,
        deps=deps,
        build=build,
        smoke=smoke,
        toolsets=toolsets,
        archs=archs,
        variants=variants,
        threadings=threadings,
        link_combos=link_combos,
    )
