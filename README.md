Boost Windows release builds
============================

Builds the official Windows binaries for a Boost release: every supported MSVC
toolset, both architectures, and every library variant `b2 --build-type=complete`
produces on Windows.

The work is split so that **one job builds one configuration** — for example
`msvc-14.5-64-debug-static-shared`. That keeps each job well inside a GitHub
runner's six hour limit and its 14 GB of disk, and it means a single failing
variant no longer costs you the whole release. The same driver runs the whole
matrix on one machine when you want it to.

```
python -m boostwin info                     # what is configured
python -m boostwin matrix                   # the 48 configurations
python -m boostwin all --jobs 4             # build all of them here
python -m boostwin run --id msvc-14.3-64-release-static-shared
```

```
$ python -m boostwin info
...
toolsets        : msvc-14.1, msvc-14.2, msvc-14.3, msvc-14.5
architectures   : 32, 64
variants        : debug, release
link            : shared/shared, static/shared, static/static   (link/runtime-link)
threading       : multi
configurations  : 48  (8 toolset+architecture pairs x 2 variants x 3 link combinations)
```

Which GitHub runner image a toolset builds on is a CI detail, so it stays out
of both listings; `matrix --runners` shows it when you want it.


Requirements
------------

*   **Python 3.11 or newer.** No third party packages; `build.toml` is read
    with the standard library's `tomllib`.
*   **To build:** Windows with the Visual Studio versions named in
    `build.toml`. Missing side-by-side toolsets (v141, v142) are installed
    automatically with the Visual Studio installer.
*   **To fetch, package or inspect the matrix:** anything, including Linux.
    Only `build` and `test` need a compiler.


What gets published
-------------------

For `boost_1_93_0`:

| File | Contents |
| --- | --- |
| `boost_1_93_0-bin-msvc-14.3-64.zip` | one `lib64-msvc-14.3` directory: the `.lib`, `.dll` and `.pdb` files for that compiler and architecture, plus `DEPENDENCY_VERSIONS.txt`. One zip per compiler/architecture. |
| `boost_1_93_0-bin-msvc-all-32-64.7z` | the whole tree: Boost's headers and every `libNN-msvc-X.Y` directory. A build of only part of the matrix is named `...-bin-msvc-partial-14.3-64.7z` instead, so it cannot be mistaken for a release. |
| `boost_1_93_0-32bitlog.txt`, `-64bitlog.txt` | every configuration's b2 output for that architecture, concatenated. |
| `DEPENDENCY_VERSIONS.txt` | generated from the compilers and dependencies the build actually used. |
| `result_matrix.txt` | generated from the smoke test results. |
| `SHA256SUMS` | checksums for everything above. |

The per-configuration zips hold binaries only. They are meant to sit beside the
matching Boost source release (the same archive this build starts from), which
is where the headers come from. The `.7z` is the self-contained option.

Configuration
-------------

Everything lives in [build.toml](build.toml) — which release to build, where
the dependencies come from, which toolsets run on which runner image, and which
variants make up the matrix. CI reads the same file, so there is no second
place to update.

To build a different release, edit `[release]`:

```toml
[release]
version = "93"
minor_version = "0"
type = "rc"        # master-snapshot | beta-rc | beta | rc | release
repo = "archives"
rc = 1
```

or override it for one run, from the command line (`--release-version 93
--type rc`) or the environment (`BOOSTWIN_RELEASE_VERSION`,
`BOOSTWIN_RELEASE_TYPE`, ...), which is how the workflow's dispatch inputs get
in.


Commands
--------

| Command | What it does |
| --- | --- |
| `info` | the resolved release, dependencies and every matrix dimension |
| `matrix` | list the build configurations; `--runners` adds the GitHub runner image, `--json` / `--github` are for CI |
| `fetch` | download the Boost source and the dependency archives |
| `prepare` | unpack everything and build `b2` |
| `toolchain` | locate the compiler and write `user-config.jam` (for debugging) |
| `build` | run b2 for one configuration and stage the result |
| `test` | smoke test one configuration |
| `run` | `prepare` + `build` + `test` for one configuration |
| `all` | every selected configuration, then `package` |
| `package` | turn staged output into the release archives |
| `clean` | remove build output |

Every command takes the same filters — `--id`, `--toolset`, `--arch`,
`--variant`, `--link`, `--runtime-link` — repeatable and combinable. The
per-configuration commands need the filters to narrow down to exactly one.

```
python -m boostwin all --toolset 14.3 --arch 64      # one compiler only
python -m boostwin all --jobs 4 --b2-jobs 8          # 4 configs at a time
python -m boostwin package --stages ./downloaded     # package artifacts from CI
```

`--build-root` (or `$BOOSTWIN_BUILD_ROOT`, or `[build].build_root`) says where
to work. It defaults to `D:\boostwin` when a D: drive exists, because the build
needs tens of gigabytes. The layout under it is:

```
downloads/            the source and dependency archives, downloaded once
deps/                 unpacked Python, zlib, bzip2 and 7-Zip
src/boost_1_93_0/     the Boost source, with b2 built in it
work/<config id>/     bin.v2, user-config.jam, the MSVC setup script
stage/<config id>/    libNN-msvc-X.Y plus meta/ (logs, toolchain, test results)
out/                  the release archives
```


How a toolset is selected
-------------------------

A GitHub runner image ships one Visual Studio, but each one carries several
MSVC toolsets side by side and the installer can add more. So a toolset entry
names a Visual Studio *version range*, the toolset to select inside it, and
the MSVC versions that are acceptable:

