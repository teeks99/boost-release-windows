import argparse
import os
try:
    from urllib.request import urlretrieve
except ImportError: # Python 2
    from urllib import urlretrieve

import repo_paths

supported_extensions = ["7z", "tar.bz2", "tar.gz", "zip"]
repo = "archives"
staging = "staging"


parser = argparse.ArgumentParser()
parser.add_argument("-v", "--version", required=True)
parser.add_argument("-m", "--minor-version", default="0")
parser.add_argument("-b", "--beta")
parser.add_argument("-r", "--rc")
parser.add_argument("-s", "--snapshot")
parser.add_argument("--dont-remove-rc", action="store_false", dest="remove_rc")

args = parser.parse_args()

release_type = ""
archive_dir = f"1.{args.version}.{args.minor_version}"
if args.beta:
    if args.rc:
        release_type = "beta-rc"
        archive_dir += f"_b{args.beta}_rc{args.rc}"
    else:
        release_type = "beta"
        archive_dir += f"_b{args.beta}"
elif args.snapshot:
    release_type = "snapshot"
    archive_dir += f"_snapshot"
elif args.rc:
    release_type = "rc"
    archive_dir += f"_rc{args.rc}"
else:
    release_type = "release"


lib_path = os.path.join(staging, "lib", archive_dir)
bin_path = os.path.join(staging, "bin", archive_dir)
if not os.path.exists(lib_path):
    print(f"Making Dir: {lib_path}")
    os.makedirs(lib_path)
if not os.path.exists(bin_path):
    print(f"Making Dir: {bin_path}")
    os.makedirs(bin_path)

config = repo_paths.REPOS[repo][release_type]
replace = {"version": args.version, "minor_version": args.minor_version}
if args.beta:
    replace["beta"] = args.beta
if args.rc:
    replace["rc"] = args.rc
replace["archive_suffix"] = config["archive_suffix"].format(**replace)

for extension in supported_extensions:
    replace["file_extension"] = extension

    url_root = config["url"].format(**replace)
    server_file = config["file"].format(**replace)

    url = url_root + server_file
    local_file = server_file

    if args.remove_rc and args.rc:
        local_file = local_file.replace(f"_rc{args.rc}", "")

    local_path = os.path.join(lib_path, local_file)

    print(f"Downloading:\n\t{url}\nTo:\n\t{local_path}")
    try:
        urlretrieve(url, local_path)
    except:
        print("Error Downloading: " + url)
        raise