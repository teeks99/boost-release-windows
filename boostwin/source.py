"""Fetching and unpacking the Boost source and its build dependencies."""
import os
import shutil

from .util import WINDOWS, Timer, download, extract, fail, log, rmtree, run


def dependency_downloads(config):
    """(url, archive name, unpacked directory name) for every dependency.

    The two names differ for Python: ``Python3.14.0-64.tar.xz`` unpacks to
    ``Python314-64``.
    """
    deps = config.deps
    items = []
    for arch_key in config.arch_keys:
        arch = config.archs[arch_key]
        items.append((deps.url + deps.python_archive(arch.python_arch),
                      deps.python_archive(arch.python_arch),
                      deps.python_dir(arch.python_arch)))
    for archive, directory in (
        (deps.zlib_archive, deps.zlib_dir),
        (deps.bzip2_archive, deps.bzip2_dir),
        (deps.sevenzip_archive, deps.sevenzip_dir),
    ):
        items.append((deps.url + archive, archive, directory))
    return items


def fetch(workspace, force=False):
    """Download the source archive and every dependency into ``downloads``."""
    config = workspace.config
    workspace.ensure_layout()
    with Timer("fetch"):
        if config.release.is_git:
            log("repo is 'git': the source is cloned during prepare")
        else:
            download(config.release.source_url,
                     workspace.downloads / config.release.source_file,
                     force=force)
        for url, name, _directory in dependency_downloads(config):
            download(url, workspace.downloads / name, force=force)
    return workspace.downloads


def _extract_source(workspace):
    config = workspace.config
    if workspace.source.exists():
        log("source already present: {}".format(workspace.source))
        return workspace.source

    if config.release.is_git:
        run(["git", "clone", "--recursive", "--branch",
             config.release.git_branch, config.release.source_url,
             config.release.source_name],
            cwd=workspace.source_parent)
        return workspace.source

    archive = workspace.downloads / config.release.source_file
    if not archive.exists():
        fail("source archive missing: {} (run 'fetch' first)".format(archive))
    extract(archive, workspace.source_parent, sevenzip=workspace.sevenzip)

    extracted = workspace.source_parent / config.release.source_archive_output
    if extracted != workspace.source:
        if not extracted.exists():
            fail("expected {} inside {}".format(extracted.name, archive.name))
        log("rename {} -> {}".format(extracted.name, workspace.source.name))
        shutil.move(str(extracted), str(workspace.source))
    return workspace.source


def extract_dependencies(workspace, optional=False):
    """Unpack the dependency archives that have been downloaded.

    ``optional`` skips anything not downloaded, which is what the packaging
    step wants: it only needs 7-Zip, not zlib or Python.
    """
    config = workspace.config
    for _url, name, directory in dependency_downloads(config):
        archive = workspace.downloads / name
        if not archive.exists():
            if optional:
                continue
            fail("dependency archive missing: {} (run 'fetch' first)"
                 .format(archive))
        unpacked = workspace.deps / directory
        if unpacked.exists():
            log("dependency already present: {}".format(unpacked.name))
            continue
        extract(archive, workspace.deps)
        if not unpacked.exists():
            fail("{} did not unpack to {}".format(name, unpacked))


def build_environment(workspace, base_env=None):
    """Environment additions Boost.Iostreams needs to build zlib and bzip2."""
    env = dict(base_env if base_env is not None else os.environ)
    env["ZLIB_SOURCE"] = str(workspace.zlib_root)
    env["BZIP2_SOURCE"] = str(workspace.bzip2_root)
    return env


def bootstrap(workspace, env=None, force=False):
    """Build ``b2`` in the source tree."""
    if workspace.b2.exists() and not force:
        log("b2 already built: {}".format(workspace.b2))
        return workspace.b2
    script = "bootstrap.bat" if WINDOWS else "./bootstrap.sh"
    with Timer("bootstrap"):
        run(script, cwd=workspace.source, env=env)
    if not workspace.b2.exists():
        fail("bootstrap did not produce {}".format(workspace.b2))
    return workspace.b2


def prepare(workspace, env=None, do_bootstrap=True, with_deps=True):
    """Unpack everything and build b2, ready for one or more configurations."""
    workspace.ensure_layout()
    with Timer("prepare"):
        # Dependencies first: they are all tar archives, and one of them is
        # the 7-Zip that a .7z source release needs to unpack.
        extract_dependencies(workspace, optional=not with_deps)
        _extract_source(workspace)
        if do_bootstrap:
            bootstrap(workspace, env=env)
    return workspace.source


def clean_source(workspace):
    """Remove staged library directories left in the source tree."""
    for entry in workspace.source.glob("lib*-msvc-*"):
        if entry.is_dir():
            rmtree(entry)
    garbage = workspace.source / "garbage_headers"
    if garbage.exists():
        rmtree(garbage)
