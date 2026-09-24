Boost Windows release builds
============================

Builds the official Windows binaries for a Boost release: every supported MSVC
toolset, every architecture, and every library variant `b2 --build-type=complete`
produces on Windows.

The work is split so that **one job builds one configuration** — for example
`msvc-14.5-64-debug-static-shared`. That keeps each job well inside a GitHub
runner's six hour limit and its 14 GB of disk, and it means a single failing
variant no longer costs you the whole release. The same driver runs the whole
matrix on one machine when you want it to.

```
python -m boostwin info                     # what is configured
python -m boostwin matrix                   # the 54 configurations
python -m boostwin all --jobs 4             # build all of them here
python -m boostwin run --id msvc-14.3-64-release-static-shared
```

```
$ python -m boostwin info
...
toolsets        : msvc-14.1, msvc-14.2, msvc-14.3, msvc-14.5
architectures   : 32, 64, arm64
variants        : debug, release
link            : shared/shared, static/shared, static/static   (link/runtime-link)
threading       : multi
configurations  : 54  (9 toolset+architecture pairs x 2 variants x 3 link combinations)
```

Nine pairs rather than twelve because `arm64` is built by msvc-14.5 alone.

This file is about running a build. [CONTRIBUTING.md](CONTRIBUTING.md) is about
how it works — how a toolset is found, how the matrix reaches CI, what the
smoke tests actually check, and the Windows path length and Boost.Context
problems that arm64 turned up.


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
| `boost_1_93_0-bin-msvc-14.3-64.zip` | one `lib64-msvc-14.3` directory: the `.lib`, `.dll` and `.pdb` files for that compiler and architecture, plus `DEPENDENCY_VERSIONS.txt`. One zip per compiler/architecture, `...-14.5-arm64.zip` included. |
| `boost_1_93_0-bin-msvc-all.7z` | the whole tree: Boost's headers and every `libNN-msvc-X.Y` directory, every compiler and every architecture. A build of only part of the matrix spells out what is in it -- `...-bin-msvc-partial-14.3-64.7z` -- so it cannot be mistaken for a release. |
| `boost_1_93_0-32bitlog.txt`, `-64bitlog.txt`, `-arm64bitlog.txt` | every configuration's b2 output for that architecture, concatenated. |
| `DEPENDENCY_VERSIONS.txt` | generated from the compilers and dependencies the build actually used. |
| `result_matrix.txt` | generated from the smoke test results. |
| `SHA256SUMS` | checksums for everything above. |

The per-configuration zips hold binaries only. They are meant to sit beside the
matching Boost source release (the same archive this build starts from), which
is where the headers come from. The `.7z` is the self-contained option.

Two things to pass on to anyone using the **arm64** binaries, both of which
come from Boost itself rather than from anything here:

*   **Code that uses Boost.Context has to be compiled with `BOOST_USE_WINFIB`
    defined**, or it will fail to link on `make_fcontext`. Nothing
    auto-detects this.
*   **Boost.Coroutine and Boost.Fiber are not in the arm64 zip.** They do not
    build for Windows on ARM64.

