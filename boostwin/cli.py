"""Command line interface: ``python -m boostwin <command>``."""
import argparse
import json
import multiprocessing
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import build as build_module
from . import config as config_module
from . import msvc, package as package_module, paths, smoke, source
from .util import (Timer, WINDOWS, fail, format_duration, format_size,
                   github_output, github_summary, log, rmtree)


# ----------------------------------------------------------- plumbing ---
def make_workspace(args):
    overrides = {
        "version": args.release_version,
        "minor_version": args.minor_version,
        "type": args.type,
        "repo": args.repo,
        "beta": args.beta,
        "rc": args.rc,
    }
    config = config_module.load(args.config_file, overrides=overrides)
    root = Path(args.build_root) if args.build_root else paths.default_build_root(config)
    return paths.Workspace(root, config)


def selected(workspace, args):
    """Apply the --toolset/--arch/... filters to the full matrix."""
    configs = workspace.config.matrix()
    if getattr(args, "id", None):
        wanted = set(args.id)
        configs = [c for c in configs if c.id in wanted]
        unknown = wanted - {c.id for c in configs}
        if unknown:
            fail("unknown configuration(s): " + ", ".join(sorted(unknown)))
    for attribute, key in (("toolset", lambda c: c.toolset.name),
                           ("arch", lambda c: c.arch.key),
                           ("variant", lambda c: c.variant),
                           ("link", lambda c: c.link),
                           ("runtime_link", lambda c: c.runtime_link)):
        values = getattr(args, attribute, None)
        if values:
            configs = [c for c in configs if key(c) in values]
    if not configs:
        fail("no configurations match the given filters")
    return configs


def one(workspace, args):
    """The single configuration a per-configuration command operates on."""
    configs = selected(workspace, args)
    if len(configs) != 1:
        fail("this command needs exactly one configuration, {} matched:\n  {}"
             .format(len(configs), "\n  ".join(c.id for c in configs)))
    return configs[0]


def toolchain_for(workspace, build_config, install_missing=False):
    return msvc.prepare(workspace, build_config, install_missing=install_missing)


# ----------------------------------------------------------- commands ---
def cmd_info(workspace, args):
    config = workspace.config
    release = config.release
    print("config file     : {}".format(config.path))
    print("build root      : {}  ({} free)".format(
        workspace.root, format_size(workspace.free_bytes())))
    print("boost release   : {}  ({})".format(release.dotted_version, release.type))
    print("release name    : {}".format(release.release_name))
    print("source          : {}".format(release.source_url))
    print("dependencies    : Python {}, zlib {}, bzip2 {}, 7-Zip {}".format(
        config.deps.python, config.deps.zlib, config.deps.bzip2,
        config.deps.sevenzip))
    print("toolsets        : {}".format(
        ", ".join("msvc-{} on {}".format(t.name, t.runner)
                  for t in config.toolsets)))
    print("architectures   : {}".format(", ".join(config.arch_keys)))
    print("configurations  : {}".format(len(config.matrix())))
    print("full archive    : {}".format(config.full_archive_name))
    print("host            : {} ({} cpus)".format(
        sys.platform, multiprocessing.cpu_count()))
    if WINDOWS:
        print("host arch       : {}".format(msvc.host_arch()))
    return 0


def cmd_matrix(workspace, args):
    configs = selected(workspace, args)
    entries = [c.matrix_entry() for c in configs]
    if args.github:
        github_output("configs", json.dumps(entries, separators=(",", ":")))
        github_output("count", str(len(entries)))
        github_output("release_name", workspace.config.release.release_name)
    if args.json or args.github:
        print(json.dumps(entries, indent=2 if not args.github else None))
        return 0
    for entry in entries:
        print("{:<42} {}".format(entry["id"], entry["runner"]))
    print("\n{} configurations on {} runner image(s)".format(
        len(entries), len({e["runner"] for e in entries})))
    return 0


def cmd_fetch(workspace, args):
    source.fetch(workspace, force=args.force)
    return 0


