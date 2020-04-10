# Runs the Visual Studio Build for a python version
import os
import shutil
import subprocess
import itertools
import threading
import multiprocessing
import datetime
import sys
import argparse
import tarfile
from string import Template
from timer import Timer
try:
    from urllib.request import urlretrieve
except ImportError: # Python 2
    from urllib import urlretrieve


VERSION = "73"
MINOR_VERSION = "0"
#TYPE = "master-snapshot"
TYPE = "beta-rc"
#TYPE = "rc"
REPO = "bintray"
BETA = 1
RC = 2

BUILD_DRIVE = "D:" + os.sep
BUILD_DIR = "ReleaseBuild"
TIMES = "times.txt"

vc_versions = ["10.0", "11.0", "12.0", "14.0", "14.1", "14.2"]
vc_archs = ["32", "64"]

PACKAGE_PROCESSES = 16

# Binary packages used during build, that we can't get from upstream
tk_boost_deps = "https://boost.teeks99.com/deps/"

python2_ver = "2.7.17"
python3_ver = "3.8.2"
pyvers = ["27", "38"]
py2use = ["8.0", "9.0", "10.0", "11.0", "12.0"]

zlib_ver = "1.2.8"
zlib_base_path = "http://www.zlib.net/fossils/"

bzip2_ver = "1.0.6"
#bzip2_base_path = "http://www.bzip.org/"
bzip2_base_path = tk_boost_deps

inno_ver = "5.6.1_tk1"
inno_compression_threads = 6

bintray_boost = "https://dl.bintray.com/boostorg/"
REPOS = {
    "bintray": {
        "master-snapshot": {
            "url": bintray_boost + "master/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.tar.bz2",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "-snapshot"
        },
        "beta-rc": {
            "url": bintray_boost + "beta/1.{version}.{minor_version}.beta.{beta}.rc{rc}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.tar.bz2",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}_rc{rc}"
        },
        "beta": {
            "url": bintray_boost + "beta/1.{version}.{minor_version}.beta.{beta}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.tar.bz2",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}"
        },
        "rc": {
            "url": bintray_boost + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}_rc{rc}.tar.bz2",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": ""
        },
        "release": {
            "url": bintray_boost + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}.tar.bz2",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": ""
        }
    },
    "local": {
        "b1": {
            "url": "none",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.tar.bz2",
            "source_archive_output": "boost_1_{version}_{minor_version}"
        }
    },
    "git": {
        "develop": {
            "url": "https://github.com/boostorg/boost",
            "branch": "develop",
            "source_archive_output": "boost",
            "archive_suffix": "",
            "file": ""
        },
        "master": {
            "url": "https://github.com/boostorg/boost",
            "branch": "master",
            "source_archive_output": "boost",
            "archive_suffix": "",
            "file": ""
        }
    }
}


class Archive(object):
    def __init__(self, zip_cmd, base_url, package, extensions=[], local_file=None):
        self.zip_cmd = zip_cmd
        self.base_url = base_url
        self.package = package
        self.extensions = extensions
        if not self.extensions:
            base, ext = os.path.splitext(self.package)
            while ext in [".tar", ".bz2", ".xz", ".gz", ".7z", ".zip"]:
                self.package = base
                self.extensions.append(ext)
                base, ext = os.path.splitext(self.package)
            self.extensions.reverse()

        if local_file:
            self.local_file = local_file
        else:
            self.local_file = self.package
        self.download_name = self.package

        for extension in extensions:
            self.download_name += extension
            self.local_file += extension

    def download(self):
        if self.base_url != "none":
            url = self.base_url + self.download_name
            print("Downloading: " + url)
            try:
                urlretrieve(url, self.local_file)
            except:
                print("Error Downloading: " + url)
                raise
            with open(self.local_file, "r") as f:
                pass

    def extract(self):
        if self.zip_cmd == "tarfile":
            print(f"Extracting: {self.local_file}")
            with tarfile.open(self.local_file) as tf:
                tf.extractall()
        else:
            unzip_name = self.local_file
            for extension in reversed(self.extensions):
                subprocess.call(self.zip_cmd + " x " + unzip_name)
                unzip_name = unzip_name[:-len(extension)]

    def get(self):
        self.download()
        self.extract()

    def params(self):
        return [
            self.zip_cmd, self.base_url, self.package, self.extensions,
            self.local_file, self.download_name
        ]


