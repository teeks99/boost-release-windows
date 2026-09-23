"""Smoke tests run against the libraries a configuration just staged.

Three things are checked, in increasing order of how much they prove:

1. inventory  - every staged file is named the way this configuration says
   it should be, and no required library is missing;
2. compile    - the checked-in Visual Studio project for this toolset
   (smoke/vs/msvc-<toolset>/BoostSmoke.vcxproj) builds a program that uses
   a dozen Boost libraries, linking purely through Boost's auto-linking,
   and it runs;
3. python     - a Boost.Python extension module, built the same way
   through BoostSmokePython.vcxproj, builds and imports.

Building through the checked-in project rather than a direct cl.exe
invocation is closer to how someone consuming these binaries from Visual
Studio actually builds against them, and it doubles as a check that the
project's own include/lib paths (via the .props boostwin.vsproj writes) are
still correct.
"""
import json
import os
import re
import subprocess
from pathlib import Path

from . import vsproj
from .util import Timer, log

# libboost_filesystem-vc143-mt-sgd-x64-1_92.lib
#   ^prefix ^stem      ^toolset ^tags   ^arch ^version
AUTOLINK_LINE = re.compile(r"Linking to lib file:\s*(\S+\.lib)")


def classify_autolink(lib_dir, names):
    """Split the libraries auto-linking asked for into Boost and system ones.

    Not every auto-linked library is ours: Boost.Atomic pulls in the Windows
    SDK's synchronization.lib for WaitOnAddress, for instance.  Only the Boost
    ones have to be in the staged directory.
    """
    boost = []
    system = []
    problems = []
    for name in names:
        # BOOST_LIB_DIAGNOSTIC stringizes an already quoted system library
        # name, so it can arrive as "synchronization".lib.
        name = name.replace('"', "")
        if re.match(r"^(lib)?boost_", name):
            boost.append(name)
            if not (Path(lib_dir) / name).exists():
                problems.append(
                    "{} was requested but is not in {}".format(name, lib_dir))
        else:
            system.append(name)
    if not boost:
        problems.append("no Boost libraries were auto-linked; "
                        "BOOST_LIB_DIAGNOSTIC produced nothing")
    return sorted(set(boost)), sorted(set(system)), problems


def parse_library_name(filename):
    """Split a staged Boost library file name into its parts, or None."""
    stem, extension = os.path.splitext(filename)
    if not extension:
        return None
    parts = stem.split("-")
    if len(parts) < 4:
        return None
    name = parts[0]
    for prefix in ("libboost_", "boost_"):
        if name.startswith(prefix):
            library = name[len(prefix):]
            break
    else:
        return None
    return {
        "file": filename,
        "library": library,
        "static_prefix": name.startswith("lib"),
        "toolset_tag": parts[1],
        "option_tags": parts[2:-2],
        "arch_tag": parts[-2],
        "version_tag": parts[-1],
        "extension": extension.lstrip("."),
    }


def expected_toolset_tag(build_config):
    return "vc" + build_config.toolset.name.replace(".", "")


def expected_option_tags(build_config):
    """The ``-mt``/``-gd``/``-s``/``-sgd`` part of a staged file name."""
    tags = []
    if build_config.threading == "multi":
        tags.append("mt")
    options = ""
    if build_config.runtime_link == "static":
        options += "s"
    if build_config.variant == "debug":
        options += "gd"
    if options:
        tags.append(options)
    return tags


def expected_arch_tag(build_config):
    letter = "a" if build_config.arch.architecture == "arm" else "x"
    return letter + build_config.arch.address_model


def required_libraries(config, build_config):
    """Library stems this configuration must produce."""
    substitutions = {"python_tag": config.deps.python_tag}
    required = [name.format(**substitutions)
                for name in config.smoke.required_libs]
    for entry in config.smoke.conditional_libs:
        if entry.applies_to(build_config):
            required += [name.format(**substitutions) for name in entry.libs]
    return sorted(set(required))


