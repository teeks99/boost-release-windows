Working on the build itself
===========================

[README.md](README.md) is about running a build. This is about how it is put
together: how a compiler is found, how the matrix reaches CI, what the smoke
tests actually check, and the handful of places where Windows or Boost forced
a decision that is not obvious from the code.

The driver is the `boostwin` package. It has no third party dependencies, and
everything except `build` and `test` runs anywhere — which is why the matrix,
the packaging and all of the tests can be exercised on Linux.

```
python -m unittest discover -s tests -t .
```

The tests cover everything that does not need a compiler: the matrix, the
release naming, toolset selection against a fake Visual Studio tree, the
inventory and autolink checks, and packaging against a fake build root. CI
runs them before it starts any Windows runner, so a broken `build.toml` costs
seconds rather than a matrix of jobs.


The build root layout
---------------------

```
downloads/            the source and dependency archives, downloaded once
deps/                 unpacked Python, zlib, bzip2 and 7-Zip
src/boost_1_93_0/     the Boost source, with b2 built in it
work/<short id>/      bin.v2, user-config.jam, the MSVC setup script
stage/<config id>/    libNN-msvc-X.Y plus meta/ (logs, toolchain, test results)
out/                  the release archives
```

Only the work tree uses the short id, and it spells the variant the way b2
does -- `msvc-14.5-arm64-lib-sgd` rather than
`msvc-14.5-arm64-debug-static-static`:

*   `lib` or `dll` for `link`. b2 gives a static library the prefix `lib`
    and an import library no prefix at all, and a directory cannot be named
    "no prefix".
*   then b2's ABI tag: `s` for a static runtime, `g` for a debug runtime and
    `d` for a debug build -- so `sgd`. (b2 also has `y` for python-debugging
    and `p`/`n` for STLport; neither is in this matrix.) A release build
    against a shared runtime has no ABI tag at all, in b2's naming and here.

b2's `mt` is the one part left out. Every configuration is multithreaded --
single-threaded Boost is a relic -- so it distinguishes nothing in a
directory name. The staged files still carry it, because that part is b2's
to decide, not ours, and `boostwin.inventory` expects it there.

So `work/msvc-14.5-arm64-lib-sgd/` reads like the
`libboost_*-vc145-mt-sgd-a64-1_93.lib` files in the stage directory beside
it, and both spellings come from one definition on `BuildConfig`, so they
cannot drift apart. The toolset and the architecture stay verbatim: they
are what `--toolset` and `--arch` take, and what tells you which compiler a
directory belongs to. Anything the short id leaves out has to stay constant
across the matrix, and loading a `build.toml` where it does not is refused
rather than left to build two configurations on top of each other.

