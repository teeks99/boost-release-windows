"""Tests for the parts of boostwin that do not need a compiler.

    python -m unittest discover -s tests
"""
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path

from boostwin import config as config_module
from boostwin import package, paths, smoke

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
            * len(config.variants) * len(config.threadings) \
            * len(config.link_combos)
        self.assertEqual(len(matrix), expected)
        self.assertEqual(len({c.id for c in matrix}), len(matrix))

    def test_configuration_identity(self):
        config = load()
        found = [c for c in config.matrix()
                 if c.id == "msvc-14.3-64-release-static-shared"]
        self.assertEqual(len(found), 1)
        build_config = found[0]
        self.assertEqual(build_config.lib_dir, "lib64-msvc-14.3")
        self.assertEqual(build_config.runner, "windows-2025")
        self.assertIn("toolset=msvc-14.3", build_config.b2_properties)
        self.assertIn("address-model=64", build_config.b2_properties)
        self.assertIn("runtime-link=shared", build_config.b2_properties)

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

    def test_full_archive_name_follows_the_architectures(self):
        config = load()
        self.assertEqual(config.full_archive_name,
                         "boost_1_93_0-snapshot-bin-msvc-all-32-64.7z")

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
        parsed = smoke.parse_library_name(
            "libboost_filesystem-vc143-mt-sgd-x64-1_92.lib")
        self.assertEqual(parsed["library"], "filesystem")
        self.assertEqual(parsed["toolset_tag"], "vc143")
        self.assertEqual(parsed["option_tags"], ["mt", "sgd"])
        self.assertEqual(parsed["arch_tag"], "x64")

    def test_rejects_names_that_are_not_boost_libraries(self):
        self.assertIsNone(smoke.parse_library_name("DEPENDENCY_VERSIONS.txt"))
        self.assertIsNone(smoke.parse_library_name("zlib.lib"))

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
            self.assertEqual(smoke.expected_option_tags(build_config),
                             ["mt"] + wanted[key])
            seen.add(key)
        self.assertEqual(seen, set(wanted))

    def test_expected_arch_tag_handles_arm(self):
        config = load()
        sixty_four = [c for c in config.matrix()
                      if c.arch.key == "64" and c.toolset.name == "14.3"][0]
        self.assertEqual(smoke.expected_arch_tag(sixty_four), "x64")

        arm_config = type(sixty_four)(
            toolset=sixty_four.toolset, arch=config.archs["arm64"],
            variant="release", threading="multi", link="static",
            runtime_link="shared")
        self.assertEqual(smoke.expected_arch_tag(arm_config), "a64")
        self.assertEqual(arm_config.lib_dir, "libarm64-msvc-14.3")
        self.assertIn("architecture=arm", arm_config.b2_properties)
        self.assertIn("address-model=64", arm_config.b2_properties)

    def test_conditional_libraries_apply_only_where_configured(self):
        config = load()
        shared_runtime = [c for c in config.matrix()
                          if c.runtime_link == "shared"][0]
        static_runtime = [c for c in config.matrix()
                          if c.runtime_link == "static"][0]
        self.assertIn("python314", smoke.required_libraries(config, shared_runtime))
        self.assertNotIn("python314",
                         smoke.required_libraries(config, static_runtime))


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
        result = smoke.check_inventory(self.workspace, self.build_config)
        self.assertTrue(result["ok"], result.get("problems"))
        self.assertIn("filesystem", result["libraries"])

    def test_a_missing_required_library_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        for path in lib_dir.glob("*boost_thread-*"):
            path.unlink()
        result = smoke.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])
        self.assertTrue(any("thread" in problem
                            for problem in result["problems"]))

    def test_a_file_from_the_wrong_variant_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        # A release file that has strayed into the debug staging directory.
        (lib_dir / "libboost_thread-vc143-mt-s-x64-1_93.lib").write_text("x")
        result = smoke.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])
        self.assertTrue(any("option tags" in problem
                            for problem in result["problems"]))

    def test_a_file_from_the_wrong_toolset_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        (lib_dir / "libboost_thread-vc142-mt-sgd-x64-1_93.lib").write_text("x")
        result = smoke.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])
        self.assertTrue(any("toolset tag" in problem
                            for problem in result["problems"]))

    def test_an_empty_staging_directory_fails(self):
        lib_dir = self.workspace.stage_lib(self.build_config)
        for path in lib_dir.iterdir():
            path.unlink()
        result = smoke.check_inventory(self.workspace, self.build_config)
        self.assertFalse(result["ok"])


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = load()
        self.root = Path(self.tmp.name)
        fixture.write(self.root, self.config)
        self.workspace = paths.Workspace(self.root, self.config).ensure_layout()

    def tearDown(self):
        self.tmp.cleanup()

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
        for toolset in ("14.1", "14.2", "14.3", "14.5"):
            for arch in ("32", "64"):
                self.assertIn(
                    "boost_1_93_0-snapshot-bin-msvc-{}-{}.zip".format(
                        toolset, arch), names)

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


if __name__ == "__main__":
    unittest.main()
