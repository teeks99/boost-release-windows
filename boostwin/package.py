"""Turning per-configuration staged directories into release artifacts.

This step never needs a compiler, so it runs on any machine: on a build VM it
reads the ``stage`` directory the local builds wrote, and in CI it reads the
directory the build jobs' artifacts were downloaded into.
"""
import json
import shutil
import zipfile
from pathlib import Path

from .util import (Timer, fail, format_size, log, merge_tree, rmtree, run,
                   sha256)

LIB_DIR_GLOB = "lib*-msvc-*"


def find_stage_dirs(stages_root):
    """Locate every staged ``libNN-msvc-X.Y`` directory under ``stages_root``.

    Searched rather than assumed, because the layout differs between a local
    build tree and a directory of downloaded CI artifacts.
    """
    stages_root = Path(stages_root)
    if not stages_root.is_dir():
        fail("no staged output at {}".format(stages_root))
    found = []
    for depth in ("", "*/", "*/*/"):
        found += [p for p in stages_root.glob(depth + LIB_DIR_GLOB)
                  if p.is_dir()]
    unique = {}
    for path in found:
        unique.setdefault(str(path), path)
    return sorted(unique.values(), key=lambda p: (p.name, str(p)))


def find_meta(stages_root, filename):
    stages_root = Path(stages_root)
    return sorted(stages_root.glob("**/meta/" + filename))


def load_json_meta(stages_root, filename):
    records = []
    for path in find_meta(stages_root, filename):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as error:
            log("warning: could not read {}: {}".format(path, error))
    return records


def _config_id_for(meta_dir, by_id):
    """Work out which configuration a meta directory belongs to.

    The directory name is the artifact name in CI and the configuration id
    locally, so prefer what the configuration wrote about itself.
    """
    for name in ("toolchain.json", "build.json", "test.json"):
        path = meta_dir / name
        if path.exists():
            try:
                recorded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if recorded.get("config") in by_id:
                return recorded["config"]
    directory = meta_dir.parent.name
    matches = [key for key in by_id if key in directory]
    if matches:
        return max(matches, key=len)
    return directory


def describe_lib_dirs(lib_dirs):
    """Split ``lib64-msvc-14.3`` names back into toolsets and architectures."""
    toolsets = []
    archs = []
    for name in lib_dirs:
        arch, _, toolset = name.partition("-msvc-")
        arch = arch[len("lib"):]
        if toolset and toolset not in toolsets:
            toolsets.append(toolset)
        if arch and arch not in archs:
            archs.append(arch)
    return sorted(toolsets), sorted(archs)


def full_archive_name(config, lib_dirs):
    """Name the everything archive after what is actually inside it.

    A build of part of the matrix -- one compiler while trying something out,
    or a rerun of the configurations that failed -- must not produce a file
    called ``msvc-all`` that only holds one library directory.
    """
    toolsets, archs = describe_lib_dirs(lib_dirs)
    complete = (toolsets == sorted(t.name for t in config.toolsets)
                and archs == sorted(config.arch_keys))
    label = "all" if complete else "partial-" + "-".join(toolsets)
    return "{}-bin-msvc-{}-{}.7z".format(
        config.release.release_name, label, "-".join(archs))


def missing_lib_dirs(config, lib_dirs):
    """Library directories the full matrix would have produced but did not."""
    expected = []
    for build_config in config.matrix():
        if build_config.lib_dir not in expected:
            expected.append(build_config.lib_dir)
    return [name for name in expected if name not in lib_dirs]


def merge_into_source(workspace, stages_root):
    """Copy every staged library directory into the Boost source tree."""
    source = workspace.source
    if not source.is_dir():
        fail("the Boost source tree is missing at {}; run 'prepare' first"
             .format(source))
    merged = {}
    for stage_dir in find_stage_dirs(stages_root):
        target = source / stage_dir.name
        merge_tree(stage_dir, target)
        merged.setdefault(stage_dir.name, 0)
        merged[stage_dir.name] += 1
    if not merged:
        fail("no {} directories found under {}".format(
            LIB_DIR_GLOB, stages_root))
    for name in sorted(merged):
        count = len(list((source / name).iterdir()))
        log("merged {} configuration(s) into {} ({} files)".format(
            merged[name], name, count))
    return sorted(merged)


def dependency_versions(config, toolchains):
    """Build DEPENDENCY_VERSIONS.txt from what the builds actually used."""
    release = config.release
    lines = [
        "Boost {} ({})".format(release.dotted_version, release.release_name),
        "Source: {}".format(release.source_url),
        "",
        "Python: {} (used for Boost.Python)".format(config.deps.python),
        "zlib: {}".format(config.deps.zlib),
        "bzip2: {}".format(config.deps.bzip2),
        "",
        "Compilers:",
    ]
    seen = {}
    for record in toolchains:
        key = record.get("toolset", "?")
        if key in seen:
            continue
        seen[key] = record
    for key in sorted(seen):
        record = seen[key]
        lines.append(
            "  msvc-{} - {} - MSVC {} - {} {} - Windows SDK {}".format(
                key,
                record.get("vs_display", ""),
                record.get("msvc_toolset", "?"),
                record.get("vs_product", "?"),
                record.get("vs_version", "?"),
                record.get("windows_sdk", "?") or "(default)"))
    lines.append("")
    return "\n".join(lines)


