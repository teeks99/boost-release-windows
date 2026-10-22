"""Tests for the parts of boostwin that do not need a compiler.

    python -m unittest discover -s tests
"""
import contextlib
import io
import json
import os
import re
import shlex
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from boostwin import config as config_module
from boostwin import cli, inventory, msvc, package, paths, smoke, vsproj

from . import fixture

REPO = Path(__file__).resolve().parent.parent
BUILD_TOML = REPO / "build.toml"


def load(**overrides):
    return config_module.load(BUILD_TOML, overrides=overrides or None)


class ConfigTests(unittest.TestCase):
    def test_matrix_covers_every_dimension(self):
        config = load()
        matrix = config.matrix()
        expected = sum(len(t.archs) for t in config.toolsets) \
            * len(config.variants) * len(config.link_combos)
        self.assertEqual(len(matrix), expected)
        self.assertEqual(len({c.id for c in matrix}), len(matrix))

    def test_configuration_identity(self):
        config = load()
        found = [c for c in config.matrix()
                 if c.id == "msvc-14.3-64-release-static-shared"]
        self.assertEqual(len(found), 1)
        build_config = found[0]
        self.assertEqual(build_config.lib_dir, "lib64-msvc-14.3")
        self.assertEqual(build_config.runner, "windows-2022")
        self.assertIn("toolset=msvc-14.3", build_config.b2_properties)
        self.assertIn("address-model=64", build_config.b2_properties)
        self.assertIn("runtime-link=shared", build_config.b2_properties)

    def test_short_ids_name_exactly_one_configuration_each(self):
        # The work tree is named with short_id, so a spelling that collapsed
        # two configurations would have them building on top of each other.
        # b2's ABI tag alone would: it carries runtime-link and variant but
        # not link, which is why link_tag is in there too.
        config = load()
        matrix = config.matrix()
        self.assertEqual(len({c.short_id for c in matrix}), len(matrix))
        for build_config in matrix:
            self.assertLess(len(build_config.short_id), len(build_config.id))

    def test_short_id_spells_the_variant_the_way_b2_does(self):
        # So a work directory reads like the file names staged beside it.
        config = load()
        expected = {
            "msvc-14.5-arm64-release-static-static": "msvc-14.5-arm64-lib-s",
            "msvc-14.5-arm64-debug-static-static": "msvc-14.5-arm64-lib-sgd",
            "msvc-14.5-arm64-release-shared-shared": "msvc-14.5-arm64-dll",
            "msvc-14.3-64-debug-shared-shared": "msvc-14.3-64-dll-gd",
        }
        found = {c.id: c.short_id for c in config.matrix()
                 if c.id in expected}
        self.assertEqual(found, expected)

    def test_the_abi_tag_is_the_one_the_staged_files_carry(self):
        # inventory checks staged file names against the same tag, so what
        # the work directory is called and what the check enforces cannot
        # drift apart.
        config = load()
        for build_config in config.matrix():
            self.assertEqual(
                inventory.expected_option_tags(build_config),
                ["mt"] + ([build_config.abi_tag] if build_config.abi_tag
                          else []))
            if build_config.abi_tag:
                self.assertTrue(
                    build_config.short_id.endswith("-" + build_config.abi_tag),
                    build_config.short_id)
            # `lib` is b2's own prefix for a static library; an import
            # library has none, which a directory name cannot express.
            self.assertEqual(build_config.link_tag,
                             "lib" if build_config.link == "static" else "dll")

    def test_the_work_directory_drops_mt_but_the_staged_files_keep_it(self):
        # Every configuration is multithreaded, so `mt` distinguishes
        # nothing in a directory name -- but it is b2 that names the files,
        # so the inventory check still has to expect it there.
        config = load()
        for build_config in config.matrix():
            self.assertNotIn("-mt", build_config.short_id)
            self.assertEqual(build_config.option_tags[0], "mt")
            self.assertIn("threading=multi", build_config.b2_properties)

    def test_two_configurations_cannot_share_a_work_directory(self):
        # short_id leaves out what is constant across the matrix, so a
        # build.toml that made one of those vary has to be refused rather
        # than left to build two configurations on top of each other.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build.toml"
            path.write_text(BUILD_TOML.read_text(encoding="utf-8").replace(
                'variant = ["debug", "release"]',
                'variant = ["debug", "release", "profile"]'),
                encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                config_module.load(path)
        message = str(raised.exception)
        self.assertIn("work directory", message)
        self.assertIn("profile", message)

    def test_short_id_keeps_the_parts_that_identify_a_machine(self):
        # Toolset and architecture stay verbatim: they are what tells you
        # which compiler a work directory belongs to, and they are what
        # --toolset and --arch take.
        config = load()
        for build_config in config.matrix():
            self.assertTrue(build_config.short_id.startswith(
                "msvc-{}-{}-".format(build_config.toolset.name,
                                     build_config.arch.key)),
                build_config.short_id)

    def test_release_naming_matches_the_published_layout(self):
        snapshot = load(version="93", minor_version="0",
                        type="master-snapshot", repo="archives")
        self.assertEqual(snapshot.release.release_name,
                         "boost_1_93_0-snapshot")
        self.assertEqual(
            snapshot.release.source_url,
            "https://archives.boost.io/master/boost_1_93_0-snapshot.tar.bz2")

        candidate = load(version="92", minor_version="0", type="rc", rc="1")
        self.assertEqual(candidate.release.release_name, "boost_1_92_0")
        self.assertEqual(
            candidate.release.source_url,
            "https://archives.boost.io/release/1.92.0/source/"
            "boost_1_92_0_rc1.tar.bz2")

        beta = load(version="92", minor_version="0", type="beta-rc",
                    beta="1", rc="1")
        self.assertEqual(beta.release.release_name, "boost_1_92_0_b1")

    def test_the_full_archive_is_named_all_and_nothing_else(self):
        # It holds every compiler and every architecture, so it says so
        # once.  Listing them would date the name the next time one is
        # added -- which is exactly what arm64 did.
        config = load()
        self.assertEqual(config.full_archive_name,
                         "boost_1_93_0-snapshot-bin-msvc-all.7z")
        for arch_key in config.arch_keys:
            self.assertNotIn(arch_key, config.full_archive_name)

    def test_arm64_is_built_by_one_toolset_on_its_own_runner(self):
        # The Arm64 runner images only carry the newest Visual Studio, and
        # arm64 is built natively rather than cross compiled, so exactly one
        # toolset builds it and that one architecture names its own machine.
        config = load()
        arm = [c for c in config.matrix() if c.arch.key == "arm64"]
        self.assertEqual({c.toolset.name for c in arm}, {"14.5"})
        self.assertEqual(len(arm),
                         len(config.variants) * len(config.link_combos))

        toolset = config.toolset("14.5")
        self.assertEqual(toolset.runner_for("arm64"), "windows-11-vs2026-arm")
        # ...and the same compiler's other architectures are untouched.
        for arch_key in ("32", "64"):
            self.assertEqual(toolset.runner_for(arch_key), toolset.runner)
        self.assertEqual(config.toolset("14.3").runner_for("arm64"),
                         "windows-2022")

    def test_an_arch_runner_for_an_unbuilt_architecture_is_rejected(self):
        # Silently ignoring it would leave the build running somewhere the
        # author did not intend.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build.toml"
            path.write_text(BUILD_TOML.read_text(encoding="utf-8").replace(
                'arch_runners = { arm64 = "windows-11-vs2026-arm" }',
                'arch_runners = { arm46 = "windows-11-vs2026-arm" }'),
                encoding="utf-8")
            with self.assertRaises(SystemExit) as raised:
                config_module.load(path)
        self.assertIn("arm46", str(raised.exception))
        self.assertIn("14.5", str(raised.exception))

    def test_environment_overrides_are_applied(self):
        os.environ["BOOSTWIN_RELEASE_VERSION"] = "91"
        os.environ["BOOSTWIN_RELEASE_TYPE"] = "release"
        try:
            self.assertEqual(load().release.release_name, "boost_1_91_0")
            # An explicit flag still wins over the environment.
            self.assertEqual(load(version="90").release.release_name,
                             "boost_1_90_0")
        finally:
            del os.environ["BOOSTWIN_RELEASE_VERSION"]
            del os.environ["BOOSTWIN_RELEASE_TYPE"]

    def test_python_archive_and_directory_names_differ(self):
        deps = load().deps
        self.assertEqual(deps.python_archive("64"), "Python3.14.0-64.tar.xz")
        self.assertEqual(deps.python_dir("64"), "Python314-64")
        self.assertEqual(deps.python_tag, "314")


class LibraryNameTests(unittest.TestCase):
    def test_parses_a_versioned_layout_name(self):
        parsed = inventory.parse_library_name(
            "libboost_filesystem-vc143-mt-sgd-x64-1_92.lib")
        self.assertEqual(parsed["library"], "filesystem")
        self.assertEqual(parsed["toolset_tag"], "vc143")
        self.assertEqual(parsed["option_tags"], ["mt", "sgd"])
        self.assertEqual(parsed["arch_tag"], "x64")

    def test_rejects_names_that_are_not_boost_libraries(self):
        self.assertIsNone(
            inventory.parse_library_name("DEPENDENCY_VERSIONS.txt"))
        self.assertIsNone(inventory.parse_library_name("zlib.lib"))

    def test_expected_tags_cover_every_variant(self):
        config = load()
        wanted = {
            ("release", "shared"): [],
            ("debug", "shared"): ["gd"],
            ("release", "static"): ["s"],
            ("debug", "static"): ["sgd"],
        }
        seen = set()
        for build_config in config.matrix():
            key = (build_config.variant, build_config.runtime_link)
            self.assertEqual(inventory.expected_option_tags(build_config),
                             ["mt"] + wanted[key])
            seen.add(key)
        self.assertEqual(seen, set(wanted))

    def test_expected_arch_tag_handles_arm(self):
        config = load()
        sixty_four = [c for c in config.matrix()
                      if c.arch.key == "64" and c.toolset.name == "14.3"][0]
        self.assertEqual(inventory.expected_arch_tag(sixty_four), "x64")

        arm_config = type(sixty_four)(
            toolset=sixty_four.toolset, arch=config.archs["arm64"],
            variant="release", link="static", runtime_link="shared")
        self.assertEqual(inventory.expected_arch_tag(arm_config), "a64")
        self.assertEqual(arm_config.lib_dir, "libarm64-msvc-14.3")
        self.assertIn("architecture=arm", arm_config.b2_properties)
        self.assertIn("address-model=64", arm_config.b2_properties)

    def test_conditional_libraries_apply_only_where_configured(self):
        config = load()
        shared_runtime = [c for c in config.matrix()
                          if c.runtime_link == "shared"][0]
        static_runtime = [c for c in config.matrix()
                          if c.runtime_link == "static"][0]
        self.assertIn("python314",
                      inventory.required_libraries(config, shared_runtime))
        self.assertNotIn("python314",
                         inventory.required_libraries(config, static_runtime))

    def test_coroutine_and_fiber_are_not_required_on_arm64(self):
        # Neither builds for Windows on ARM64: Boost.Context is forced to its
        # winfib implementation there and the two libraries are built against
        # the fcontext interface anyway, so they fail to link.  Requiring them
        # would fail every arm64 configuration for something upstream does.
        config = load()
        for build_config in config.matrix():
            required = inventory.required_libraries(config, build_config)
            for name in ("coroutine", "fiber"):
                self.assertEqual(build_config.arch.key != "arm64",
                                 name in required,
                                 "{} in {}".format(name, build_config.id))
            # ...and nothing else was lost along the way.
            self.assertIn("context", required)
            self.assertIn("thread", required)

    def test_an_arch_filter_is_the_only_new_conditional_dimension(self):
        # The filters are matched by name against a BuildConfig, and arch is
        # the one that is not a plain string on it.
        config = load()
        entry = config_module.ConditionalLibs(libs=("example",),
                                              arch=("arm64",))
        arm = [c for c in config.matrix() if c.arch.key == "arm64"][0]
        other = [c for c in config.matrix() if c.arch.key == "64"][0]
        self.assertTrue(entry.applies_to(arm))
        self.assertFalse(entry.applies_to(other))
        # An entry with no filters at all still applies everywhere.
        everywhere = config_module.ConditionalLibs(libs=("example",))
        self.assertTrue(everywhere.applies_to(arm))
        self.assertTrue(everywhere.applies_to(other))


class VsProjTests(unittest.TestCase):
    """The checked-in smoke/vs/msvc-<toolset> projects and their .props."""

    def test_every_toolset_has_a_checked_in_project(self):
        # Otherwise `test` would silently skip the msbuild checks for a
        # toolset build.toml still lists.
        config = load()
        for toolset in config.toolsets:
            self.assertIn(toolset.name, vsproj.PLATFORM_TOOLSET,
                          "msvc-{} has no PlatformToolset mapping".format(
                              toolset.name))
            for path in (vsproj.solution_path(config, toolset),
                        vsproj.vcxproj_path(config, toolset),
                        vsproj.python_vcxproj_path(config, toolset)):
                self.assertTrue(path.is_file(), path)

    def test_configuration_name_covers_every_link_combination(self):
        config = load()
        expected = {
            ("debug", "shared", "shared"): "Debug",
            ("debug", "static", "shared"): "Debug-Static",
            ("debug", "static", "static"): "Debug-StaticRuntime",
            ("release", "shared", "shared"): "Release",
            ("release", "static", "shared"): "Release-Static",
            ("release", "static", "static"): "Release-StaticRuntime",
        }
        seen = set()
        for build_config in config.matrix():
            key = (build_config.variant, build_config.link,
                  build_config.runtime_link)
            self.assertEqual(vsproj.configuration_name(build_config),
                             expected[key])
            seen.add(key)
        self.assertEqual(seen, set(expected))

    def test_platform_name_matches_architecture(self):
        config = load()
        expected = {"32": "Win32", "64": "x64", "arm64": "ARM64"}
        seen = set()
        for build_config in config.matrix():
            self.assertEqual(vsproj.platform_name(build_config),
                             expected[build_config.arch.key])
            seen.add(build_config.arch.key)
        self.assertEqual(seen, set(expected))

    def test_every_configuration_has_a_project_configuration_to_build(self):
        # boostwin drives msbuild with /p:Configuration and /p:Platform, and
        # an msbuild that is handed a pair the project does not declare
        # builds something else rather than failing -- so every entry in the
        # matrix has to be in the checked-in vcxproj.  This is what catches
        # an architecture added to build.toml but not to the projects.
        config = load()
        for build_config in config.matrix():
            toolset = build_config.toolset
            wanted = '"{}|{}"'.format(vsproj.configuration_name(build_config),
                                      vsproj.platform_name(build_config))
            for path in (vsproj.vcxproj_path(config, toolset),
                         vsproj.python_vcxproj_path(config, toolset)):
                # assertTrue rather than assertIn: the latter would put the
                # whole project file in the failure message.
                self.assertTrue(
                    wanted in path.read_text(encoding="utf-8"),
                    "smoke/vs/msvc-{}/{} declares no {} configuration, "
                    "which {} needs".format(toolset.name, path.name, wanted,
                                            build_config.id))

    def test_props_path_is_named_after_configuration_and_platform(self):
        config = load()
        build_config = [c for c in config.matrix()
                       if c.id == "msvc-14.3-64-debug-static-static"][0]
        path = vsproj.props_path(config, build_config)
        self.assertEqual(path.name, "Debug-StaticRuntime-x64.props")
        self.assertEqual(path.parent.name, "generated")

    def test_write_props_fills_in_the_staged_paths(self):
        # write_props necessarily writes next to the checked-in vcxproj
        # (smoke/vs/msvc-.../generated/), not under the sandboxed workspace
        # root, since that is the only path a static vcxproj can Import --
        # so this cleans up after itself instead of leaving it behind.
        config = load()
        build_config = [c for c in config.matrix()
                       if c.id == "msvc-14.3-64-release-shared-shared"][0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture.write(root, config, [build_config])
            workspace = paths.Workspace(root, config)
            path = vsproj.write_props(workspace, build_config)
            self.addCleanup(path.unlink, missing_ok=True)
            text = path.read_text(encoding="utf-8")
        self.assertIn("<BoostIncludeDir>", text)
        self.assertIn(str(workspace.source).replace("/", "\\"), text)
        self.assertIn(str(workspace.stage_lib(build_config)).replace(
            "/", "\\"), text)
        # boost_charconv has to be linked explicitly; fixture.write stages
        # it under its required-library name, so it should be resolved here
        # too, the same way check_compile_and_run resolves it.
        self.assertIn("<BoostExtraLibs>", text)
        self.assertIn("charconv", text)

    def test_v141_projects_do_not_rely_on_the_bare_10_0_sdk_alias(self):
        # WindowsTargetPlatformVersion 10.0 stands for "whichever Windows 10
        # SDK is installed" only from MSBuild's v160 (Visual Studio 2019)
        # targets onwards.  A v141 build goes through the v150 targets, which
        # take it literally, look for an SDK directory named 10.0 and stop
        # with MSB8036 -- so the v141 projects have to resolve a concrete
        # version themselves.  Newer toolsets are free to keep the alias.
        config = load()
        toolsets = [t for t in config.toolsets
                    if vsproj.platform_toolset(t) == "v141"]
        self.assertTrue(toolsets, "build.toml no longer lists a v141 toolset")
        for toolset in toolsets:
            for path in (vsproj.vcxproj_path(config, toolset),
                         vsproj.python_vcxproj_path(config, toolset)):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("<WindowsTargetPlatformVersion>10.0<", text,
                                 path)
                self.assertIn("GetLatestSDKTargetPlatformVersion", text,
                              path)

    def test_the_arm64_smoke_build_matches_the_context_implementation(self):
        # b2 builds Boost.Context with <context-impl>winfib on Windows ARM64,
        # which carries -DBOOST_USE_WINFIB.  boost/context/fiber.hpp does no
        # auto-detection, so without the same macro the smoke program gets
        # the fcontext implementation and fails to link on make_fcontext --
        # which is exactly what a consumer of these binaries would hit.
        config = load()
        arm = [c for c in config.matrix() if c.arch.key == "arm64"]
        self.assertTrue(arm, "build.toml no longer builds arm64")
        for build_config in arm:
            text = vsproj.vcxproj_path(
                config, build_config.toolset).read_text(encoding="utf-8")
            self.assertIn("BOOST_USE_WINFIB", text)
            self.assertIn("'$(Platform)'=='ARM64'", text)

    def test_msbuild_is_told_which_windows_sdk_to_use(self):
        # Same reason: whatever the project asks for, the version vcvarsall
        # resolved wins, so the smoke build uses the SDK the libraries under
        # test were built against.
        config = load()
        build_config = [c for c in config.matrix()
                        if c.toolset.name == "14.1"][0]
        toolchain = mock.Mock(env={}, info={"vs_path": "vs",
                                            "windows_sdk": "10.0.22621.0"})
        completed = mock.Mock(returncode=0, stdout=b"")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = paths.Workspace(Path(tmp), config)
            with mock.patch.object(smoke.vsproj, "find_msbuild",
                                   return_value=Path("MSBuild.exe")), \
                    mock.patch.object(smoke.subprocess, "run",
                                      return_value=completed) as run, \
                    contextlib.redirect_stdout(io.StringIO()):
                smoke._msbuild(
                    workspace, build_config, toolchain,
                    vsproj.vcxproj_path(config, build_config.toolset),
                    "compile.log")
        command = run.call_args[0][0]
        self.assertIn("/p:WindowsTargetPlatformVersion=10.0.22621.0", command)


class B2CommandTests(unittest.TestCase):
    """How boostwin drives b2 for one configuration."""

    def commands(self):
        config = load()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = paths.Workspace(Path(tmp), config)
            for build_config in config.matrix():
                yield build_config, cli.build_module.b2_command(
                    workspace, build_config)

    def test_the_work_tree_is_short_and_the_stage_tree_is_not(self):
        # Two different jobs: the work tree holds b2's own paths, which run
        # close to MAX_PATH, and nothing reads it back by name.  The stage
        # tree is what CI artifacts and `package` match on, so it keeps the
        # full configuration id.
        config = load()
        with tempfile.TemporaryDirectory() as tmp:
            workspace = paths.Workspace(Path(tmp), config)
            for build_config in config.matrix():
                self.assertEqual(workspace.work(build_config).name,
                                 build_config.short_id)
                self.assertEqual(workspace.stage(build_config).name,
                                 build_config.id)

    def test_target_paths_are_hashed(self):
        # b2 names every intermediate directory after the full property set,
        # which is 116 characters before the file name.  architecture-arm is
        # 32 of those and only appears for arm64, which took an arm64
        # static-runtime build's longest response file past Windows' 260
        # character MAX_PATH: lib.exe failed with LNK1104, b2 carried on, and
        # boost_serialization, boost_wserialization, boost_type_erasure and
        # boost_python314 were simply absent from the staged output.
        for build_config, command in self.commands():
            self.assertIn("--hash", command, build_config.id)

    def test_hashing_does_not_move_the_staged_output(self):
        # --hash only renames what is under --build-dir.  Where the
        # libraries land is named explicitly, and packaging depends on it.
        for build_config, command in self.commands():
            staged = [a for a in command if a.startswith("--stage-libdir=")]
            self.assertEqual(len(staged), 1, build_config.id)
            self.assertTrue(staged[0].endswith("/" + build_config.lib_dir),
                            staged[0])


def run_cli(*argv):
    """Run a boostwin command against this repo's build.toml, capturing output."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = cli.main(["--config-file", str(BUILD_TOML)] + list(argv))
    return code, buffer.getvalue()


class InfoAndMatrixTests(unittest.TestCase):
    """What the two read-only commands put in front of someone."""

    def test_info_describes_every_matrix_dimension(self):
        code, output = run_cli("info")
        self.assertEqual(code, 0)
        for expected in ("toolsets", "msvc-14.1", "architectures",
                         "32, 64, arm64", "variants", "debug, release",
                         "link", "shared/shared", "static/static",
                         "threading", "configurations", "54"):
            self.assertIn(expected, output)

    def test_info_does_not_mention_github_runners(self):
        # Runner labels mean nothing to someone building on their own machine.
        _code, output = run_cli("info")
        self.assertNotIn("windows-", output)

    def test_matrix_lists_configuration_ids_only(self):
        code, output = run_cli("matrix", "--toolset", "14.3", "--arch", "64")
        self.assertEqual(code, 0)
        self.assertIn("msvc-14.3-64-release-static-shared", output)
        self.assertNotIn("windows-", output)
        self.assertIn("6 configurations", output)

    def test_matrix_shows_runners_when_asked(self):
        code, output = run_cli("matrix", "--toolset", "14.3", "--runners")
        self.assertEqual(code, 0)
        self.assertIn("windows-2022", output)
        self.assertIn("runner image", output)

    def test_matrix_json_always_carries_the_runner(self):
        # CI needs it even though the human listing does not show it.
        code, output = run_cli("matrix", "--json", "--id",
                               "msvc-14.5-64-release-static-shared")
        self.assertEqual(code, 0)
        entries = json.loads(output)
        self.assertEqual(entries[0]["runner"], "windows-2025")

    def test_matrix_json_carries_the_per_architecture_runner(self):
        # runs-on comes straight out of this, so the arm64 jobs landing on
        # the Arm64 image is the whole mechanism.
        code, output = run_cli("matrix", "--json", "--id",
                               "msvc-14.5-arm64-release-static-shared")
        self.assertEqual(code, 0)
        entries = json.loads(output)
        self.assertEqual(entries[0]["runner"], "windows-11-vs2026-arm")
        self.assertEqual(entries[0]["arch"], "arm64")
        self.assertEqual(entries[0]["lib_dir"], "libarm64-msvc-14.5")


class DocumentedCommandTests(unittest.TestCase):
    """Every command line the docs show has to be one the tool accepts."""

    COMMAND = re.compile(r"python3? -m boostwin ([^\n#`]*)")
    SOURCES = ("README.md", "CONTRIBUTING.md", "build.toml",
               ".github/workflows/build.yaml")

    def documented_commands(self):
        for name in self.SOURCES:
            text = (REPO / name).read_text(encoding="utf-8")
            for line in text.splitlines():
                for arguments in self.COMMAND.findall(line):
                    arguments = arguments.strip()
                    # Skip anything the shell would have expanded first.
                    if not arguments or "$" in arguments or "{" in arguments:
                        continue
                    yield name, arguments

    def test_the_docs_show_at_least_a_few_commands(self):
        self.assertGreaterEqual(len(list(self.documented_commands())), 8)

    def test_every_documented_command_parses(self):
        parser = cli.build_parser()
        for name, arguments in self.documented_commands():
            with self.subTest(source=name, command=arguments):
                try:
                    parser.parse_args(shlex.split(arguments))
                except SystemExit:
                    self.fail("{} documents a command boostwin rejects: "
                              "python -m boostwin {}".format(name, arguments))


class VersionRangeTests(unittest.TestCase):
    """The Visual Studio version ranges in build.toml must select correctly.

    Matched here rather than by vswhere's own -version option, so it is worth
    pinning down: getting it wrong means a runner with the right compiler
    installed reports that it has none.
    """

    def test_visual_studio_2022_range(self):
        for version in ("17.0", "17.14.37628.2", "17.99.9"):
            self.assertTrue(msvc.version_in_range(version, "[17.0,18.0)"),
                            version)
        for version in ("16.11.53", "18.0", "18.9.12120.119"):
            self.assertFalse(msvc.version_in_range(version, "[17.0,18.0)"),
                             version)

    def test_visual_studio_2026_range(self):
        for version in ("18.0", "18.9.12120.119", "18.10.12201.205"):
            self.assertTrue(msvc.version_in_range(version, "[18.0,19.0)"),
                            version)
        for version in ("17.14.37628.2", "19.0"):
            self.assertFalse(msvc.version_in_range(version, "[18.0,19.0)"),
                             version)

    def test_bracket_kinds_and_open_ends(self):
        self.assertFalse(msvc.version_in_range("17.0", "(17.0,18.0)"))
        self.assertTrue(msvc.version_in_range("17.1", "(17.0,18.0)"))
        self.assertTrue(msvc.version_in_range("18.0", "[17.0,18.0]"))
        self.assertTrue(msvc.version_in_range("25.0", "[17.0,)"))
        self.assertFalse(msvc.version_in_range("16.0", "[17.0,)"))

    def test_a_bare_version_means_that_one_or_newer(self):
        self.assertTrue(msvc.version_in_range("17.5", "17.0"))
        self.assertFalse(msvc.version_in_range("16.5", "17.0"))

    def test_a_missing_version_never_matches(self):
        self.assertFalse(msvc.version_in_range("", "[17.0,18.0)"))

    # What each GitHub runner label actually carries.  windows-latest and
    # windows-2025 were moved onto the Visual Studio 2026 image in June 2026,
    # which is why anything needing Visual Studio 2022 says windows-2022.
    RUNNER_VISUAL_STUDIO = {
        "windows-2022": "17.14.37628.2",
        "windows-2025": "18.9.12120.119",
        "windows-latest": "18.9.12120.119",
        "windows-2025-vs2026": "18.9.12120.119",
        "windows-11-arm": "17.14.37628.2",
        "windows-11-vs2026-arm": "18.10.12201.205",
    }

    def test_each_toolset_asks_for_the_visual_studio_its_runner_has(self):
        # Per configuration rather than per toolset: an arch_runners entry
        # sends one architecture to a different image, which has to carry a
        # Visual Studio that satisfies the same range.
        config = load()
        for build_config in config.matrix():
            runner = build_config.runner
            self.assertIn(
                runner, self.RUNNER_VISUAL_STUDIO,
                "{} uses an unknown runner label".format(build_config.id))
            version = self.RUNNER_VISUAL_STUDIO[runner]
            self.assertTrue(
                msvc.version_in_range(
                    version, build_config.toolset.vs_version_range),
                "{} runs on {}, which has Visual Studio {}, but asks for "
                "{}".format(build_config.id, runner, version,
                            build_config.toolset.vs_version_range))

    def test_arm64_builds_on_an_arm_runner(self):
        # Native, not cross compiled: b2, the smoke program and the
        # Boost.Python extension all run on the machine that built them.
        config = load()
        for build_config in config.matrix():
            self.assertEqual(build_config.arch.key == "arm64",
                             build_config.runner.endswith("-arm"),
                             build_config.id)

    def test_every_configured_toolset_range_is_understood(self):
        config = load()
        known = {
            "[17.0,18.0)": "17.14.37628.2",   # windows-2025 today
            "[18.0,19.0)": "18.9.12120.119",  # windows-2025-vs2026 today
        }
        for toolset in config.toolsets:
            self.assertIn(toolset.vs_version_range, known,
                          "unrecognised range for msvc-" + toolset.name)
            self.assertTrue(msvc.version_in_range(
                known[toolset.vs_version_range], toolset.vs_version_range))


def make_visual_studio(root, toolsets, default=None):
    """A directory tree shaped like a Visual Studio installation."""
    root = Path(root)
    for name in toolsets:
        (root / "VC" / "Tools" / "MSVC" / name).mkdir(parents=True)
    build = root / "VC" / "Auxiliary" / "Build"
    build.mkdir(parents=True, exist_ok=True)
    (build / "Microsoft.VCToolsVersion.default.txt").write_text(
        default or toolsets[-1])
    return root


class ToolsetSelectionTests(unittest.TestCase):
    """Picking the MSVC toolset out of whichever Visual Studio is installed.

    GitHub moved the windows-2025 label onto the Visual Studio 2026 image in
    June 2026; these pin down that such a move is refused loudly rather than
    producing v145 binaries labelled vc143.
    """

    # What each image actually carries.
    VS2022 = ["14.29.30133", "14.44.35207"]
    VS2026 = ["14.29.30133", "14.44.35207", "14.50.35000"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = load()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def toolset(self, name):
        return self.config.toolset(name)

    def test_family_matching(self):
        for name, prefix, expected in (
            ("14.16.27023", "14.16", True),
            ("14.16.27023", "14.1", True),
            ("14.29.30133", "14.2", True),
            ("14.29.30133", "14.1", False),
            ("14.44.35207", "14.4", True),
            ("14.44.35207", "14.3", False),
            ("14.34.31933", "14.3", True),
            ("14.50.35000", "14.5", True),
            ("14.50.35000", "14.4", False),
        ):
            self.assertEqual(msvc.matches_msvc_version(name, prefix), expected,
                             "{} vs {}".format(name, prefix))

    def test_visual_studio_2022_satisfies_141_142_and_143(self):
        vs = make_visual_studio(self.root / "vs2022", self.VS2022)
        self.assertEqual(
            msvc.require_toolset_directory(vs, self.toolset("14.3")).name,
            "14.44.35207")
        self.assertEqual(
            msvc.require_toolset_directory(vs, self.toolset("14.2")).name,
            "14.29.30133")
        # v141 has to be installed first, so nothing is selected yet.
        self.assertIsNone(
            msvc.select_toolset_directory(vs, self.toolset("14.1")))

    def test_visual_studio_2026_satisfies_145(self):
        vs = make_visual_studio(self.root / "vs2026", self.VS2026)
        self.assertEqual(
            msvc.require_toolset_directory(vs, self.toolset("14.5")).name,
            "14.50.35000")

    def test_143_refuses_a_visual_studio_2026_default(self):
        # Exactly what happened when windows-2025 became the VS 2026 image:
        # the default toolset is v145, and calling it vc143 would be wrong.
        vs = make_visual_studio(self.root / "vs2026", self.VS2026)
        with self.assertRaises(SystemExit) as raised:
            msvc.require_toolset_directory(vs, self.toolset("14.3"))
        message = str(raised.exception)
        self.assertIn("14.50.35000", message)
        self.assertIn("runner image", message)

    def test_145_refuses_a_visual_studio_2022(self):
        vs = make_visual_studio(self.root / "vs2022", self.VS2022)
        with self.assertRaises(SystemExit) as raised:
            msvc.require_toolset_directory(vs, self.toolset("14.5"))
        self.assertIn("14.44.35207", str(raised.exception))

    def test_a_pinned_toolset_works_in_either_visual_studio(self):
        # 14.2 is a side-by-side toolset on both images, so it does not care
        # which Visual Studio hosts it.
        for name, toolsets in (("vs2022", self.VS2022), ("vs2026", self.VS2026)):
            vs = make_visual_studio(self.root / name, toolsets)
            self.assertEqual(
                msvc.require_toolset_directory(vs, self.toolset("14.2")).name,
                "14.29.30133")

    def test_the_default_marker_is_preferred_over_the_newest(self):
        vs = make_visual_studio(self.root / "vs2022", self.VS2022,
                                default="14.29.30133")
        self.assertEqual(msvc.default_toolset(vs).name, "14.29.30133")

    def test_a_visual_studio_with_no_toolsets_is_reported(self):
        vs = self.root / "empty"
        (vs / "VC").mkdir(parents=True)
        with self.assertRaises(SystemExit) as raised:
            msvc.require_toolset_directory(vs, self.toolset("14.3"))
        self.assertIn("none", str(raised.exception))


class InventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = load()
        self.build_config = [c for c in self.config.matrix()
                             if c.id == "msvc-14.3-64-debug-static-static"][0]
        self.root = Path(self.tmp.name)
        fixture.write(self.root, self.config, [self.build_config])
        self.workspace = paths.Workspace(self.root, self.config)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_complete_staging_directory_passes(self):
        result = inventory.check_inventory(self.workspace, self.build_config)
        self.assertTrue(result["ok"], result.get("problems"))
        self.assertIn("filesystem", result["libraries"])

    def test_a_missing_required_library_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        for path in lib_dir.glob("*boost_thread-*"):
            path.unlink()
        result = inventory.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])
        self.assertTrue(any("thread" in problem
                            for problem in result["problems"]))

    def test_a_file_from_the_wrong_variant_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        # A release file that has strayed into the debug staging directory.
        (lib_dir / "libboost_thread-vc143-mt-s-x64-1_93.lib").write_text("x")
        result = inventory.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])
        self.assertTrue(any("option tags" in problem
                            for problem in result["problems"]))

    def test_a_file_from_the_wrong_toolset_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        (lib_dir / "libboost_thread-vc142-mt-sgd-x64-1_93.lib").write_text("x")
        result = inventory.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])
        self.assertTrue(any("toolset tag" in problem
                            for problem in result["problems"]))

    def test_an_empty_staging_directory_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        for path in lib_dir.iterdir():
            path.unlink()
        result = inventory.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])


class AutolinkTests(unittest.TestCase):
    """What Boost's auto-linking asks the linker for, against what was staged.

    Not every request is ours: Boost.Atomic pulls in the Windows SDK's
    synchronization.lib, and BOOST_LIB_DIAGNOSTIC reports it with its quotes
    still attached.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib_dir = Path(self.tmp.name)
        for name in ("boost_filesystem-vc143-mt-x64-1_93.lib",
                     "boost_thread-vc143-mt-x64-1_93.lib",
                     "libboost_exception-vc143-mt-x64-1_93.lib"):
            (self.lib_dir / name).write_text("x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_staged_boost_libraries_pass(self):
        boost, system, problems = smoke.classify_autolink(
            self.lib_dir, ["boost_filesystem-vc143-mt-x64-1_93.lib",
                           "boost_thread-vc143-mt-x64-1_93.lib",
                           "libboost_exception-vc143-mt-x64-1_93.lib"])
        self.assertEqual(problems, [])
        self.assertEqual(len(boost), 3)
        self.assertEqual(system, [])

    def test_a_windows_sdk_library_is_not_required_to_be_staged(self):
        boost, system, problems = smoke.classify_autolink(
            self.lib_dir, ['"synchronization".lib',
                           "boost_thread-vc143-mt-x64-1_93.lib"])
        self.assertEqual(problems, [])
        self.assertEqual(system, ["synchronization.lib"])
        self.assertEqual(boost, ["boost_thread-vc143-mt-x64-1_93.lib"])

    def test_a_boost_library_that_was_not_staged_fails(self):
        _boost, _system, problems = smoke.classify_autolink(
            self.lib_dir, ["boost_thread-vc143-mt-x64-1_93.lib",
                           "boost_regex-vc143-mt-x64-1_93.lib"])
        self.assertEqual(len(problems), 1)
        self.assertIn("boost_regex", problems[0])

    def test_a_boost_library_from_the_wrong_variant_fails(self):
        # A debug name against a release staging directory: the file is not
        # there, which is the whole point of checking.
        _boost, _system, problems = smoke.classify_autolink(
            self.lib_dir, ["boost_thread-vc143-mt-gd-x64-1_93.lib"])
        self.assertEqual(len(problems), 1)
        self.assertIn("-mt-gd-", problems[0])

    def test_linking_nothing_from_boost_fails(self):
        _boost, _system, problems = smoke.classify_autolink(
            self.lib_dir, ['"synchronization".lib'])
        self.assertTrue(any("no Boost libraries" in p for p in problems))

        _boost, _system, problems = smoke.classify_autolink(self.lib_dir, [])
        self.assertTrue(any("no Boost libraries" in p for p in problems))

    def test_the_diagnostic_line_is_recognised(self):
        # Exactly the shape cl.exe emits, including the quoted system library.
        output = (
            "smoke.cpp\n"
            "Linking to lib file: boost_atomic-vc143-mt-x64-1_93.lib\n"
            'Linking to lib file: "synchronization".lib\n'
            "Linking to lib file: boost_thread-vc143-mt-x64-1_93.lib\n")
        found = smoke.AUTOLINK_LINE.findall(output)
        self.assertEqual(found, ["boost_atomic-vc143-mt-x64-1_93.lib",
                                 '"synchronization".lib',
                                 "boost_thread-vc143-mt-x64-1_93.lib"])


class ExtraLinkTests(unittest.TestCase):
    """Libraries the smoke program must name because Boost does not."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lib_dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_static_library_is_found_by_stem(self):
        name = "libboost_charconv-vc143-mt-x64-1_93.lib"
        (self.lib_dir / name).write_text("x")
        resolved, problems = smoke.extra_link_libraries(
            self.lib_dir, ["charconv"])
        self.assertEqual(problems, [])
        self.assertEqual([p.name for p in resolved], [name])

    def test_an_import_library_is_found_by_stem(self):
        name = "boost_charconv-vc143-mt-gd-x64-1_93.lib"
        (self.lib_dir / name).write_text("x")
        (self.lib_dir / "boost_charconv-vc143-mt-gd-x64-1_93.dll").write_text("x")
        resolved, problems = smoke.extra_link_libraries(
            self.lib_dir, ["charconv"])
        self.assertEqual(problems, [])
        self.assertEqual([p.name for p in resolved], [name])

    def test_a_missing_library_is_reported(self):
        resolved, problems = smoke.extra_link_libraries(
            self.lib_dir, ["charconv"])
        self.assertEqual(resolved, [])
        self.assertEqual(len(problems), 1)
        self.assertIn("boost_charconv", problems[0])

    def test_a_library_the_linker_could_not_open_is_named(self):
        # Two checks fail for one cause when a library does not build:
        # inventory says it is missing, and the smoke link cannot open it.
        # The compile check has to say which, or the result matrix points at
        # the smoke project instead of at the build.
        output = (
            "  Linking to lib file: libboost_serialization-vc145-mt-s-a64-1_93.lib\n"
            "LINK : fatal error LNK1104: cannot open file "
            "'libboost_serialization-vc145-mt-s-a64-1_93.lib' [BoostSmoke.vcxproj]\n")
        self.assertEqual(
            smoke.missing_link_libraries(output),
            ["libboost_serialization-vc145-mt-s-a64-1_93.lib"])

    def test_a_missing_system_library_is_not_blamed_on_the_build(self):
        # Only Boost names mean "this configuration did not stage it".
        output = ("LINK : fatal error LNK1104: cannot open file "
                  "'python314.lib'\n")
        self.assertEqual(smoke.missing_link_libraries(output), [])

    def test_nothing_configured_asks_for_nothing(self):
        self.assertEqual(smoke.extra_link_libraries(self.lib_dir, []),
                         ([], []))

    def test_every_extra_library_is_also_a_required_one(self):
        # Otherwise a build could stage nothing for it and the inventory
        # check would stay quiet while the link failed.
        config = load()
        for stem in config.smoke.extra_link_libs:
            self.assertIn(stem, config.smoke.required_libs)


class DependencyOrderingTests(unittest.TestCase):
    """Dependencies must be unpacked before the toolchain is resolved.

    Resolving the toolchain writes user-config.jam, and that step decides
    whether to configure Boost.Python by checking whether the Python
    dependency has already been unpacked.  On a fresh build root, doing
    this the other way round silently produces a user-config.jam with no
    Boost.Python at all -- only a log warning, no error.
    """

    ID = "msvc-14.3-64-release-static-shared"

    def _args(self, command, *extra):
        parser = cli.build_parser()
        return parser.parse_args(
            ["--config-file", str(BUILD_TOML), command, "--id", self.ID]
            + list(extra))

    def _workspace(self, args, build_root):
        args.build_root = build_root
        return cli.make_workspace(args)

    def _patch_common(self, order):
        fake_toolchain = mock.Mock(env={})

        def record_extract(*_args, **_kwargs):
            order.append("extract_dependencies")

        def record_toolchain(*_args, **_kwargs):
            order.append("toolchain_for")
            return fake_toolchain

        patches = [
            mock.patch.object(cli.source, "fetch"),
            mock.patch.object(cli.source, "extract_dependencies",
                              side_effect=record_extract),
            mock.patch.object(cli.source, "prepare"),
            mock.patch.object(cli, "toolchain_for",
                              side_effect=record_toolchain),
            mock.patch.object(cli.build_module, "build", return_value={
                "b2_exit_code": 0, "staged_files": 0, "staged_bytes": 0,
                "seconds": 0.0}),
            mock.patch.object(cli.smoke, "test",
                              return_value={"ok": True, "checks": []}),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_run_unpacks_dependencies_before_resolving_the_toolchain(self):
        order = []
        self._patch_common(order)
        with tempfile.TemporaryDirectory() as tmp:
            args = self._args("run", "--no-fetch")
            workspace = self._workspace(args, tmp)
            self.assertEqual(cli.cmd_run(workspace, args), 0)
        self.assertEqual(order, ["extract_dependencies", "toolchain_for"])

    def test_prepare_unpacks_dependencies_before_resolving_the_toolchain(self):
        order = []
        self._patch_common(order)
        with mock.patch.object(cli, "WINDOWS", True), \
             tempfile.TemporaryDirectory() as tmp:
            args = self._args("prepare", "--no-fetch")
            workspace = self._workspace(args, tmp)
            self.assertEqual(cli.cmd_prepare(workspace, args), 0)
        self.assertEqual(order, ["extract_dependencies", "toolchain_for"])

    def test_all_unpacks_dependencies_before_resolving_the_toolchain(self):
        order = []
        self._patch_common(order)
        with tempfile.TemporaryDirectory() as tmp:
            args = self._args("all", "--no-fetch", "--no-package")
            workspace = self._workspace(args, tmp)
            self.assertEqual(cli.cmd_all(workspace, args), 0)
        self.assertEqual(order[0], "extract_dependencies")
        self.assertIn("toolchain_for", order)


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = load()
        self.root = Path(self.tmp.name)
        fixture.write(self.root, self.config)
        self.workspace = paths.Workspace(self.root, self.config).ensure_layout()

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_complete_build_keeps_the_established_archive_name(self):
        lib_dirs = sorted({c.lib_dir for c in self.config.matrix()})
        self.assertEqual(package.full_archive_name(self.config, lib_dirs),
                         "boost_1_93_0-snapshot-bin-msvc-all.7z")
        self.assertEqual(package.missing_lib_dirs(self.config, lib_dirs), [])

    def test_a_partial_build_is_named_partial(self):
        # Otherwise a one compiler trial produces a file called msvc-all.
        # Unlike the complete archive, this one keeps the compiler and
        # architecture list: there it is the only thing saying what is
        # actually inside.
        name = package.full_archive_name(self.config, ["lib64-msvc-14.3"])
        self.assertEqual(
            name, "boost_1_93_0-snapshot-bin-msvc-partial-14.3-64.7z")
        self.assertNotIn("all", name)
        self.assertEqual(len(package.missing_lib_dirs(
            self.config, ["lib64-msvc-14.3"])), 8)

    def test_a_missing_architecture_is_still_partial(self):
        lib_dirs = sorted(c.lib_dir for c in self.config.matrix()
                          if c.arch.key == "64")
        self.assertIn("partial",
                      package.full_archive_name(self.config, set(lib_dirs)))

    def test_packaging_one_toolset_names_everything_for_that_toolset(self):
        # Exactly the six configuration trial: one complete library
        # directory, and a full archive that does not claim to be a release.
        trial = [c for c in self.config.matrix()
                 if c.toolset.name == "14.3" and c.arch.key == "64"]
        self.assertEqual(len(trial), 6)
        root = Path(self.tmp.name) / "trial"
        fixture.write(root, self.config, trial)
        workspace = paths.Workspace(root, self.config).ensure_layout()
        package.package(workspace)
        names = {p.name for p in workspace.out.iterdir()}
        self.assertIn("boost_1_93_0-snapshot-bin-msvc-14.3-64.zip", names)
        self.assertIn("boost_1_93_0-snapshot-bin-msvc-partial-14.3-64.7z",
                      names)
        self.assertNotIn("boost_1_93_0-snapshot-bin-msvc-all.7z", names)
        # and that one zip holds every variant of the six
        with zipfile.ZipFile(
                workspace.out / "boost_1_93_0-snapshot-bin-msvc-14.3-64.zip"
        ) as archive:
            entries = archive.namelist()
        for tag in ("-mt-x64-", "-mt-gd-x64-", "-mt-s-x64-", "-mt-sgd-x64-"):
            self.assertTrue(any(tag in name for name in entries), tag)

    def test_packaging_produces_the_expected_artifacts(self):
        package.package(self.workspace)
        out = self.workspace.out
        names = {path.name for path in out.iterdir()}
        self.assertIn(self.config.full_archive_name, names)
        self.assertIn("DEPENDENCY_VERSIONS.txt", names)
        self.assertIn("SHA256SUMS", names)
        self.assertIn("result_matrix.txt", names)
        self.assertIn("boost_1_93_0-snapshot-32bitlog.txt", names)
        self.assertIn("boost_1_93_0-snapshot-64bitlog.txt", names)
        self.assertIn("boost_1_93_0-snapshot-arm64bitlog.txt", names)
        for toolset in ("14.1", "14.2", "14.3", "14.5"):
            for arch in ("32", "64"):
                self.assertIn(
                    "boost_1_93_0-snapshot-bin-msvc-{}-{}.zip".format(
                        toolset, arch), names)
        # arm64 ships the same way as the rest: its own per compiler zip,
        # and its libraries inside the one .7z with everything else.
        self.assertIn("boost_1_93_0-snapshot-bin-msvc-14.5-arm64.zip", names)

    def test_the_arm64_zip_holds_arm64_libraries(self):
        # Same zip layout as the x86 and x64 ones, with Boost's own a64 tag
        # instead of x86/x64 in the file names.
        package.package(self.workspace, full=False, checksums=False)
        target = self.workspace.out / \
            "boost_1_93_0-snapshot-bin-msvc-14.5-arm64.zip"
        with zipfile.ZipFile(target) as archive:
            entries = [name for name in archive.namelist()
                       if not name.endswith("/")]
        self.assertTrue(entries)
        self.assertTrue(all(name.startswith("libarm64-msvc-14.5/")
                            for name in entries), entries[:5])
        self.assertIn("libarm64-msvc-14.5/DEPENDENCY_VERSIONS.txt", entries)
        for tag in ("-mt-a64-", "-mt-gd-a64-", "-mt-s-a64-", "-mt-sgd-a64-"):
            self.assertTrue(any(tag in name for name in entries), tag)
        self.assertFalse(any("-x64-" in name for name in entries))

    def test_the_full_archive_holds_every_architecture(self):
        # "bundle arm64 in the same 7z" -- the everything archive is the
        # source tree with every libNN-msvc-X.Y directory merged into it.
        lib_dirs = package.merge_into_source(self.workspace,
                                             self.workspace.stages)
        self.assertIn("libarm64-msvc-14.5", lib_dirs)
        self.assertIn("lib32-msvc-14.1", lib_dirs)
        self.assertIn("lib64-msvc-14.5", lib_dirs)
        self.assertEqual(package.full_archive_name(self.config, lib_dirs),
                         "boost_1_93_0-snapshot-bin-msvc-all.7z")

    def test_a_missing_arm64_build_makes_the_archive_partial(self):
        lib_dirs = sorted({c.lib_dir for c in self.config.matrix()
                           if c.arch.key != "arm64"})
        self.assertIn("partial",
                      package.full_archive_name(self.config, lib_dirs))
        self.assertEqual(package.missing_lib_dirs(self.config, lib_dirs),
                         ["libarm64-msvc-14.5"])

    def test_a_per_configuration_zip_holds_one_library_directory(self):
        package.package(self.workspace, full=False, checksums=False)
        target = self.workspace.out / \
            "boost_1_93_0-snapshot-bin-msvc-14.3-64.zip"
        with zipfile.ZipFile(target) as archive:
            entries = [name for name in archive.namelist()
                       if not name.endswith("/")]
        self.assertTrue(entries)
        self.assertTrue(all(name.startswith("lib64-msvc-14.3/")
                            for name in entries), entries[:5])
        self.assertIn("lib64-msvc-14.3/DEPENDENCY_VERSIONS.txt", entries)
        # Every variant of this compiler and architecture ends up together.
        self.assertTrue(any("-mt-gd-x64-" in name for name in entries))
        self.assertTrue(any("-mt-sgd-x64-" in name for name in entries))
        self.assertTrue(any(name.endswith(".dll") for name in entries))

    def test_dependency_versions_records_every_toolset(self):
        package.package(self.workspace, per_config=False, full=False,
                        checksums=False)
        text = (self.workspace.out / "DEPENDENCY_VERSIONS.txt").read_text()
        for toolset in ("14.1", "14.2", "14.3", "14.5"):
            self.assertIn("msvc-" + toolset, text)
        self.assertIn("zlib: 1.3.1", text)
        self.assertIn("bzip2: 1.0.8", text)
        self.assertIn("Python: 3.14.0", text)

    def test_result_matrix_reports_every_configuration(self):
        package.package(self.workspace, per_config=False, full=False,
                        checksums=False)
        text = (self.workspace.out / "result_matrix.txt").read_text()
        for build_config in self.config.matrix():
            self.assertIn("msvc-" + build_config.toolset.name, text)
        self.assertNotIn("?", text)
        self.assertNotIn("FAIL", text)

    def test_result_matrix_reports_failures(self):
        fixture.write(self.root, self.config, ok=False)
        package.package(self.workspace, per_config=False, full=False,
                        checksums=False)
        text = (self.workspace.out / "result_matrix.txt").read_text()
        self.assertIn("FAIL", text)
        self.assertIn("warn", text)

    def test_stage_directories_are_found_in_the_ci_layout(self):
        # actions/download-artifact puts each artifact in its own directory.
        ci_root = self.root / "downloaded"
        for stage in (self.root / "stage").iterdir():
            target = ci_root / ("stage-" + stage.name)
            target.mkdir(parents=True)
            for entry in stage.iterdir():
                entry.rename(target / entry.name)
        found = package.find_stage_dirs(ci_root)
        self.assertEqual(len(found), len(self.config.matrix()))
        package.package(self.workspace, stages_root=ci_root, full=False,
                        checksums=False)
        names = {path.name for path in self.workspace.out.iterdir()}
        self.assertIn("boost_1_93_0-snapshot-bin-msvc-14.5-32.zip", names)
        self.assertIn("boost_1_93_0-snapshot-64bitlog.txt", names)

    def test_discarding_stages_frees_the_inputs(self):
        stages = self.workspace.stages
        package.package(self.workspace, full=False, checksums=False,
                        discard_stages=True)
        self.assertFalse(stages.exists())
        self.assertTrue((self.workspace.out /
                         "boost_1_93_0-snapshot-bin-msvc-14.1-32.zip").exists())

    def _drop_library(self, config_id, library):
        test_json = self.workspace.stages / config_id / "meta" / "test.json"
        record = json.loads(test_json.read_text())
        for check in record["checks"]:
            if check["name"] == "inventory":
                check["libraries"] = [name for name in check["libraries"]
                                      if name != library]
        test_json.write_text(json.dumps(record))

    def test_a_library_missing_from_one_variant_is_reported(self):
        self._drop_library("msvc-14.3-64-release-static-shared", "wave")
        problems = package.check_consistency(self.workspace,
                                             self.workspace.stages)
        self.assertTrue(any("wave" in problem for problem in problems))

    def test_a_library_missing_from_one_toolset_is_reported(self):
        # Present everywhere except msvc-14.1, which the per-toolset grouping
        # cannot see but the per-variant grouping can.
        for build_config in self.config.matrix():
            if build_config.toolset.name == "14.1":
                self._drop_library(build_config.id, "locale")
        problems = package.check_consistency(self.workspace,
                                             self.workspace.stages)
        self.assertTrue(any("locale" in problem and "14.1" in problem
                            for problem in problems), problems[:5])

    def test_boost_python_absent_from_static_runtime_is_not_a_problem(self):
        problems = package.check_consistency(self.workspace,
                                             self.workspace.stages)
        self.assertEqual(problems, [])

    def test_coroutine_and_fiber_absent_from_arm64_is_not_a_problem(self):
        # They are declared conditional in build.toml, so the cross-check
        # between configurations must not report them on every release.
        arm = [c for c in self.config.matrix() if c.arch.key == "arm64"][0]
        self.assertEqual(
            package.unexpected_libraries(self.config, arm),
            {"coroutine", "fiber"})
        problems = package.check_consistency(self.workspace,
                                             self.workspace.stages)
        self.assertEqual(problems, [])

    def test_a_library_missing_only_from_arm64_is_still_reported(self):
        # The point of not requiring coroutine and fiber there is to leave
        # the check able to say something when arm64 loses anything else.
        for build_config in self.config.matrix():
            if build_config.arch.key == "arm64":
                self._drop_library(build_config.id, "regex")
        problems = package.check_consistency(self.workspace,
                                             self.workspace.stages)
        self.assertTrue(any("regex" in problem and "arm64" in problem
                            for problem in problems), problems[:5])
        self.assertFalse(any("coroutine" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()
