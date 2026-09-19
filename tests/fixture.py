"""Build a fake build root: a stub Boost tree plus staged output.

Lets the configuration, inventory and packaging code be exercised on any
machine, including the Linux box a release is usually prepared from.
"""
import json
from pathlib import Path

from boostwin import smoke

# Libraries Boost only ever builds as static, even for link=shared.
STATIC_ONLY = ("exception", "test_exec_monitor")


def staged_file_names(config, build_config):
    """The file names a real build of this configuration would stage."""
    tags = [smoke.expected_toolset_tag(build_config)]
    tags += smoke.expected_option_tags(build_config)
    tags.append(smoke.expected_arch_tag(build_config))
    tags.append("1_" + config.release.version)
    tag = "-".join(tags)

    names = []
    for library in smoke.required_libraries(config, build_config):
        if build_config.link == "shared" and library not in STATIC_ONLY:
            names.append("boost_{}-{}.lib".format(library, tag))
            names.append("boost_{}-{}.dll".format(library, tag))
        else:
            names.append("libboost_{}-{}.lib".format(library, tag))
    return names


def write(root, config, configs=None, ok=True):
    """Populate ``root`` and return it."""
    root = Path(root)
    configs = configs if configs is not None else config.matrix()

    source = root / "src" / config.release.source_name
    (source / "boost").mkdir(parents=True, exist_ok=True)
    (source / "boost" / "version.hpp").write_text(
        "#define BOOST_VERSION 1{}00\n".format(config.release.version))
    (source / "Jamroot").write_text("# stub\n")

    for build_config in configs:
        stage = root / "stage" / build_config.id
        libraries = stage / build_config.lib_dir
        libraries.mkdir(parents=True, exist_ok=True)
        for name in staged_file_names(config, build_config):
            (libraries / name).write_text("x" * 128)

        meta = stage / "meta"
        meta.mkdir(parents=True, exist_ok=True)
        (meta / "build.log").write_text(
            "b2 output for {}\n".format(build_config.id))
        (meta / "build.json").write_text(json.dumps({
            "config": build_config.id,
            "b2_exit_code": 0 if ok else 1,
            "seconds": 1234.5,
            "staged_files": 10,
            "staged_bytes": 1280,
        }))
        checks = [
            {"name": "inventory", "ok": ok, "detail": "",
             "libraries": smoke.required_libraries(config, build_config)},
            {"name": "compile", "ok": ok, "detail": ""},
            {"name": "run", "ok": ok, "detail": ""},
        ]
        if smoke.python_extension_applies(config, build_config):
            checks.append({"name": "python", "ok": ok, "detail": ""})
        (meta / "test.json").write_text(json.dumps({
            "config": build_config.id,
            "toolset": build_config.toolset.name,
            "arch": build_config.arch.key,
            "variant": build_config.variant,
            "link": build_config.link,
            "runtime_link": build_config.runtime_link,
            "ok": ok,
            "checks": checks,
        }))
        (meta / "toolchain.json").write_text(json.dumps({
            "config": build_config.id,
            "toolset": build_config.toolset.name,
            "vs_display": build_config.toolset.vs_display,
            "vs_product": "Visual Studio Enterprise 2022",
            "vs_version": "17.14.37628.2",
            "msvc_toolset": "14.16.27023",
            "windows_sdk": "10.0.26100.0",
        }))
    return root