def cmd_prepare(workspace, args):
    if not args.no_fetch:
        source.fetch(workspace)
    if args.no_bootstrap or not WINDOWS:
        source.prepare(workspace, do_bootstrap=False)
        return 0
    # b2 is bootstrapped inside this configuration's MSVC environment, which
    # is what lets a brand new Visual Studio work before b2 learns to find it.
    build_config = one(workspace, args)
    toolchain = toolchain_for(workspace, build_config, install_missing=True)
    source.prepare(workspace, env=toolchain.env, do_bootstrap=True)
    return 0


def cmd_toolchain(workspace, args):
    build_config = one(workspace, args)
    toolchain = toolchain_for(workspace, build_config,
                              install_missing=not args.no_install)
    print(json.dumps(toolchain.info, indent=2))
    return 0


def cmd_build(workspace, args):
    build_config = one(workspace, args)
    toolchain = toolchain_for(workspace, build_config)
    result = build_module.build(
        workspace, build_config, toolchain, jobs=args.b2_jobs,
        strict=args.strict,
        clean=False if args.keep_intermediate else None)
    _summarise_build(workspace, build_config, result)
    return 0


def cmd_test(workspace, args):
    build_config = one(workspace, args)
    if not workspace.config.smoke.enabled:
        log("smoke tests are disabled in {}".format(workspace.config.path))
        return 0
    toolchain = toolchain_for(workspace, build_config)
    summary = smoke.test(workspace, build_config, toolchain)
    _summarise_test(build_config, summary)
    return 0 if summary["ok"] else 1


def cmd_run(workspace, args):
    """prepare + build + test for one configuration."""
    build_config = one(workspace, args)
    if not args.no_fetch:
        source.fetch(workspace)
    toolchain = toolchain_for(workspace, build_config, install_missing=True)
    source.prepare(workspace, env=toolchain.env)
    result = build_module.build(workspace, build_config, toolchain,
                                jobs=args.b2_jobs, strict=args.strict)
    _summarise_build(workspace, build_config, result)
    if not workspace.config.smoke.enabled:
        return 0
    summary = smoke.test(workspace, build_config, toolchain)
    _summarise_test(build_config, summary)
    return 0 if summary["ok"] else 1


def cmd_all(workspace, args):
    """Build every selected configuration on this machine, then package."""
    configs = selected(workspace, args)
    log("{} configuration(s) to build in {}".format(
        len(configs), workspace.root))
    if not args.no_fetch:
        source.fetch(workspace)

    first_toolchain = toolchain_for(workspace, configs[0], install_missing=True)
    source.prepare(workspace, env=first_toolchain.env)

    failures = []
    with Timer("build all"):
        if args.jobs <= 1:
            for build_config in configs:
                if _run_one_inprocess(workspace, build_config, args) != 0:
                    failures.append(build_config.id)
        else:
            failures = _run_parallel(workspace, configs, args)

    if not args.no_package:
        package_module.package(workspace)

    if failures:
        log("{} configuration(s) reported problems:".format(len(failures)))
        for name in failures:
            log("  " + name)
        return 1
    return 0


def _run_one_inprocess(workspace, build_config, args):
    try:
        toolchain = toolchain_for(workspace, build_config,
                                  install_missing=True)
        build_module.build(workspace, build_config, toolchain,
                           jobs=args.b2_jobs, strict=args.strict)
        if not workspace.config.smoke.enabled:
            return 0
        summary = smoke.test(workspace, build_config, toolchain)
        return 0 if summary["ok"] else 1
    except SystemExit as error:
        log("error in {}: {}".format(build_config.id, error))
        return 1