def check_inventory(workspace, build_config):
    """Verify what was staged is named for this configuration and complete."""
    config = workspace.config
    lib_dir = workspace.stage_lib(build_config)
    if not lib_dir.is_dir():
        return {"name": "inventory", "ok": False,
                "detail": "nothing was staged in {}".format(lib_dir),
                "problems": ["the staged directory does not exist"],
                "libraries": [], "files": 0}
    files = sorted(p.name for p in lib_dir.iterdir() if p.is_file())

    want_toolset = expected_toolset_tag(build_config)
    want_options = expected_option_tags(build_config)
    want_arch = expected_arch_tag(build_config)

    found = set()
    problems = []
    unparsed = []
    for filename in files:
        if filename in ("DEPENDENCY_VERSIONS.txt",):
            continue
        parsed = parse_library_name(filename)
        if parsed is None:
            unparsed.append(filename)
            continue
        found.add(parsed["library"])
        if parsed["toolset_tag"] != want_toolset:
            problems.append("{}: toolset tag {}, expected {}".format(
                filename, parsed["toolset_tag"], want_toolset))
        if parsed["option_tags"] != want_options:
            problems.append("{}: option tags {}, expected {}".format(
                filename, "-".join(parsed["option_tags"]) or "(none)",
                "-".join(want_options) or "(none)"))
        if parsed["arch_tag"] != want_arch:
            problems.append("{}: arch tag {}, expected {}".format(
                filename, parsed["arch_tag"], want_arch))

    required = required_libraries(config, build_config)
    missing = [name for name in required if name not in found]
    if missing:
        problems.append("missing libraries: " + ", ".join(missing))
    if unparsed:
        problems.append("unrecognised file names: " + ", ".join(unparsed))
    if not found:
        problems.append("no Boost libraries were staged at all")

    detail = "{} files, {} libraries".format(len(files), len(found))
    for problem in problems:
        log("  inventory: " + problem)
    return {
        "name": "inventory",
        "ok": not problems,
        "detail": detail,
        "problems": problems,
        "libraries": sorted(found),
        "files": len(files),
    }


def extra_link_libraries(lib_dir, stems):
    """Locate the staged file for each library that must be named explicitly.

    A few Boost libraries call into another without auto-linking it -- most
    notably Boost.JSON, whose compiled code calls boost::charconv::to_chars
    while only including charconv's detail config, which carries no autolink
    block.  A shared build hides this inside the DLL; a static one does not.
    """
    lib_dir = Path(lib_dir)
    resolved = []
    problems = []
    for stem in stems:
        matches = sorted(lib_dir.glob("*boost_{}-*.lib".format(stem)))
        if matches:
            resolved.append(matches[0])
        else:
            problems.append(
                "boost_{} has to be linked explicitly but was not staged in "
                "{}".format(stem, lib_dir))
    return resolved, problems


# The error msbuild reports for a PostBuildEvent (or any other <Exec>-based
# build step) that exited non-zero.  BoostSmoke.vcxproj and
# BoostSmokePython.vcxproj both run the program they just built as their
# post-build step, so this is what tells "it failed to build" apart from
# "it built, but running it failed" without a second process of our own.
_POST_BUILD_FAILED = re.compile(r"\bMSB3073\b")


