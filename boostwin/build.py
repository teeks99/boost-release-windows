"""Running b2 for exactly one build configuration."""
import json
import multiprocessing

from . import source
from .util import Timer, fail, format_size, log, run, rmtree


def b2_command(workspace, build_config, jobs=None):
    config = workspace.config
    jobs = jobs or config.build.b2_jobs or multiprocessing.cpu_count()
    # Forward slashes throughout: Boost.Build treats a backslash as an escape
    # in some contexts, and it accepts posix separators everywhere.
    command = [
        str(workspace.b2),
        "-j{}".format(jobs),
        "--user-config=" + workspace.user_config(build_config).as_posix(),
        "--build-dir=" + workspace.build_dir(build_config).as_posix(),
        "--stage-libdir=" + workspace.stage_lib(build_config).as_posix(),
        # b2 names every intermediate directory after the whole property
        # set -- msvc-14.5/release/address-model-64/architecture-arm/
        # link-static/runtime-link-static/threadapi-win32/threading-multi,
        # 116 characters before the file name.  `architecture` only appears
        # when it is not the default, so an arm64 build runs 32 characters
        # longer than the same x64 one, and with a static runtime that took
        # the longest response file past Windows' 260 character MAX_PATH:
        # lib.exe failed with LNK1104, b2 carried on, and the library was
        # simply absent from the release.  --hash replaces that path with an
        # MD5 of itself, which costs readable bin.v2 directory names and
        # buys back about 80 characters.  The staged output is unaffected --
        # --stage-libdir above says where that goes.
        "--hash",
        "--build-type=" + config.build.build_type,
        "--layout=" + config.build.layout,
        "--debug-configuration",
    ]
    for library in config.build.without:
        command.append("--without-" + library)
    command += build_config.b2_properties
    command += list(config.build.extra_b2_args)
    command.append("stage")
    return command


def staged_files(workspace, build_config):
    lib_dir = workspace.stage_lib(build_config)
    if not lib_dir.is_dir():
        return []
    return sorted(p for p in lib_dir.iterdir() if p.is_file())


def build(workspace, build_config, toolchain, jobs=None, strict=False,
          clean=None):
    """Build and stage one configuration; returns a result dictionary."""
    config = workspace.config
    if not workspace.b2.exists():
        fail("b2 is missing; run 'prepare' first")

    stage_lib = workspace.stage_lib(build_config)
    stage_lib.mkdir(parents=True, exist_ok=True)
    log_file = workspace.build_log(build_config)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    if log_file.exists():
        log_file.unlink()  # one log per build, not one per attempt

    # b2 drives vcvarsall itself through the <setup> script in
    # user-config.jam, so it gets a clean environment to start from.
    env = source.build_environment(workspace)
    command = b2_command(workspace, build_config, jobs=jobs)

    with Timer("build " + build_config.id) as timer:
        code = run(command, cwd=workspace.source, env=env,
                   log_file=log_file, check=False)

    files = staged_files(workspace, build_config)
    total = sum(path.stat().st_size for path in files)
    log("staged {} files ({}) in {}".format(
        len(files), format_size(total), stage_lib))

    result = {
        "config": build_config.id,
        "b2_exit_code": code,
        "seconds": round(timer.elapsed, 1),
        "staged_files": len(files),
        "staged_bytes": total,
    }
    (workspace.meta(build_config) / "build.json").write_text(
        json.dumps(result, indent=2), encoding="utf-8")

    if code != 0:
        message = "b2 exited with {} for {}".format(code, build_config.id)
        if strict:
            fail(message)
        log("warning: " + message + " (see " + str(log_file) + ")")
    if not files:
        fail("nothing was staged into {}".format(stage_lib))

    if clean is None:
        clean = not config.build.keep_intermediate
    if clean:
        build_dir = workspace.build_dir(build_config)
        log("removing intermediates: {}".format(build_dir))
        rmtree(build_dir)

    return result