def _run_parallel(workspace, configs, args):
    """Run configurations as separate processes, one log file each."""
    failures = []

    def worker(build_config):
        work = workspace.work(build_config)
        work.mkdir(parents=True, exist_ok=True)
        console = work / "console.log"
        command = [sys.executable, "-m", "boostwin",
                   "--config-file", str(workspace.config.path),
                   "--build-root", str(workspace.root),
                   "run", "--id", build_config.id, "--no-fetch"]
        if args.b2_jobs:
            command += ["--b2-jobs", str(args.b2_jobs)]
        if args.strict:
            command.append("--strict")
        log("start {}  (log: {})".format(build_config.id, console))
        with open(console, "w", encoding="utf-8", errors="replace") as handle:
            code = subprocess.call(command, stdout=handle,
                                   stderr=subprocess.STDOUT,
                                   cwd=str(workspace.config.root))
        log("{} {}".format("done " if code == 0 else "FAILED", build_config.id))
        return build_config.id, code

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for name, code in pool.map(worker, configs):
            if code != 0:
                failures.append(name)
    return failures


def cmd_package(workspace, args):
    if not args.no_fetch:
        source.fetch(workspace)
    source.prepare(workspace, do_bootstrap=False, with_deps=False)
    package_module.package(workspace, stages_root=args.stages,
                           per_config=not args.no_per_config,
                           full=not args.no_full,
                           checksums=not args.no_checksums,
                           discard_stages=args.discard_stages)
    return 0


def cmd_clean(workspace, args):
    targets = []
    if args.all:
        targets = [workspace.root]
    else:
        targets = [workspace.root / "work", workspace.stages, workspace.out]
        if args.source:
            targets.append(workspace.source_parent)
    for target in targets:
        if target.exists():
            log("removing {}".format(target))
            rmtree(target)
    return 0


# --------------------------------------------------------- reporting ---
def _summarise_build(workspace, build_config, result):
    github_summary("### {}\n\n- b2 exit code: `{}`\n- staged: {} files, {}\n"
                   "- build time: {}\n".format(
                       build_config.id, result["b2_exit_code"],
                       result["staged_files"],
                       format_size(result["staged_bytes"]),
                       format_duration(result["seconds"])))


def _summarise_test(build_config, summary):
    rows = ["| check | result | detail |", "| --- | --- | --- |"]
    for check in summary["checks"]:
        state = "skip" if check.get("skipped") else (
            "pass" if check["ok"] else "**fail**")
        rows.append("| {} | {} | {} |".format(
            check["name"], state, check.get("detail", "")))
    github_summary("\n".join(rows) + "\n")


# ------------------------------------------------------------ parsing ---
def add_selection_arguments(parser, single):
    parser.add_argument("--id", action="append",
                        help="configuration id, e.g. "
                             "msvc-14.3-64-release-static-shared")
    parser.add_argument("--toolset", action="append",
                        help="limit to these toolsets, e.g. 14.3")
    parser.add_argument("--arch", action="append",
                        help="limit to these architectures, e.g. 64")
    parser.add_argument("--variant", action="append",
                        help="limit to debug and/or release")
    parser.add_argument("--link", action="append",
                        help="limit to shared and/or static")
    parser.add_argument("--runtime-link", action="append",
                        dest="runtime_link",
                        help="limit to shared and/or static")
    if single:
        parser.description = (parser.description or "") + \
            "  The filters must narrow down to a single configuration."


