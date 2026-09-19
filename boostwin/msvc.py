"""Finding, installing and activating the MSVC toolset for a configuration.

GitHub's runner images ship a single Visual Studio, but each one carries
several MSVC toolsets side by side and the installer can add more.  So rather
than assuming "one Visual Studio per compiler version" the way a hand-built VM
does, a configuration names a Visual Studio version range plus the toolset
version to select inside it with ``vcvarsall.bat -vcvars_ver=``.
"""
import json
import os
import platform
import re
import subprocess
import time
from pathlib import Path

from .util import WINDOWS, fail, log, run

# vs_installer exit codes that mean "done", possibly wanting a reboot.
INSTALLER_OK = {0, 3010, 1641}

HOST_DIR = {"amd64": "x64", "x86": "x86", "arm64": "arm64"}
TARGET_DIR = {"x64": "x64", "x86": "x86", "arm64": "arm64", "arm": "arm"}
# vcvarsall.bat spells the 64 bit host/target "amd64".
VCVARS_TOKEN = {"x64": "amd64", "x86": "x86", "arm64": "arm64", "arm": "arm"}


def host_arch():
    # PROCESSOR_ARCHITEW6432 is set when a 32 bit process is running on a
    # 64 bit Windows, and is the one that describes the machine.
    machine = (os.environ.get("PROCESSOR_ARCHITEW6432")
               or os.environ.get("PROCESSOR_ARCHITECTURE")
               or platform.machine() or "").lower()
    if machine in ("arm64", "aarch64"):
        return "arm64"
    if machine in ("x86", "i386", "i686"):
        return "x86"
    return "amd64"


def program_files_x86():
    return Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))


def vswhere_path():
    path = program_files_x86() / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not path.exists():
        fail("vswhere.exe not found at {}; is Visual Studio installed?"
             .format(path))
    return path


def _version_key(text):
    return tuple(int(part) for part in re.findall(r"\d+", text))


# A NuGet style range, as Visual Studio spells it: [17.0,18.0), [15.0,), ...
VERSION_RANGE = re.compile(
    r"^\s*([\[(])\s*([0-9][0-9.]*)?\s*,\s*([0-9][0-9.]*)?\s*([\])])\s*$")


def version_in_range(version, version_range):
    """Is ``version`` inside a range like ``[17.0,18.0)``?

    Done here rather than with vswhere's own ``-version`` option so that a
    build that finds no compiler can report what it did find.
    """
    if not version:
        return False
    matched = VERSION_RANGE.match(version_range)
    if not matched:
        # A bare version means "this one or newer", the same as vswhere.
        return _version_key(version) >= _version_key(version_range)
    low_bracket, low, high, high_bracket = matched.groups()
    key = _version_key(version)
    if low:
        low_key = _version_key(low)
        if key < low_key or (key == low_key and low_bracket == "("):
            return False
    if high:
        high_key = _version_key(high)
        if key > high_key or (key == high_key and high_bracket == ")"):
            return False
    return True