```toml
[[toolset]]
name = "14.1"
runner = "windows-2022"
vs_version_range = "[17.0,18.0)"     # Visual Studio 2022
vcvars_ver = "14.16"                 # the v141 toolset inside it
msvc_versions = ["14.1"]             # what the result has to be
archs = ["32", "64"]
install_components = ["Microsoft.VisualStudio.Component.VC.v141.x86.x64"]
```

`boostwin toolchain` finds that Visual Studio with `vswhere`, adds any missing
`install_components`, resolves the exact `VC/Tools/MSVC/<version>` directory,
then writes a small batch file that calls `vcvarsall.bat <arch>
-vcvars_ver=<that exact version>`. `user-config.jam` points b2 at both that
script and the matching `cl.exe`, so nothing is left to auto-detection and the
produced libraries carry the right `vc141` tag.

`msvc_versions` is the safety net. If the resolved toolset is not from one of
those families the build stops and says so, rather than quietly producing (for
example) v145 binaries named `vc143`. Which runner label carries which Visual
Studio is not stable -- GitHub moved `windows-latest` and `windows-2025` onto
the Visual Studio 2026 image in June 2026, leaving `windows-2022` as the only
label with Visual Studio 2022 -- so the current mapping is:

| toolset | runner | Visual Studio | MSVC |
| --- | --- | --- | --- |
| msvc-14.1 | `windows-2022` | 2022 | v141, installed on demand |
| msvc-14.2 | `windows-2022` | 2022 | v142, side by side |
| msvc-14.3 | `windows-2022` | 2022 | v143, the default |
| msvc-14.5 | `windows-2025` | 2026 | v145, the default |

`python -m boostwin toolchain --list` prints every Visual Studio on a machine
and which toolset each one satisfies, which is the quickest way to check a
runner image against this table. The build jobs run it too, so it is in the
log before anything can fail.

When `windows-2022` is eventually retired, msvc-14.3 moves to a Visual Studio
2026 image by setting `vcvars_ver = "14.44"` -- that toolset ships with VS 2026
as a side-by-side component, and `msvc_versions` will confirm it.


The smoke tests
---------------

Run per configuration, against the libraries that configuration just staged:

1.  **inventory** — every staged file is named for this exact configuration
    (`vc143`, `mt`, `sgd`, `x64` and so on), and no required library is
    missing. The required list is `[smoke].required_libs` in `build.toml`.
2.  **compile** — [smoke/smoke.cpp](smoke/smoke.cpp) uses about fifteen Boost
    libraries and names none of them: everything is pulled in by Boost's
    auto-linking. It is compiled with `BOOST_LIB_DIAGNOSTIC`, and every Boost
    name the compiler asks for must be a file that was actually staged. That
    is the check that library naming and the build variant agree. Libraries
    from the Windows SDK are reported but not required -- Boost.Atomic
    auto-links `synchronization.lib` for `WaitOnAddress`.

    `[smoke].extra_link_libs` names the few libraries Boost fails to
    auto-link. Boost.JSON's compiled code calls `boost::charconv::to_chars`
    but only includes charconv's *detail* config, which carries no autolink
    block; a shared build hides that inside the DLL, a static one does not
    link at all. They are resolved from the staged directory by stem, so the
    file name still comes from what the build actually produced.
3.  **run** — the program runs and its checks pass, which exercises threads,
    filesystem, serialization and a zlib and bzip2 round trip through
    Boost.Iostreams (so the bundled zlib and bzip2 really did get built in).
4.  **python** — a Boost.Python extension module is built and imported with the
    matching interpreter. Only for release / shared / shared, since the
    dependency Python packages carry no debug binaries. The staged directory
    is handed to `os.add_dll_directory`, because Python 3.8 and later do not
    use `PATH` to resolve an extension module's DLLs -- which is what a user
    of these binaries has to do too.

`package` also cross-checks the configurations against each other: all six
variants of a compiler and architecture should contain the same libraries.


GitHub Actions
--------------

[.github/workflows/build.yaml](.github/workflows/build.yaml) has four jobs:

*   **matrix** — runs `boostwin matrix --github` to produce the job list.
*   **downloads** — fetches the source and dependencies once and uploads them
    as an artifact, so the Boost servers see one download per run rather than
    one per configuration.
*   **build** — one job per configuration, on the runner image its toolset
    names. Uploads `stage-<config id>`.
*   **package** — downloads every stage artifact and assembles the release.

Use **Run workflow** to build a specific release; the inputs map onto the
`[release]` settings. `configs`, `toolsets`, `archs` and `variants` narrow the
matrix when you only need to rebuild part of it -- `configs` takes exact
configuration ids, which is what you want after one job fails.


### Trying a change to the workflow

`workflow_dispatch` only appears in the Actions tab once the workflow file is
on the default branch, so a change to this workflow cannot be tested with
**Run workflow** until it has been merged. Pushes and pull requests have no
such restriction: both run the version of the workflow in the branch itself.

So, while working on a branch:

*   Add the branch to `on: push: branches:`. Every push then runs the
    workflow, including the packaging job.
*   A push to anything other than `master`, and every pull request, builds
    only the `TRIAL_*` slice set in the workflow's `env:` block -- three
    configurations by default, which finish in about the time one does.
    Widen or narrow that slice as you work through what you want to prove.
*   A pull request runs the same slice but skips packaging, which makes it
    the cheap option when you only want to know that the build still works.

Remove the branch from `branches:` when the work merges.

Jobs do not fail the run when b2 reports errors — Boost does not always build
cleanly everywhere, and the result matrix is part of the release. The smoke
tests are what decide whether a configuration is good.

Publishing
----------

Building is only half of it. [upload_checklist.txt](upload_checklist.txt) has
the steps for staging and uploading a release, and
[stage_release.py](stage_release.py), [aws_upload.py](aws_upload.py) and
[artifactory_upload.py](artifactory_upload.py) are the tools it refers to.