class GitArchive(object):
    def __init__(self, url, branch, output_location):
        self.url = url
        self.branch = branch
        self.builddir, self.reponame = os.path.split(output_location)

    def get(self):
        subprocess.check_call(
            "git clone --recursive {} {} --branch {}".format(
            self.url, self.reponame, self.branch), cwd=self.builddir,
            shell=True)


class RemoteArchive(Archive):
    def __init__(self, params):
        self.zip_cmd = params[0]
        self.base_url = params[1]
        self.package = params[2]
        self.extensions = params[3]
        self.local_file = params[4]
        self.download_name = params[5]


def run_remote_archive(params):
    a = RemoteArchive(params)
    a.get()


def make_installer(options):
    o = options
    installer_file = o['version'] + "-" + o['config'] + ".exe"

    os.mkdir(o['tmp_build_dir'])
    os.chdir(o['tmp_build_dir'])
    subprocess.call(o['zip_cmd'] + " x " + o['source_path'] + ".tar")
    shutil.move(o['source_archive_output'], o['source'])
    shutil.copytree(os.path.join(o['source_path'], o['libs']), os.path.join(o['source'], o['libs']))

    replace = {"FILL_VERSION": o['version'], "FILL_CONFIG": o['config'], "FILL_SOURCE": o['source'], "FILL_NUM_THREADS": o['compression_threads']}
    with open(os.path.join(o['build_path'], "BoostWinInstaller-PyTemplate.iss"), "r") as installer_template:
        stemplate = Template(installer_template.read())
        with open("installer_" + o['config'] + ".iss", "w") as installer:
            installer.write(stemplate.safe_substitute(replace))

    print("Making installer for: " + installer_file)
    subprocess.call('"' + o['inno_cmd'] + '" /cc installer_' + o['config'] + ".iss", shell=True)
    os.chdir(o['build_path'])
    shutil.move(os.path.join(o['tmp_build_dir'], installer_file), installer_file)
    shutil.rmtree(o['tmp_build_dir'])
    print("Installer " + installer_file + " complete")