def _vswhere(arguments):
    """Run vswhere, retrying without -utf8 for older copies that reject it."""
    base = [str(vswhere_path())] + list(arguments)
    attempts = [base + ["-utf8"], base]
    for index, command in enumerate(attempts):
        completed = subprocess.run(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        if completed.returncode == 0:
            return stdout
        if index + 1 < len(attempts):
            continue
        stderr = completed.stderr.decode("utf-8", errors="replace")
        fail("vswhere exited with {}: {}\n{}\n{}".format(
            completed.returncode, subprocess.list2cmdline(command),
            stdout.strip(), stderr.strip()))


def visual_studio_instances():
    """Every Visual Studio vswhere reports, newest first.

    ``-all`` is deliberate: an installation the installer considers to have
    problems is still perfectly able to compile Boost, and is hidden from the
    default query.
    """
    text = _vswhere(["-products", "*", "-all", "-prerelease",
                     "-format", "json"]).strip()
    if not text:
        return []
    try:
        instances = json.loads(text)
    except json.JSONDecodeError:
        fail("could not parse vswhere output:\n" + text)
    instances.sort(
        key=lambda instance: _version_key(
            instance.get("installationVersion", "0")),
        reverse=True)
    return instances


def describe_instance(instance):
    return "{} {} at {}".format(
        instance.get("displayName") or instance.get("productId", "?"),
        instance.get("installationVersion", "?"),
        instance.get("installationPath", "?"))


def find_visual_studio(version_range):
    """The newest Visual Studio whose version falls inside ``version_range``."""
    for instance in visual_studio_instances():
        if version_in_range(instance.get("installationVersion", ""),
                            version_range):
            return instance
    return None


def require_visual_studio(toolset):
    instance = find_visual_studio(toolset.vs_version_range)
    if not instance:
        found = visual_studio_instances()
        listing = "\n".join("  " + describe_instance(i) for i in found)
        fail("no Visual Studio matching {} found for toolset msvc-{}.\n"
             "vswhere reported {} installation(s):\n{}".format(
                 toolset.vs_version_range, toolset.name, len(found),
                 listing or "  (none)"))
    log("Visual Studio: " + describe_instance(instance))
    return instance


def install_components(toolset, instance, timeout=1800):
    """Add this toolset's Visual Studio components if it is not there yet.

    Whether the toolset is present is decided by looking for its directory
    rather than by asking vswhere about a component id: a component can be
    installed as part of a group under a different id, and the directory is
    what the build actually needs.
    """
    if not toolset.install_components:
        return instance
    install_path = instance["installationPath"]
    if find_toolset_directory(install_path, toolset.vcvars_ver) is not None:
        log("MSVC toolset {} is already installed".format(
            toolset.vcvars_ver or "(default)"))
        return instance
    missing = toolset.install_components

    installer_dir = program_files_x86() / "Microsoft Visual Studio" / "Installer"
    installer = None
    for name in ("vs_installer.exe", "setup.exe"):
        candidate = installer_dir / name
        if candidate.exists():
            installer = candidate
            break
    if installer is None:
        fail("no Visual Studio installer found in {}".format(installer_dir))

    command = [str(installer), "modify",
               "--installPath", instance["installationPath"],
               "--quiet", "--norestart", "--nocache"]
    for component in missing:
        command += ["--add", component]
    log("installing missing components: {}".format(", ".join(missing)))
    code = run(command, check=False)
    if code not in INSTALLER_OK:
        log("warning: installer exited with {}; checking whether the "
            "components landed anyway".format(code))

    deadline = time.time() + timeout
    while True:
        instance = require_visual_studio(toolset)
        if find_toolset_directory(instance["installationPath"],
                                  toolset.vcvars_ver) is not None:
            log("components installed")
            return instance
        if time.time() >= deadline:
            break
        time.sleep(15)
    fail("timed out waiting for the Visual Studio components: {}"
         .format(", ".join(missing)))


def find_toolset_directory(install_path, vcvars_ver):
    """``VC/Tools/MSVC/<version>`` for this configuration, or None."""
    tools = Path(install_path) / "VC" / "Tools" / "MSVC"
    if not tools.is_dir():
        return None
    if vcvars_ver:
        matches = sorted(
            (entry for entry in tools.iterdir()
             if entry.is_dir() and entry.name.startswith(vcvars_ver + ".")),
            key=lambda entry: _version_key(entry.name))
        return matches[-1] if matches else None

    default_file = (Path(install_path) / "VC" / "Auxiliary" / "Build"
                    / "Microsoft.VCToolsVersion.default.txt")
    if default_file.exists():
        name = default_file.read_text(encoding="utf-8").strip()
        if (tools / name).is_dir():
            return tools / name
    directories = sorted((e for e in tools.iterdir() if e.is_dir()),
                         key=lambda entry: _version_key(entry.name))
    return directories[-1] if directories else None


def toolset_directory(install_path, vcvars_ver):
    """The ``VC/Tools/MSVC/<version>`` directory this configuration will use."""
    found = find_toolset_directory(install_path, vcvars_ver)
    if found is None:
        tools = Path(install_path) / "VC" / "Tools" / "MSVC"
        available = ", ".join(sorted(e.name for e in tools.iterdir())) \
            if tools.is_dir() else "none"
        fail("MSVC toolset {} is not installed under {} (have: {})"
             .format(vcvars_ver or "(default)", tools, available))
    return found


def compiler_path(toolset_dir, arch):
    host = HOST_DIR[host_arch()]
    target = TARGET_DIR[arch.vcvars_target]
    cl = toolset_dir / "bin" / ("Host" + host) / target / "cl.exe"
    if not cl.exists():
        fail("cl.exe not found at {}".format(cl))
    return cl


def vcvarsall(install_path):
    path = Path(install_path) / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat"
    if not path.exists():
        fail("vcvarsall.bat not found at {}".format(path))
    return path


def vcvars_argument(target_token, host=None):
    """Turn a target like ``x86`` into a vcvarsall.bat argument."""
    host = host or host_arch()
    target = VCVARS_TOKEN.get(target_token, target_token)
    if target == host:
        return target
    return "{}_{}".format(host, target)


# b2 asks the setup script for a cpu using its own vocabulary; map every
# spelling it can produce onto the right vcvarsall.bat argument.
_B2_CPU_TOKENS = {
    "x86": "x86",
    "amd64": "x64",
    "x64": "x64",
    "x86_amd64": "x64",
    "amd64_x86": "x86",
    "arm": "arm",
    "arm64": "arm64",
    "x86_arm64": "arm64",
    "amd64_arm64": "arm64",
    "x86_arm": "arm",
    "amd64_arm": "arm",
}


def write_setup_script(path, install_path, vcvars_ver, arch):
    """Write the batch file b2 (and we) use to enter the right MSVC shell.

    b2 calls it with the cpu it is building for, so the mapping below must
    cover every spelling msvc.jam can pass.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    version_flag = " -vcvars_ver={}".format(vcvars_ver) if vcvars_ver else ""
    default = vcvars_argument(arch.vcvars_target)

    lines = [
        "@echo off",
        "rem Generated by boostwin. Enters the MSVC environment for this build.",
        'set "BW_VCVARS="',
        'if "%~1"=="" set "BW_VCVARS={}"'.format(default),
    ]
    for token, target in _B2_CPU_TOKENS.items():
        lines.append('if /I "%~1"=="{}" set "BW_VCVARS={}"'.format(
            token, vcvars_argument(target)))
    lines += [
        'if not defined BW_VCVARS (',
        '    echo boostwin: unsupported vcvars target "%~1" 1>&2',
        '    exit /b 1',
        ')',
        'call "{}" %BW_VCVARS%{}'.format(vcvarsall(install_path), version_flag),
    ]
    path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    return path


def write_env_dump_script(path, setup_script, arch):
    """A no-argument companion script, so we can read the environment back."""
    path = Path(path)
    argument = vcvars_argument(arch.vcvars_target)
    lines = [
        "@echo off",
        "rem Generated by boostwin. Prints the MSVC environment.",
        'call "{}" {}'.format(setup_script, argument),
        "if errorlevel 1 exit /b 1",
        "echo ___BOOSTWIN_ENV___",
        "set",
    ]
    path.write_text("\r\n".join(lines) + "\r\n", encoding="ascii")
    return path


def read_environment(dump_script, base_env=None):
    """Run the dump script and return the resulting environment as a dict."""
    if not WINDOWS:
        fail("MSVC environments can only be read on Windows")
    completed = subprocess.run(
        ["cmd", "/d", "/c", "call", str(dump_script)],
        env=dict(base_env if base_env is not None else os.environ),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    text = completed.stdout.decode("utf-8", errors="replace")
    if completed.returncode != 0 or "___BOOSTWIN_ENV___" not in text:
        fail("could not enter the MSVC environment:\n" + text)
    _banner, _sep, dump = text.partition("___BOOSTWIN_ENV___")
    env = {}
    for line in dump.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip():
            env[key.strip()] = value
    if "PATH" not in env and "Path" in env:
        env["PATH"] = env["Path"]
    if "INCLUDE" not in env:
        fail("the MSVC environment has no INCLUDE; vcvarsall probably failed:\n"
             + text)
    return env


def jam_path(path):
    """A Windows path in the form Boost.Build's jam files expect."""
    return str(Path(path)).replace("\\", "\\\\")


def write_user_config(path, build_config, cl, setup_script, python_root,
                      python_version):
    """Write the ``user-config.jam`` for exactly this configuration.

    Only this configuration's toolset and Python are declared, so nothing can
    silently fall back to a different compiler or a mismatched interpreter.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Generated by boostwin for {}.".format(build_config.id),
        "import toolset : using ;",
        "",
        "using msvc : {} : \"{}\" : <setup>\"{}\" ;".format(
            build_config.toolset.name, jam_path(cl), jam_path(setup_script)),
        "",
    ]
    if python_root is not None:
        include = Path(python_root) / "include"
        libs = Path(python_root) / "libs"
        lines += [
            "using python",
            "    : {}   # version".format(python_version),
            "    :      # interpreter",
            "    : \"{}\"".format(jam_path(include)),
            "    : \"{}\"".format(jam_path(libs)),
            "    ;",
            "",
        ]
    path.write_text("\n".join(lines), encoding="ascii")
    return path


def cl_banner(cl, env):
    """First line of cl.exe's banner, e.g. the exact compiler version."""
    completed = subprocess.run([str(cl)], env=env, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    text = completed.stdout.decode("utf-8", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line
    return "unknown"


class Toolchain(object):
    """Everything a build or test step needs to talk to the compiler."""

    def __init__(self, build_config, env, info, user_config, setup_script, cl):
        self.build_config = build_config
        self.env = env
        self.info = info
        self.user_config = user_config
        self.setup_script = setup_script
        # An absolute path: on Windows a child process is looked up on the
        # *parent's* PATH, so passing the MSVC environment is not enough to
        # find the right cl.exe.
        self.cl = cl


def prepare(workspace, build_config, base_env=None, install_missing=True):
    """Locate (and if needed install) the toolset, then activate it."""
    if not WINDOWS:
        fail("building Boost requires Windows; "
             "'fetch', 'matrix' and 'package' work anywhere")

    config = workspace.config
    toolset = build_config.toolset
    arch = build_config.arch
    work = workspace.work(build_config)
    work.mkdir(parents=True, exist_ok=True)

    instance = require_visual_studio(toolset)
    if install_missing and toolset.install_components:
        instance = install_components(toolset, instance)

    install_path = instance["installationPath"]
    tools_dir = toolset_directory(install_path, toolset.vcvars_ver)
    cl = compiler_path(tools_dir, arch)
    log("MSVC toolset: {} ({})".format(tools_dir.name, cl))

    setup_script = write_setup_script(
        workspace.setup_script(build_config), install_path,
        toolset.vcvars_ver, arch)
    dump_script = write_env_dump_script(
        work / "dump-env.bat", setup_script, arch)
    env = read_environment(dump_script, base_env=base_env)

    python_root = workspace.python_root(arch)
    if not python_root.is_dir():
        log("warning: {} is missing, Boost.Python will not be configured"
            .format(python_root))
        python_root = None

    user_config = write_user_config(
        workspace.user_config(build_config), build_config, cl, setup_script,
        python_root, config.deps.python_dotted)

    info = {
        "config": build_config.id,
        "toolset": toolset.name,
        "arch": arch.key,
        "variant": build_config.variant,
        "link": build_config.link,
        "runtime_link": build_config.runtime_link,
        "runner": toolset.runner,
        "vs_display": toolset.vs_display,
        "vs_product": instance.get("displayName", ""),
        "vs_version": instance.get("installationVersion", ""),
        "vs_path": install_path,
        "msvc_toolset": tools_dir.name,
        "cl": str(cl),
        "cl_banner": cl_banner(cl, env),
        "windows_sdk": env.get("WindowsSDKVersion", "").strip("\\"),
        "python": config.deps.python,
        "zlib": config.deps.zlib,
        "bzip2": config.deps.bzip2,
    }
    target = workspace.toolchain_json(build_config)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(info, indent=2), encoding="utf-8")
    log("compiler: " + info["cl_banner"])
    return Toolchain(build_config, env, info, user_config, setup_script, cl)