def build_parser():
    parser = argparse.ArgumentParser(
        prog="boostwin",
        description="Build the Boost Windows binary release, one "
                    "configuration at a time.")
    parser.add_argument("--config-file", default=None,
                        help="path to build.toml (default: ./build.toml)")
    parser.add_argument("--build-root", default=None,
                        help="where to unpack and build (default: from "
                             "build.toml, or D:\\boostwin)")
    parser.add_argument("--release-version", default=None,
                        help="override [release].version, e.g. 93")
    parser.add_argument("--minor-version", default=None,
                        help="override [release].minor_version")
    parser.add_argument("--type", default=None,
                        help="override [release].type")
    parser.add_argument("--repo", default=None,
                        help="override [release].repo")
    parser.add_argument("--beta", default=None, help="override [release].beta")
    parser.add_argument("--rc", default=None, help="override [release].rc")
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", help="show the resolved configuration")
    info.set_defaults(handler=cmd_info)

    matrix = commands.add_parser(
        "matrix", help="list the build configurations")
    matrix.add_argument("--json", action="store_true",
                        help="print the matrix as JSON")
    matrix.add_argument("--github", action="store_true",
                        help="write the matrix to $GITHUB_OUTPUT")
    add_selection_arguments(matrix, single=False)
    matrix.set_defaults(handler=cmd_matrix)

    fetch = commands.add_parser(
        "fetch", help="download the source and dependency archives")
    fetch.add_argument("--force", action="store_true",
                       help="download even if the file is already there")
    fetch.set_defaults(handler=cmd_fetch)

    prepare = commands.add_parser(
        "prepare", help="unpack everything and build b2")
    prepare.add_argument("--no-fetch", action="store_true")
    prepare.add_argument("--no-bootstrap", action="store_true",
                         help="unpack only; no compiler needed")
    add_selection_arguments(prepare, single=True)
    prepare.set_defaults(handler=cmd_prepare)

    toolchain = commands.add_parser(
        "toolchain", help="locate the compiler and write user-config.jam")
    toolchain.add_argument("--no-install", action="store_true",
                           help="do not add missing Visual Studio components")
    add_selection_arguments(toolchain, single=True)
    toolchain.set_defaults(handler=cmd_toolchain)

    build = commands.add_parser("build", help="build one configuration")
    build.add_argument("--b2-jobs", type=int, default=None,
                       help="parallel b2 jobs (default: one per cpu)")
    build.add_argument("--strict", action="store_true",
                       help="fail if b2 reports any error")
    build.add_argument("--keep-intermediate", action="store_true",
                       help="keep bin.v2 after the build")
    add_selection_arguments(build, single=True)
    build.set_defaults(handler=cmd_build)

    test = commands.add_parser("test", help="smoke test one configuration")
    add_selection_arguments(test, single=True)
    test.set_defaults(handler=cmd_test)

    run = commands.add_parser(
        "run", help="prepare, build and test one configuration")
    run.add_argument("--b2-jobs", type=int, default=None)
    run.add_argument("--strict", action="store_true")
    run.add_argument("--no-fetch", action="store_true")
    add_selection_arguments(run, single=True)
    run.set_defaults(handler=cmd_run)

    everything = commands.add_parser(
        "all", help="build every configuration here, then package")
    everything.add_argument("--jobs", type=int, default=1,
                            help="how many configurations to build at once")
    everything.add_argument("--b2-jobs", type=int, default=None,
                            help="parallel b2 jobs within each configuration")
    everything.add_argument("--strict", action="store_true")
    everything.add_argument("--no-fetch", action="store_true")
    everything.add_argument("--no-package", action="store_true")
    add_selection_arguments(everything, single=False)
    everything.set_defaults(handler=cmd_all)

    package = commands.add_parser(
        "package", help="turn staged output into release artifacts")
    package.add_argument("--stages", default=None,
                         help="directory holding the staged libraries "
                              "(default: <build root>/stage)")
    package.add_argument("--no-per-config", action="store_true",
                         help="skip the per compiler/architecture zips")
    package.add_argument("--no-full", action="store_true",
                         help="skip the full .7z archive")
    package.add_argument("--no-checksums", action="store_true")
    package.add_argument("--no-fetch", action="store_true")
    package.add_argument("--discard-stages", action="store_true",
                         help="delete the staged copies once they have been "
                              "merged, to save disk on a small runner")
    package.set_defaults(handler=cmd_package)

    clean = commands.add_parser("clean", help="remove build output")
    clean.add_argument("--all", action="store_true",
                       help="remove the whole build root")
    clean.add_argument("--source", action="store_true",
                       help="also remove the unpacked source")
    clean.set_defaults(handler=cmd_clean)

    return parser


def main(argv=None):
    if sys.version_info < (3, 11):
        print("boostwin needs Python 3.11 or newer (it reads build.toml with "
              "tomllib); this is {}.{}".format(*sys.version_info[:2]),
              file=sys.stderr)
        return 1
    parser = build_parser()
    args = parser.parse_args(argv)
    workspace = make_workspace(args)
    try:
        return args.handler(workspace, args)
    except SystemExit as error:
        if isinstance(error.code, str):
            print(error.code, file=sys.stderr)
            return 1
        raise
