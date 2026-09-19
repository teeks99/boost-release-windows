"""Smoke tests run against the libraries a configuration just staged.

Three things are checked, in increasing order of how much they prove:

1. inventory  - every staged file is named the way this configuration says
   it should be, and no required library is missing;
2. compile    - a program that uses a dozen Boost libraries compiles, links
   purely through Boost's auto-linking, and runs;
3. python     - a Boost.Python extension module builds and imports.
"""
import json
import os
import re
import subprocess

from .util import Timer, log

# libboost_filesystem-vc143-mt-sgd-x64-1_92.lib
#   ^prefix ^stem      ^toolset ^tags   ^arch ^version
AUTOLINK_LINE = re.compile(r"Linking to lib file:\s*(\S+\.lib)")


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


def _runtime_flag(build_config):
    static_runtime = build_config.runtime_link == "static"
    debug = build_config.variant == "debug"
    if static_runtime:
        return "/MTd" if debug else "/MT"
    return "/MDd" if debug else "/MD"


def _compile_flags(workspace, build_config):
    config = workspace.config
    flags = [
        "/nologo", "/EHsc", "/W3", "/bigobj",
        "/std:" + config.smoke.std, "/Zc:__cplusplus",
        "/D_CRT_SECURE_NO_WARNINGS",
        # Makes the compiler print every library auto-linking asks for, which
        # is what turns this into a naming check as well as a link check.
        "/DBOOST_LIB_DIAGNOSTIC",
        _runtime_flag(build_config),
    ]
    if build_config.variant == "debug":
        flags += ["/Od", "/Zi", "/DDEBUG"]
    else:
        flags += ["/O2", "/DNDEBUG"]
    if build_config.link == "shared":
        flags.append("/DBOOST_ALL_DYN_LINK")
    return flags


def _run_environment(workspace, build_config, toolchain, extra_path=()):
    env = dict(toolchain.env)
    paths = [str(workspace.stage_lib(build_config))]
    paths += [str(p) for p in extra_path]
    env["PATH"] = os.pathsep.join(paths + [env.get("PATH", "")])
    return env


def check_compile_and_run(workspace, build_config, toolchain):
    """Build and run the smoke program against the staged libraries."""
    config = workspace.config
    sources = config.root / "smoke"
    work = workspace.work(build_config) / "smoke"
    work.mkdir(parents=True, exist_ok=True)
    executable = work / "boost_smoke.exe"

    command = [str(toolchain.cl)] + _compile_flags(workspace, build_config) + [
        "/I" + str(workspace.source),
        "/Fo" + str(work) + os.sep,
        "/Fd" + str(work / "boost_smoke.pdb"),
        "/Fe" + str(executable),
        str(sources / "smoke.cpp"),
        "/link",
        "/LIBPATH:" + str(workspace.stage_lib(build_config)),
    ]
    completed = subprocess.run(
        command, cwd=str(work), env=toolchain.env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = completed.stdout.decode("utf-8", errors="replace")
    print(output, flush=True)
    (work / "compile.log").write_text(output, encoding="utf-8")

    autolinked = sorted(set(AUTOLINK_LINE.findall(output)))
    if completed.returncode != 0:
        return {
            "name": "compile",
            "ok": False,
            "detail": "cl exited with {}".format(completed.returncode),
            "autolink": autolinked,
        }, None

    problems = _check_autolink(workspace, build_config, autolinked)
    if problems:
        for problem in problems:
            log("  autolink: " + problem)
        return {
            "name": "compile",
            "ok": False,
            "detail": "auto-linked names do not match the staged files",
            "problems": problems,
            "autolink": autolinked,
        }, executable

    log("auto-linked {} libraries".format(len(autolinked)))
    return {
        "name": "compile",
        "ok": True,
        "detail": "{} auto-linked libraries".format(len(autolinked)),
        "autolink": autolinked,
    }, executable


def _check_autolink(workspace, build_config, autolinked):
    """Every name auto-linking asked for must be a file we actually staged."""
    lib_dir = workspace.stage_lib(build_config)
    problems = []
    for name in autolinked:
        if not (lib_dir / name).exists():
            problems.append(
                "{} was requested but is not in {}".format(name, lib_dir))
    if not autolinked:
        problems.append("no libraries were auto-linked; "
                        "BOOST_LIB_DIAGNOSTIC produced nothing")
    return problems


def check_run(workspace, build_config, toolchain, executable):
    env = _run_environment(workspace, build_config, toolchain)
    work = executable.parent
    completed = subprocess.run(
        [str(executable)], cwd=str(work), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = completed.stdout.decode("utf-8", errors="replace")
    print(output, flush=True)
    (work / "run.log").write_text(output, encoding="utf-8")
    return {
        "name": "run",
        "ok": completed.returncode == 0,
        "detail": "exit code {}".format(completed.returncode),
        "output": output.splitlines()[-1] if output.strip() else "",
    }


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
    config = workspace.config
    python_root = workspace.python_root(build_config.arch)
    interpreter = python_root / "python.exe"
    if not interpreter.exists():
        return {"name": "python", "ok": True, "skipped": True,
                "detail": "no interpreter at {}".format(interpreter)}

    sources = config.root / "smoke"
    work = workspace.work(build_config) / "python"
    work.mkdir(parents=True, exist_ok=True)
    module = work / "boost_smoke_ext.pyd"

    command = [str(toolchain.cl)] + _compile_flags(workspace, build_config) + [
        "/LD",
        "/I" + str(workspace.source),
        "/I" + str(python_root / "include"),
        "/Fo" + str(work) + os.sep,
        "/Fd" + str(work / "boost_smoke_ext.pdb"),
        "/Fe" + str(module),
        str(sources / "python_ext.cpp"),
        "/link",
        "/LIBPATH:" + str(workspace.stage_lib(build_config)),
        "/LIBPATH:" + str(python_root / "libs"),
    ]
    completed = subprocess.run(
        command, cwd=str(work), env=toolchain.env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = completed.stdout.decode("utf-8", errors="replace")
    print(output, flush=True)
    if completed.returncode != 0:
        return {"name": "python", "ok": False,
                "detail": "building the extension failed with {}".format(
                    completed.returncode)}

    env = _run_environment(workspace, build_config, toolchain,
                           extra_path=[python_root])
    completed = subprocess.run(
        [str(interpreter), str(sources / "python_ext_test.py"), str(work)],
        cwd=str(work), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = completed.stdout.decode("utf-8", errors="replace")
    print(output, flush=True)
    return {"name": "python", "ok": completed.returncode == 0,
            "detail": output.strip().splitlines()[-1] if output.strip()
            else "exit code {}".format(completed.returncode)}


def test(workspace, build_config, toolchain):
    """Run every applicable check and write ``meta/test.json``."""
    config = workspace.config
    results = []
    with Timer("test " + build_config.id):
        inventory = check_inventory(workspace, build_config)
        results.append(inventory)

        compile_result, executable = check_compile_and_run(
            workspace, build_config, toolchain)
        results.append(compile_result)

        if compile_result["ok"] and executable is not None:
            results.append(check_run(workspace, build_config, toolchain,
                                     executable))

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