[Boost.Context on arm64](CONTRIBUTING.md#boostcontext-on-arm64-and-what-it-costs)
has the reasons.


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
type = "rc"        # rc | beta-rc | master-snapshot
repo = "archives"
rc = 1
```

or leave it alone and override it for one run -- see
[Choosing the release](#choosing-the-release) below.

[build.vsall.toml](build.vsall.toml) is an alternate configuration for a
workstation that has Visual Studio 2017, 2019, 2022 and 2026 installed
side by side as their own products, rather than one Visual Studio with
older toolsets bolted on as side-by-side components the way the GitHub
runner images do. It builds 32 and 64 bit only -- an arm64 configuration
there would have nothing to run its smoke test on. Point any command at it
with `--config-file`:

```
python -m boostwin --config-file build.vsall.toml all --jobs 4
```


Building on a local machine
---------------------------

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

### Choosing the release

`build.toml` says which release to build. For a one-off build, override it on
the command line instead. These go **before** the subcommand, because they
apply to all of them:

```
python -m boostwin --release-version 92 --type rc --rc 2 all
python -m boostwin --release-version 93 --type beta-rc --beta 1 --rc 2 all
python -m boostwin --release-version 93 --type master-snapshot all
```

| Flag | `[release]` key | What it is |
| --- | --- | --- |
| `--release-version` | `version` | the Boost minor version -- `92` for 1.92.0 |
| `--minor-version` | `minor_version` | the patch version, almost always `0` |
| `--type` | `type` | `rc`, `beta-rc` or `master-snapshot` -- see below |
| `--beta` | `beta` | beta number; `beta-rc` needs it |
| `--rc` | `rc` | release candidate number; `rc` and `beta-rc` need it |
| `--repo` | `repo` | where to fetch from: `archives` (the default), `jfrog`, `git` or `local` |

Each one also has an environment variable — `BOOSTWIN_RELEASE_VERSION`,
`BOOSTWIN_RELEASE_TYPE`, `BOOSTWIN_RELEASE_BETA`, `BOOSTWIN_RELEASE_RC` and so
on. An empty value is ignored, which is how the workflow can forward its
dispatch inputs without special-casing the blank ones. A command line flag
beats the environment, and both beat `build.toml`.

What each type fetches, and what comes out:

| `--type` | what it builds | source archive | release name |
| --- | --- | --- | --- |
| `rc --rc 2` | a release | `boost_1_92_0_rc2.tar.bz2` | `boost_1_92_0` |
| `beta-rc --beta 1 --rc 2` | a beta | `boost_1_93_0_b1_rc2.tar.bz2` | `boost_1_93_0_b1` |
| `master-snapshot` | a snapshot of master | `boost_1_93_0-snapshot.tar.bz2` | `boost_1_93_0-snapshot` |

**A release is always built from its candidate**, which is why there is no
row here for `release` or `beta`. The release name already has the `_rcN`
dropped, so `--type rc --rc 2` produces `boost_1_92_0-bin-msvc-14.3-64.zip`
— exactly the file names the finished release ships under. `beta-rc` does
the same for a beta.

`release` and `beta` do exist: they fetch the archive without the `_rcN`,
and `boostwin` still accepts them. They are for an emergency — a release
whose candidate is gone, say — not for a normal build, so they are left out
of the table above rather than offered as a choice.

`info` resolves all of it without downloading anything, which is the quickest
way to check you asked for what you meant:

```
python -m boostwin --release-version 92 --type rc --rc 2 info
```


### Where it builds

`--build-root` (or `$BOOSTWIN_BUILD_ROOT`, or `[build].build_root`) says where
to work. It defaults to `D:\boostwin` when a D: drive exists, because the build
needs tens of gigabytes. The layout under it is:

```
downloads/            the source and dependency archives, downloaded once
deps/                 unpacked Python, zlib, bzip2 and 7-Zip
src/boost_1_93_0/     the Boost source, with b2 built in it
work/<short id>/      bin.v2, user-config.jam, the MSVC setup script
stage/<config id>/    libNN-msvc-X.Y plus meta/ (logs, toolchain, test results)
out/                  the release archives
```

The work directories are named in a shorthand — `msvc-14.5-arm64-lib-sgd`
rather than `msvc-14.5-arm64-debug-static-static` — which
[the build root layout](CONTRIBUTING.md#the-build-root-layout) explains.
Everything else uses the configuration id you see in `matrix`.

A build that finds a problem says so through the smoke tests rather than
through b2's exit code; `result_matrix.txt` is the summary, and
[the smoke tests](CONTRIBUTING.md#the-smoke-tests) says what each column
means.


Building in GitHub Actions
--------------------------

Use **Run workflow** on
[Build Boost Windows binaries](.github/workflows/build.yaml) to build a
release. The inputs map onto the `[release]` settings in `build.toml`:

| Input | Effect |
| --- | --- |
| `version`, `minor_version` | the Boost version, e.g. 93 and 0 |
| `type` | `rc`, `beta-rc` or `master-snapshot` ([not `release` or `beta`](#choosing-the-release)) |
| `beta`, `rc` | the beta and release candidate numbers |
| `configs` | exact configuration ids, comma separated |
| `toolsets`, `archs`, `variants` | narrow the matrix by dimension |
| `package` | assemble the release archives when the builds finish |

Leave an input blank to take what `build.toml` says. `configs`, `toolsets`,
`archs` and `variants` narrow the matrix when you only need part of it --
`configs` takes exact configuration ids, which is what you want after one job
fails:

```
configs: msvc-14.5-arm64-release-static-static,msvc-14.5-arm64-debug-static-static
```

The release archives come out as a `<release name>-binaries` artifact on the
**package** job; each configuration also uploads its own `stage-<config id>`,
which is what you want when you are chasing one failure.

Jobs do not fail the run when b2 reports errors — Boost does not always build
cleanly everywhere, and the result matrix is part of the release. The smoke
tests are what decide whether a configuration is good.


Publishing
----------

Building is only half of it. [upload_checklist.txt](upload_checklist.txt) has
the steps for staging and uploading a release, and
[stage_release.py](stage_release.py), [aws_upload.py](aws_upload.py) and
[artifactory_upload.py](artifactory_upload.py) are the tools it refers to.