def write_dependency_versions(workspace, stages_root, lib_dirs):
    config = workspace.config
    toolchains = load_json_meta(stages_root, "toolchain.json")
    text = dependency_versions(config, toolchains)
    root_file = workspace.source / "DEPENDENCY_VERSIONS.txt"
    root_file.write_text(text, encoding="utf-8")
    for name in lib_dirs:
        shutil.copy(root_file, workspace.source / name / "DEPENDENCY_VERSIONS.txt")
    (workspace.out / "DEPENDENCY_VERSIONS.txt").write_text(text, encoding="utf-8")
    log("wrote DEPENDENCY_VERSIONS.txt for {} library directories"
        .format(len(lib_dirs)))
    return text


def collect_logs(workspace, stages_root):
    """Concatenate each configuration's b2 log into one file per architecture."""
    config = workspace.config
    by_id = {build_config.id: build_config for build_config in config.matrix()}
    by_arch = {}
    for path in find_meta(stages_root, "build.log"):
        config_id = _config_id_for(path.parent, by_id)
        build_config = by_id.get(config_id)
        arch = build_config.arch.key if build_config else "unknown"
        by_arch.setdefault(arch, []).append((config_id, path))

    written = []
    for arch, entries in sorted(by_arch.items()):
        target = workspace.out / "{}-{}bitlog.txt".format(
            config.release.release_name, arch)
        with open(target, "w", encoding="utf-8", errors="replace") as out:
            for config_id, path in sorted(entries):
                out.write("\n{}\n=== {} ===\n{}\n".format(
                    "=" * 78, config_id, "=" * 78))
                out.write(path.read_text(encoding="utf-8", errors="replace"))
        log("wrote {} ({})".format(target.name,
                                   format_size(target.stat().st_size)))
        written.append(target)
    return written


def _inventory_libraries(record):
    for check in record.get("checks", ()):
        if check.get("name") == "inventory":
            return set(check.get("libraries", ()))
    return set()


def _compare_group(label, configs):
    """Report libraries present in some members of a group but not others."""
    problems = []
    union = set()
    for libraries in configs.values():
        union |= libraries
    for config_id, libraries in sorted(configs.items()):
        # Boost.Python is deliberately absent from the static-runtime
        # variants, so it is never evidence of an inconsistency.
        missing = {name for name in union - libraries
                   if not name.startswith("python")}
        if missing:
            problems.append("{} ({}): missing {}".format(
                config_id, label, ", ".join(sorted(missing))))
    return problems


def check_consistency(workspace, stages_root):
    """Configurations that should match each other had better match.

    Two groupings catch two different failures: comparing the variants of one
    compiler and architecture catches a library that stopped building for,
    say, the static-runtime debug build, and comparing the same variant across
    compilers catches one that stopped building for a single toolset.
    """
    tests = load_json_meta(stages_root, "test.json")
    by_target = {}
    by_variant = {}
    for record in tests:
        config_id = record.get("config")
        libraries = _inventory_libraries(record)
        target = "msvc-{} {}".format(record.get("toolset"), record.get("arch"))
        variant = "{} {} {}".format(record.get("variant"), record.get("link"),
                                    record.get("runtime_link"))
        by_target.setdefault(target, {})[config_id] = libraries
        by_variant.setdefault(variant, {})[config_id] = libraries

    problems = []
    for label, configs in sorted(by_target.items()):
        problems += _compare_group(label, configs)
    for label, configs in sorted(by_variant.items()):
        problems += _compare_group(label, configs)

    for problem in sorted(set(problems)):
        log("warning: inconsistent libraries -- " + problem)
    return problems


