"""Checking a staged directory's naming and completeness.

No compiler is involved: this only looks at the file names a build already
staged and compares them against what this configuration -- and
``build.toml``'s ``[smoke].required_libs``/``conditional_libs`` -- says
should be there. That makes it the one check in the release pipeline that
runs anywhere, including the Linux box a release is usually prepared from,
which is why it lives apart from :mod:`boostwin.smoke`'s compiler-driven
checks.
"""
import os

from .util import log


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
    """The ``-mt``/``-gd``/``-s``/``-sgd`` part of a staged file name.

    One definition, on the configuration itself, because the work directory
    is named with the same tag -- so what this checks against the staged
    files and what that directory is called cannot drift apart.
    """
    return list(build_config.option_tags)


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