class Builder(object):
    def __init__(self):
        self.lib_check_dir = "LibraryCheck"
        self.load_args()
        self.archives = []

    def load_args(self):
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "--version",
            help="The part of the boost version that changes." +
            ' e.g. "64" in "boost_1_64_0"', default=VERSION)
        parser.add_argument(
            "--minor-version",
            help="The part of the boost version that can be changed when an" +
            ' error in a build is found. e.g. "0" in "boost_1_64_0"',
            default=MINOR_VERSION)
        parser.add_argument(
            "--type", help="Type of build: master-snapshot, beta, rc, release",
            default=TYPE)
        parser.add_argument(
            "--repo", help="Repo to use for build", default=REPO)
        parser.add_argument(
            "--url", help="base of the URL to get the binary from. " +
            "Combines with file to make the full URL.", default=None)
        parser.add_argument(
            "--file",
            help="file to get from the url. e.g. boost_1_64_0.tar.bz2",
            default=None)
        parser.add_argument(
            "--source-archive-output",
            help="directory name the source file will extract to",
            default=None)
        parser.add_argument(
            "--build-drive",
            help="D rive to use for build, including trailing seperator",
            default=BUILD_DRIVE)
        parser.add_argument(
            "--build-dir",
            help="Directory on build drive to use for build",
            default=BUILD_DIR)
        parser.add_argument(
            "--times", help="file to write build times to",
            default=TIMES)
        parser.add_argument(
            "--keep-intermediate", help="Keep intermediate files (bin.v2)",
            default=False)
        parser.add_argument(
            "--beta",
            help="Beta version to use, only applies if type is 'beta'",
            default=BETA)
        parser.add_argument(
            "--rc",
            help="RC version to use, only applies if type is 'rc'",
            default=RC)

        parser.add_argument(
            "--vc-ver", action='append',
            help='version to build in dotted for e.g. 8.0, 14.1')
        parser.add_argument(
            "--vc-arch", action='append',
            help='architecture to build for e.g. 32 or 64')

        parser.add_argument(
            "--inno-compression-threads", default=inno_compression_threads, 
            help='number of compression threads inno setup uses')
        parser.parse_args(namespace=self)

        if self.vc_arch:
            global vc_archs
            vc_archs = self.vc_arch

        if self.vc_ver:
            global vc_versions
            vc_versions = self.vc_ver

    def make_vars(self):
        self.build_path = os.path.join("/", self.build_drive, self.build_dir)
        self.lib_check_path = os.path.join(self.build_path, self.lib_check_dir)
        self.archive_suffix = ""
        self.source = "boost_1_" + self.version + "_" + self.minor_version
        if self.repo == "git":
            self.source = "boost"
        self.source_path = os.path.join(self.build_path, self.source)
        self.lib_check_path = os.path.join(self.build_path, self.lib_check_dir)
        self.ext_zip_cmd = "tarfile"
        self.zip_cmd = os.path.join(self.build_path, "7z1604/x64/7za.exe")
        self.inno_cmd = os.path.join(self.build_path, "InnoSetup5/Compil32.exe")
        self.times = os.path.abspath(self.times)
        self.set_source_info()

    def set_source_info(self):
        config = REPOS[self.repo][self.type]
        replace = {
            "beta": self.beta, "rc": self.rc, "version": self.version,
            "minor_version": self.minor_version}
        if not self.archive_suffix:
            self.archive_suffix = config["archive_suffix"].format(**replace)

        replace["archive_suffix"] = self.archive_suffix
        if not self.url:
            self.url = config["url"].format(**replace)

        if not self.file:
            self.file = config["file"].format(**replace)

        if not self.source_archive_output:
            self.source_archive_output = config["source_archive_output"].format(**replace)

    def check_user_config_exists(self):
        usrcfg_file = os.path.expanduser("~/user-config.jam")
        if os.path.exists(usrcfg_file):
            raise Exception("~/user-config.jam already exists and would be replaced, please remove it manually")

    def python_ver_for_vc(self, vcver):
        if vcver in py2use:
            return python2_ver
        return python3_ver

    def python_compressed(self, ver):
        major, minor, micro = ver.split(".")
        return major+minor

    def make_user_config(self, vcver):
        pyver = self.python_ver_for_vc(vcver)
        usrcfg_file = os.path.expanduser("~/user-config.jam")
        self.py_config_replace = {}
        for version, arch, end in itertools.product(
                [self.python_compressed(pyver)], ["32", "64"], ["include", "libs"]):
            self.make_python_config_path(version, arch, end)

        repo_dir = os.path.dirname(os.path.relpath(__file__))
        template = None
        if pyver[0] == "2":
            template = os.path.join(self.build_path, "user-config.jam.py2.template")
        else:
            template = os.path.join(self.build_path, "user-config.jam.py3.template")

        with open(template, "r") as uctemp:
            stemplate = Template(uctemp.read())

        with open(usrcfg_file, "w") as usrcfg:
            usrcfg.write(stemplate.safe_substitute(self.py_config_replace))

    def make_python_config_path(self, version, arch, end):
        key = "PY" + version + "_" + arch + end
        path = os.path.join(self.build_path, "Python" + version + "-" + arch, end)
        escaped = os.path.normpath(path).replace("\\", "\\\\")
        self.py_config_replace[key] = escaped

    def make_dirs(self):
        shutil.copytree(os.path.dirname(os.path.realpath(__file__)), self.build_path)

    def make_source_archive(self):
        if self.repo == "git":
            self.archives.append(GitArchive(self.url, REPOS[self.repo][self.type]["branch"], self.source_path))
        else:
            self.archives.append(Archive(self.zip_cmd, self.url, self.file, local_file=self.source))

    def make_dep_archives(self):
        z = self.ext_zip_cmd
        a = self.archives
        a.append(Archive(z, tk_boost_deps, "Python" + python2_ver + "-32", [".tar", ".xz"]))
        a.append(Archive(z, tk_boost_deps, "Python" + python2_ver + "-64", [".tar", ".xz"]))
        a.append(Archive(z, tk_boost_deps, "Python" + python3_ver + "-32", [".tar", ".xz"]))
        a.append(Archive(z, tk_boost_deps, "Python" + python3_ver + "-64", [".tar", ".xz"]))
        a.append(Archive(z, zlib_base_path, "zlib-" + zlib_ver, [".tar", ".gz"]))
        #a.append(Archive(z, bzip2_base_path + bzip2_ver + "/", "bzip2-" + bzip2_ver, [".tar", ".gz"]))
        a.append(Archive(z, bzip2_base_path, "bzip2-" + bzip2_ver, [".tar", ".gz"]))
        a.append(Archive(z, tk_boost_deps, "InnoSetup-" + inno_ver, [".tar", ".xz"]))

    def get_and_extract_archives(self):
        for a in self.archives:
            a.get()

    def get_and_extract_archives_threaded(self):
        workers = [ threading.Thread(target=a.get) for a in self.archives ]
        for worker in workers: worker.start()
        for worker in workers: worker.join()

    def get_and_extract_archives_process(self):
        workers = [ multiprocessing.Process(target=run_remote_archive, args=(a.params(),)) for a in self.archives ]
        for worker in workers: worker.start()
        for worker in workers: worker.join()

    def move_source(self):
        if self.source_archive_output != self.source:
            shutil.move(self.source_archive_output, self.source)

    def set_env_vars(self):
        zlib = os.path.join(self.build_path, "zlib-" + zlib_ver)
        os.environ["ZLIB_SOURCE"] = os.path.normpath(zlib)

        bzip2 = os.path.join(self.build_path, "bzip2-" + bzip2_ver)
        os.environ["BZIP2_SOURCE"] = os.path.normpath(bzip2)

    def make_dependency_versions(self, out="DEPENDENCY_VERSIONS.txt", pyvers=["2", "3"]):
        with open("VS_DEPENDENCY_VERSIONS.txt", "r") as vs_versions:
            with open(out, "w") as dep_ver:
                if "2" in pyvers:
                    dep_ver.write("Python 2: " + python2_ver + "\n")
                    dep_ver.write("Python 2: " + python2_ver + " amd64\n")
                if "3" in pyvers:
                    dep_ver.write("Python 3: " + python3_ver + "\n")
                    dep_ver.write("Python 3: " + python3_ver + " amd64\n")
                dep_ver.write("zlib: " + zlib_ver + "\n")
                dep_ver.write("bzip2: " + bzip2_ver + "\n")
                dep_ver.write("\n")
                dep_ver.write(vs_versions.read())

    def bootstrap(self):
        subprocess.call("bootstrap.bat", shell=True)

    def build_version(self, arch, vc):
        self.make_user_config(vc)
        lib_dir = "lib" + arch + "-msvc-" + vc

        t = Timer("Build msvc-" + vc + "-" + arch)
        t.start()

        cmd = "b2"
        cmd += " -j%NUMBER_OF_PROCESSORS%"
        cmd += " --without-mpi"
        cmd += " --build-dir=" + self.build_path + "/bin.v2"
        cmd += " --stage-libdir=" + lib_dir
        cmd += " --build-type=complete"
        cmd += " toolset=msvc-" + vc
        cmd += " address-model=" + arch 
        cmd += " architecture=x86"
        cmd += " stage"
        print("Running: " + cmd)
        subprocess.call(cmd, shell=True)

        t.stop()
        t.output(self.times)

        with open(arch + "bitlog.txt", "a") as log:
            log.write(cmd + "\n")

        subprocess.call(cmd + " >> " + arch + "bitlog.txt 2>&1", shell=True)

        #TODO Generate DEPENDENCY_VERSIONS.txt automatically
        shutil.copy("../DEPENDENCY_VERSIONS.txt", "lib" + arch + "-msvc-" + vc + "/DEPENDENCY_VERSIONS.txt")

    def copy_logs(self, arch):
        to_file = os.path.join(self.build_path, self.source + self.archive_suffix + "-" + arch + "bitlog.txt")
        shutil.copy(arch + "bitlog.txt", to_file)
        cmd = "start \"Build Output\" notepad " + to_file
        subprocess.call(cmd, shell=True)

    def midway_cleanup(self):
        garbage_headers = os.path.join(self.source_path, "garbage_headers")
        if os.path.exists(garbage_headers):
            shutil.rmtree(garbage_headers)

        intermediates = self.build_path + "/bin.v2"
        if not self.keep_intermediate and os.path.exists(intermediates):
            shutil.rmtree(intermediates)

        shutil.copy("DEPENDENCY_VERSIONS.txt", os.path.join(self.source_path, "DEPENDENCY_VERSIONS.txt"))

    def make_archive(self):
        archive = self.source + self.archive_suffix + "-bin-msvc-all-32-64.7z"
        subprocess.call(self.zip_cmd + " a " + archive + " " + self.source_path)

    def make_installer_options(self, arch, vc):
        options = {
            'tmp_build_dir': "build-msvc-" + vc + "-" + arch,
            'zip_cmd': self.zip_cmd,
            'source_path': self.source_path,
            'source_archive_output': self.source_archive_output,
            'source': self.source,
            'version': self.source + self.archive_suffix,
            'libs': "lib" + arch + "-msvc-" + vc,
            'config': "msvc-" + vc + "-" + arch,
            'build_path': self.build_path,
            'inno_cmd': self.inno_cmd,
            'compression_threads': self.inno_compression_threads
        }
        return options

    def initialize(self):
        # TODO: Load comamd arguments
        self.make_vars()

    def prepare(self):
        self.prepare_time = Timer("prepare")
        self.prepare_time.start()
        self.check_user_config_exists()
        self.make_dirs()
        os.chdir(self.build_path)
        self.make_source_archive()
        self.make_dep_archives()
        if self.repo == "git":
            self.get_and_extract_archives()
            #self.get_and_extract_archives_threaded()
        else:
            self.get_and_extract_archives_process()
        self.move_source()
        self.set_env_vars()
        self.make_dependency_versions(pyvers=["2", "3"])
        self.setup_lib_check()
        self.prepare_time.stop()
        self.prepare_time.output(self.times)

    def build(self):
        self.build_time = Timer("Build")
        self.build_time.start()
        os.chdir(self.source_path)
        self.bootstrap()
        for vc_arch in vc_archs:
            for vc_ver in vc_versions:
                self.build_version(vc_arch, vc_ver)
            self.copy_logs(vc_arch)

        os.chdir(self.build_path)
        self.midway_cleanup()
        self.start_lib_check()
        self.build_time.stop()
        self.build_time.output(self.times)

    def setup_lib_check(self):
        for version in vc_versions:
            extension = ".vcxproj"
            if version in ["8.0", "9.0"]:  # old .vcproj users
                extension = ".vcproj"
            name, minor = version.split(".")
            if version in ["14.1", "14.2"]:  # Use dotted name for these
                name = version
            proj = "BoostLibraryCheck-VC" + name + extension

            lib32_path = os.path.join(self.source_path, "lib32-msvc-" + version)
            lib64_path = os.path.join(self.source_path, "lib64-msvc-" + version)

            proj_path = os.path.join(self.lib_check_path, proj)

            with open(proj_path, 'r') as f:
                orig = f.read()

            inc = orig.replace("FILL_INC_PATH", self.source_path)
            l32 = inc.replace("FILL_32_LINK_PATH", lib32_path)
            l64 = l32.replace("FILL_64_LINK_PATH", lib64_path)

            with open(proj_path, 'w') as f:
                f.write(l64)

    def start_lib_check(self):
        subprocess.call('start "Library Check" /d ' + self.lib_check_path +
                        ' start_check.bat' , shell=True)

    def package(self):
        self.package_time = Timer("Package")
        self.package_time.start()
        self.make_archive()

        for vc_arch, vc_ver in itertools.product(vc_archs, vc_versions):
            options = self.make_installer_options(vc_arch, vc_ver)
            make_installer(options)
        self.package_time.stop()
        self.package_time.output(self.times)

    def package_parallel(self):
        self.package_time = Timer("Package")
        self.package_time.start()
        self.make_archive()

        pool = multiprocessing.Pool(processes=PACKAGE_PROCESSES)
        self.package_results = []
        for vc_arch, vc_ver in itertools.product(vc_archs, vc_versions):
            options = self.make_installer_options(vc_arch, vc_ver)
            self.package_results.append(
                pool.apply_async(make_installer, (options,)))

        pool.close()
        pool.join()

        self.package_time.stop()
        self.package_time.output(self.times)

    def print_times(self):
        self.prepare_time.output()
        self.build_time.output()
        self.package_time.output()

    def run_build(self):
        self.initialize()
        self.prepare()
        self.build()
        self.package_parallel()

    def run_package(self):
        self.initialize()
        os.chdir(self.build_path)
        self.package_parallel()


if __name__ == "__main__":
    b = Builder()
    b.run_build()
    b.print_times()