def result_matrix(workspace, stages_root):
    """Regenerate result_matrix.txt from the smoke test results."""
    config = workspace.config
    tests = {record.get("config"): record
             for record in load_json_meta(stages_root, "test.json")}
    builds = {record.get("config"): record
              for record in load_json_meta(stages_root, "build.json")}

    header = "{:<10} {:<5} {:<8} {:<7} {:<13} {:<6} {:<10} {:<8} {:<4} {:<7}".format(
        "toolset", "arch", "variant", "link", "runtime-link", "build",
        "inventory", "compile", "run", "python")
    lines = ["", header, "-" * len(header)]
    for build_config in config.matrix():
        record = tests.get(build_config.id)
        build_record = builds.get(build_config.id)

        def mark(name):
            if record is None:
                return "?"
            for check in record.get("checks", ()):
                if check.get("name") == name:
                    if check.get("skipped"):
                        return "-"
                    return "X" if check.get("ok") else "FAIL"
            return "-"

        if build_record is None:
            build_mark = "?"
        else:
            build_mark = "X" if build_record.get("b2_exit_code") == 0 else "warn"

        lines.append(
            "{:<10} {:<5} {:<8} {:<7} {:<13} {:<6} {:<10} {:<8} {:<4} {:<7}".format(
                "msvc-" + build_config.toolset.name,
                build_config.arch.key,
                build_config.variant,
                build_config.link,
                build_config.runtime_link,
                build_mark,
                mark("inventory"),
                mark("compile"),
                mark("run"),
                mark("python")))

    lines += [
        "",
        "build      b2 finished with exit code 0 ('warn' means some targets "
        "failed; see the build log)",
        "inventory  every staged file is named for this configuration and no "
        "required library is missing",
        "compile    the smoke program compiled and every auto-linked name "
        "resolved to a staged file",
        "run        the smoke program ran and all of its checks passed",
        "python     a Boost.Python extension module built and imported "
        "('-' where that does not apply)",
        "",
    ]
    text = "\n".join(lines)
    (workspace.out / "result_matrix.txt").write_text(text, encoding="utf-8")
    return text


def make_zip(workspace, directory, target):
    """Zip ``directory`` so it unpacks as ``<directory name>/...``."""
    directory = Path(directory)
    target = Path(target)
    if target.exists():
        target.unlink()
    sevenzip = workspace.sevenzip
    if sevenzip:
        run([sevenzip, "a", "-tzip", "-mx=5", "-mmt=on", "-bso0", "-bsp0",
             str(target), directory.name],
            cwd=directory.parent)
    else:
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED,
                             compresslevel=6) as archive:
            for path in sorted(directory.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(directory.parent))
    log("wrote {} ({})".format(target.name, format_size(target.stat().st_size)))
    return target


def make_7z(workspace, directory, target, extra_args=()):
    sevenzip = workspace.sevenzip
    if not sevenzip:
        log("warning: no 7-Zip binary available, skipping {}".format(
            Path(target).name))
        return None
    directory = Path(directory)
    target = Path(target)
    if target.exists():
        target.unlink()
    run([sevenzip, "a", "-bso0", "-bsp0", *extra_args, str(target),
         directory.name],
        cwd=directory.parent)
    log("wrote {} ({})".format(target.name, format_size(target.stat().st_size)))
    return target


def write_checksums(workspace):
    files = sorted(p for p in workspace.out.iterdir()
                   if p.is_file() and p.name != "SHA256SUMS")
    lines = ["{}  {}".format(sha256(path), path.name) for path in files]
    target = workspace.out / "SHA256SUMS"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    log("wrote SHA256SUMS for {} files".format(len(files)))
    return target


def package(workspace, stages_root=None, per_config=True, full=True,
            checksums=True, discard_stages=False):
    """Produce every release artifact from the staged build output."""
    config = workspace.config
    stages_root = Path(stages_root) if stages_root else workspace.stages
    workspace.out.mkdir(parents=True, exist_ok=True)

    with Timer("package"):
        lib_dirs = merge_into_source(workspace, stages_root)
        absent = missing_lib_dirs(config, lib_dirs)
        if absent:
            log("warning: this is a partial build -- nothing was staged for "
                + ", ".join(absent))
            log("warning: the archives are named accordingly and are not a "
                "complete release")
        write_dependency_versions(workspace, stages_root, lib_dirs)
        collect_logs(workspace, stages_root)
        check_consistency(workspace, stages_root)
        print(result_matrix(workspace, stages_root))

        if discard_stages:
            # Everything worth keeping has been merged or summarised by now,
            # and on a CI runner this is the difference between fitting on
            # disk and not.
            log("discarding staged copies under {}".format(stages_root))
            rmtree(stages_root)

        if per_config:
            for name in lib_dirs:
                # lib64-msvc-14.3 -> boost_1_93_0-bin-msvc-14.3-64.zip
                arch, _, toolset = name.partition("-msvc-")
                arch = arch[len("lib"):]
                target = workspace.out / "{}-bin-msvc-{}-{}.zip".format(
                    config.release.release_name, toolset, arch)
                make_zip(workspace, workspace.source / name, target)

        if full:
            make_7z(workspace, workspace.source,
                    workspace.out / full_archive_name(config, lib_dirs))

        if checksums:
            write_checksums(workspace)

    log("artifacts in {}".format(workspace.out))
    for path in sorted(workspace.out.iterdir()):
        log("  {:<55} {}".format(path.name, format_size(path.stat().st_size)))
    return workspace.out