The reason for shortening it at all is that everything b2 creates lives
under there, and those paths run close to Windows' limit (see
[below](#why-the-intermediate-paths-are-hashed)); nothing reads the
directory back by name. The staged output keeps the full id, because the CI
artifact names and `package` do match on it.


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

| toolset | architectures | runner | Visual Studio | MSVC |
| --- | --- | --- | --- | --- |
| msvc-14.1 | 32, 64 | `windows-2022` | 2022 | v141, installed on demand |
| msvc-14.2 | 32, 64 | `windows-2022` | 2022 | v142, side by side |
| msvc-14.3 | 32, 64 | `windows-2022` | 2022 | v143, the default |
| msvc-14.5 | 32, 64 | `windows-2025` | 2026 | v145, the default |
| msvc-14.5 | arm64 | `windows-11-vs2026-arm` | 2026 | v145, the default |

`python -m boostwin toolchain --list` prints every Visual Studio on a machine
and which toolset each one satisfies, which is the quickest way to check a
runner image against this table. The build jobs run it too, so it is in the
log before anything can fail.

When `windows-2022` is eventually retired, msvc-14.3 moves to a Visual Studio
2026 image by setting `vcvars_ver = "14.44"` -- that toolset ships with VS 2026
as a side-by-side component, and `msvc_versions` will confirm it.


Architectures
-------------

`32` and `64` are built by every toolset. `arm64` is built by **msvc-14.5
only**: v141, v142 and v143 are there for people still targeting the x86 and
x64 desktops, and the Arm64 runner images carry Visual Studio 2022 or 2026,
not the older products.

arm64 is built **natively**, on an Arm64 runner, rather than cross compiled
from the x64 one. Cross compiling would produce the libraries perfectly well,
but nothing could then be run: the smoke program and the Boost.Python
extension are built *and executed* as part of every configuration's test, and
that is most of what the test is worth. So the one architecture that needs a
different machine names it, and the rest of the toolset is unchanged:

```toml
[[toolset]]
name = "14.5"
runner = "windows-2025"
archs = ["32", "64", "arm64"]
arch_runners = { arm64 = "windows-11-vs2026-arm" }
```

`windows-11-vs2026-arm` rather than `windows-11-arm` for the same reason
msvc-14.3 still says `windows-2022`: the plain label carries Visual Studio
2022 until GitHub moves it, and the explicit one is right either way. If
`arch_runners` names an architecture the toolset does not build, the config
is rejected rather than quietly ignored.

Everything downstream follows from the architecture key, so arm64 needs no
special handling: `--arch arm64` selects it, b2 gets
`architecture=arm address-model=64`, the libraries stage into
`libarm64-msvc-14.5` with Boost's own `-a64-` tag, and they ship as
`...-bin-msvc-14.5-arm64.zip` beside the other per compiler zips and inside
the same full `.7z`.

The only piece that is not derived is the Visual Studio smoke project: a
`.vcxproj` has to declare `Configuration|Platform` pairs literally, so
[smoke/vs/msvc-14.5/](smoke/vs/msvc-14.5/) carries the six `ARM64`
configurations alongside `Win32` and `x64`. A test checks that every
configuration in the matrix has one, so an architecture added to
`build.toml` and not to the projects fails before CI starts.


### Boost.Context on arm64, and what it costs

Two things about the arm64 binaries come from Boost itself rather than from
anything here, and both are worth knowing before you use them.

**Boost.Context is the winfib build.** There is no fcontext assembly for
arm64/pe, so Boost.Context's own Jamfile forces `<context-impl>winfib` on
Windows ARM64, and that composes to `-DBOOST_USE_WINFIB`. Nothing
auto-detects it: `boost/context/fiber.hpp` selects `fiber_fcontext.hpp`
unless a consumer defines the same macro. So **code using Boost.Context from
the arm64 binaries has to be compiled with `BOOST_USE_WINFIB` defined**, or
it will fail to link on `make_fcontext`. The smoke project defines it for its
`ARM64` configurations rather than hiding the requirement in `smoke.cpp`,
which makes the smoke test prove the rule holds.

**Boost.Coroutine and Boost.Fiber are not in the arm64 zip.** Boost.Context
forces winfib only on itself; both of those libraries are then built without
`BOOST_USE_WINFIB`, compile against the fcontext interface, and fail to link
against the winfib `boost_context`. Coroutine could not work either way -- it
uses `context::detail::fcontext_t` directly. They are declared as conditional
libraries instead of required ones, so an arm64 configuration is not failed
for them:

```toml
[[smoke.conditional_libs]]
libs = ["coroutine", "fiber"]
arch = ["32", "64"]
```

That is narrow on purpose. `package`'s cross-check between configurations
still reports anything *else* that goes missing from arm64 but built
everywhere else, so this does not turn into a blanket exemption.


### Why the intermediate paths are hashed

b2 names every intermediate directory after the whole property set --
`msvc-14.5/release/address-model-64/architecture-arm/link-static/runtime-link-static/threadapi-win32/threading-multi/`
-- 116 characters before the file name starts. `architecture` is only in
there when it is not the default, so an arm64 build's paths run about 32
characters longer than the same x64 one, and an arm64 build with a static
runtime went past Windows' 260 character `MAX_PATH`.

That failure is a quiet one: `lib.exe` cannot open its own response file,
reports `LNK1104`, and b2 carries on with the rest of the build. So
`boost_serialization`, `boost_wserialization`, `boost_type_erasure` and
`boost_python314` were simply absent from the staged output, and the first
sign of it was the inventory check.

`boostwin build` therefore passes b2 `--hash`, which replaces that whole
path with an MD5 of it, and the work directory is named with the
configuration's short id rather than its full one. Together those take the
longest path in the matrix from 266 characters to 184 -- 76 to spare, and
every configuration comes down, not just the two that were over. The cost
is that `bin.v2` directory names are no longer readable, which only matters
with `[build].keep_intermediate` on; where the libraries are staged is
named explicitly with `--stage-libdir` and does not change.


The smoke tests
---------------

Run per configuration, against the libraries that configuration just staged:

1.  **inventory** — every staged file is named for this exact configuration
    (`vc143`, `mt`, `sgd`, `x64` and so on), and no required library is
    missing. The required list is `[smoke].required_libs` in `build.toml`,
    plus any `[[smoke.conditional_libs]]` entry that applies -- those are
    filtered by `variant`, `link`, `runtime_link` and `arch`, which is how
    Boost.Python is expected only against a shared runtime and
    Boost.Coroutine and Boost.Fiber only off arm64.
2.  **compile** — the checked-in Visual Studio project for this toolset
    (`smoke/vs/msvc-<toolset>/BoostSmoke.vcxproj`, built with `msbuild`; see
    below) builds [smoke/smoke.cpp](smoke/smoke.cpp), which uses about
    fifteen Boost libraries and names none of them: everything is pulled in
    by Boost's auto-linking. It is compiled with `BOOST_LIB_DIAGNOSTIC`, and
    every Boost name the compiler asks for must be a file that was actually
    staged. That is the check that library naming and the build variant
    agree. Libraries from the Windows SDK are reported but not required --
    Boost.Atomic auto-links `synchronization.lib` for `WaitOnAddress`.

    `[smoke].extra_link_libs` names the few libraries Boost fails to
    auto-link. Boost.JSON's compiled code calls `boost::charconv::to_chars`
    but only includes charconv's *detail* config, which carries no autolink
    block; a shared build hides that inside the DLL, a static one does not
    link at all. They are resolved from the staged directory by stem, so the
    file name still comes from what the build actually produced.

    A `LNK1104` naming one of ours is reported as "the build did not stage
    ..." rather than as a bare msbuild exit code: a library that failed to
    build fails both this check and **inventory**, and only one of them is
    the cause.
3.  **run** — the program runs and its checks pass, which exercises threads,
    filesystem, serialization and a zlib and bzip2 round trip through
    Boost.Iostreams (so the bundled zlib and bzip2 really did get built in).
    This happens inside the same `msbuild` invocation as **compile**:
    `BoostSmoke.vcxproj` runs the program itself as a post-build step, so
    boostwin never launches it directly. A non-zero exit there is msbuild
    error `MSB3073`, which is how `boostwin.smoke` tells "it failed to
    build" apart from "it built, but running it failed" without spawning a
    process of its own.
4.  **python** — `BoostSmokePython.vcxproj` builds a Boost.Python extension
    module, and its own post-build step imports it with the matching
    interpreter and exercises it -- again inside one `msbuild` invocation,
    the same way. Only for release / shared / shared, since the dependency
    Python packages carry no debug binaries. [smoke/python_ext_test.py](smoke/python_ext_test.py)
    hands the staged directory to `os.add_dll_directory` itself, because
    Python 3.8 and later do not use `PATH` to resolve an extension module's
    DLLs -- which is what a user of these binaries has to do too.

`package` also cross-checks the configurations against each other: all six
variants of a compiler and architecture should contain the same libraries,
and so should the same variant across compilers. A library that
`[[smoke.conditional_libs]]` says does not apply to a configuration is not
counted against it, which is what keeps Boost.Python's absence from the
static-runtime builds, and Coroutine's and Fiber's from arm64, out of the
warnings -- while anything nobody declared still shows up.


### The Visual Studio projects

[smoke/vs/](smoke/vs/) has a real `.sln` for every toolset in `build.toml` --
`msvc-14.1/BoostSmoke.sln`, `msvc-14.2/...`, and so on -- each with a
`BoostSmoke.vcxproj` (builds and runs [smoke/smoke.cpp](smoke/smoke.cpp)) and
a `BoostSmokePython.vcxproj` (builds and imports
[smoke/python_ext.cpp](smoke/python_ext.cpp)). `boostwin test` drives them
with `msbuild` for the **compile**/**run**/**python** checks above and reads
the result out of msbuild's own output -- there is no separate `cl.exe` path,
and boostwin never runs the built `.exe`/`.pyd` itself; the project's own
post-build step does. They can also be opened directly in their own Visual
Studio, or in a newer one -- Visual Studio 2026 can open all four, offering
to retarget the platform toolset for whichever ones it does not have
installed.

Each project has `Debug`/`Release` crossed with the three link/runtime-link
combinations Boost supports (plain, `-Static`, `-StaticRuntime`), times the
platforms its toolset builds: twelve configurations for msvc-14.1 to
msvc-14.3 (`Win32`/`x64`), eighteen for msvc-14.5, which adds `ARM64`.
None of that is enough on its own,
though: the projects do not know where a build staged its libraries. That
comes from a `.props` file under each project's `generated/` folder, named
after the `Configuration|Platform` it belongs to (e.g.
`generated/Release-x64.props`), which `boostwin.vsproj.write_props` writes on
every `boostwin test` run for whichever configuration it just tested. Opening
a project before that has happened once will build (and its post-build step
will fail to run) with no Boost include or library path; build (or `test`)
that configuration from the command line first, or hand-write a
`generated/<Configuration>-<Platform>.props` with
`BoostIncludeDir`/`BoostLibDir` (and, for the Python project,
`PythonIncludeDir`/`PythonLibDir`/`PythonExe`) set yourself.


GitHub Actions
--------------

[.github/workflows/build.yaml](.github/workflows/build.yaml) has four jobs:

*   **matrix** — runs the tests, then `boostwin matrix --github` to produce
    the job list. It runs on Linux and takes seconds, which is the point:
    a mistake in `build.toml` stops here rather than on a matrix of Windows
    runners.
*   **downloads** — fetches the source and dependencies once and uploads them
    as an artifact, so the Boost servers see one download per run rather than
    one per configuration.
*   **build** — one job per configuration, on the runner image that
    configuration names: `runs-on` comes straight out of the matrix, which is
    how the arm64 jobs land on the Arm64 runner while the rest of msvc-14.5
    stays on the x64 one. Uploads `stage-<config id>`.
*   **package** — downloads every stage artifact and assembles the release.

Nothing about the matrix is written twice: the workflow reads the same
`build.toml` that a local build does, through the same code, so CI and a
hand-run build cannot drift apart.


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