def _msbuild(workspace, build_config, toolchain, vcxproj, log_name):
    """Run msbuild against ``vcxproj`` for this configuration.

    Verbosity is left at the default (normal) rather than turned down:
    both the BOOST_LIB_DIAGNOSTIC `#pragma message` lines and the smoke
    program's own output, echoed by the post-build step, need to survive
    in what gets captured. Returns ``(captured_output, returncode)``.
    """
    msbuild = vsproj.find_msbuild(toolchain.info["vs_path"])
    configuration = vsproj.configuration_name(build_config)
    platform = vsproj.platform_name(build_config)

    work = workspace.work(build_config) / "smoke"
    work.mkdir(parents=True, exist_ok=True)
    command = [str(msbuild), str(vcxproj), "/nologo",
               "/p:Configuration=" + configuration,
               "/p:Platform=" + platform]
    completed = subprocess.run(
        command, cwd=str(vcxproj.parent), env=toolchain.env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = completed.stdout.decode("utf-8", errors="replace")
    print(output, flush=True)
    (work / log_name).write_text(output, encoding="utf-8")
    return output, completed.returncode


def check_compile_and_run(workspace, build_config, toolchain):
    """Build and run the smoke program through the checked-in VS project.

    Both happen inside one msbuild invocation: BoostSmoke.vcxproj runs the
    program itself, as a post-build step, once it links successfully.
    Nothing here names a library file except the few `extra_link_libs`
    Boost does not auto-link, so a successful build is itself the check
    that library naming and this build variant agree; it is compiled with
    BOOST_LIB_DIAGNOSTIC, so msbuild's own captured output can be scanned
    for every name the compiler asked for.

    Returns ``(compile_result, run_result)``; ``run_result`` is ``None``
    when the build never got far enough to run anything.
    """
    config = workspace.config
    if not vsproj.has_project(config, build_config):
        return {"name": "compile", "ok": False,
                "detail": "no Visual Studio project checked in for "
                          "msvc-{}; add one under smoke/vs/".format(
                              build_config.toolset.name)}, None

    lib_dir = workspace.stage_lib(build_config)
    extra, extra_problems = extra_link_libraries(
        lib_dir, config.smoke.extra_link_libs)
    if extra_problems:
        for problem in extra_problems:
            log("  link: " + problem)
        return {"name": "compile", "ok": False,
                "detail": "a library that has to be linked explicitly is "
                          "missing",
                "problems": extra_problems}, None

    vsproj.write_props(workspace, build_config)
    output, returncode = _msbuild(
        workspace, build_config, toolchain,
        vsproj.vcxproj_path(config, build_config.toolset), "compile.log")

    requested = sorted(set(AUTOLINK_LINE.findall(output)))
    post_build_failed = bool(_POST_BUILD_FAILED.search(output))
    if returncode != 0 and not post_build_failed:
        return {
            "name": "compile",
            "ok": False,
            "detail": "msbuild exited with {}".format(returncode),
            "autolink": requested,
        }, None

    boost, system, problems = classify_autolink(lib_dir, requested)
    detail = "{} Boost libraries auto-linked".format(len(boost))
    if system:
        detail += ", plus {} from the SDK".format(len(system))
    if problems:
        for problem in problems:
            log("  autolink: " + problem)
        return {
            "name": "compile",
            "ok": False,
            "detail": "auto-linked names do not match the staged files",
            "problems": problems,
            "autolink": boost,
            "system_libraries": system,
        }, None

    if not vsproj.output_exe(config, build_config).exists():
        return {"name": "compile", "ok": False,
                "detail": "msbuild succeeded but the executable was not "
                          "produced",
                "autolink": boost, "system_libraries": system}, None

    log(detail)
    compile_result = {
        "name": "compile",
        "ok": True,
        "detail": detail,
        "autolink": boost,
        "system_libraries": system,
    }
    run_result = {
        "name": "run",
        "ok": not post_build_failed,
        "detail": "ran as the project's post-build step" if not post_build_failed
        else "the smoke program exited with a non-zero status; see "
             "compile.log",
    }
    return compile_result, run_result


def python_extension_applies(config, build_config):
    """Boost.Python only makes sense against a shared release runtime.

    The dependency Python packages carry no debug binaries, so a debug or
    static-runtime extension could not be built or loaded anyway.
    """
    return (config.smoke.python_extension
            and build_config.variant == "release"
            and build_config.link == "shared"
            and build_config.runtime_link == "shared")


def check_python_extension(workspace, build_config, toolchain):
    """Build and import the Boost.Python extension via the checked-in project.

    Both happen inside one msbuild invocation: BoostSmokePython.vcxproj's
    post-build step imports the module with the matching interpreter and
    exercises it, which is the only way to tell that boost_python was
    actually built against the right interpreter -- a library that merely
    exists in the staged directory can still be unusable.
    """
    config = workspace.config
    python_root = workspace.python_root(build_config.arch)
    if not (python_root / "python.exe").exists():
        return {"name": "python", "ok": True, "skipped": True,
                "detail": "no interpreter at {}".format(
                    python_root / "python.exe")}
    if not vsproj.has_project(config, build_config):
        return {"name": "python", "ok": False,
                "detail": "no Visual Studio project checked in for "
                          "msvc-{}; add one under smoke/vs/".format(
                              build_config.toolset.name)}

    vsproj.write_props(workspace, build_config)
    output, returncode = _msbuild(
        workspace, build_config, toolchain,
        vsproj.python_vcxproj_path(config, build_config.toolset),
        "python-compile.log")
    if returncode == 0:
        return {"name": "python", "ok": True,
                "detail": "built and imported successfully"}
    if _POST_BUILD_FAILED.search(output):
        return {"name": "python", "ok": False,
                "detail": "the extension failed to import; see "
                          "python-compile.log"}
    return {"name": "python", "ok": False,
            "detail": "building the extension failed with {}".format(
                returncode)}


def test(workspace, build_config, toolchain):
    """Run every applicable check and write ``meta/test.json``."""
    config = workspace.config
    results = []
    with Timer("test " + build_config.id):
        inventory = check_inventory(workspace, build_config)
        results.append(inventory)

        compile_result, run_result = check_compile_and_run(
            workspace, build_config, toolchain)
        results.append(compile_result)
        if run_result is not None:
            results.append(run_result)

        if python_extension_applies(config, build_config):
            results.append(check_python_extension(
                workspace, build_config, toolchain))

    summary = {
        "config": build_config.id,
        "toolset": build_config.toolset.name,
        "arch": build_config.arch.key,
        "variant": build_config.variant,
        "link": build_config.link,
        "runtime_link": build_config.runtime_link,
        "ok": all(result["ok"] for result in results),
        "checks": results,
    }
    target = workspace.test_json(build_config)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    for result in results:
        log("  {:<10} {}  {}".format(
            result["name"],
            "skip" if result.get("skipped") else ("ok" if result["ok"] else "FAIL"),
            result.get("detail", "")))
    return summary
