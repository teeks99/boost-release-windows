import os
import json
import subprocess
from pathlib import Path

pkg_type="release"
#pkg_type="beta"
beta="1"
ver_major="76"
ver_minor="0"
artifactory_server = "https://boostorg.jfrog.io/artifactory"
staging_path = "/main/staging"
key_path = os.path.expanduser("~") + "/.jfrog/BoostArtifactoryAPI_Key.txt"

class Upload:
    def __init__(self):
        self.api_key = ""
        with open(key_path, "r") as f:
            self.api_key = f.read().strip()

    def run(self):
        dir_name = "1." + ver_major + "." + ver_minor
        if pkg_type == "beta":
            dir_name += "_b" + beta

        os.chdir(os.path.join("bin", dir_name))

        files = os.listdir()

        dotted_name = "1." + ver_major + "." + ver_minor
        if pkg_type == "beta":
            dotted_name += ".beta" + beta
        self.art_path = artifactory_server + staging_path + "/" + dotted_name
        self.art_path += "/binaries/"

        print("Will upload files:")
        for file_name in files:
            self.upload_file(file_name, dry_run=True)
        input("If this is correct, press Enter to continue...")

        for file_name in files:
            print("Starting:")
            result_bytes = self.upload_file(file_name)
            result = json.loads(result_bytes.decode())
            if not "created" in result:
                raise Exception("Could not upload: " + file_name +
                                ". Message: " + result)

    def upload_file(self, file_name, dry_run=False):
        cmd = 'curl --progress-bar'
        cmd += f' -T {file_name}'
        cmd += f' -X PUT "{self.art_path}"'
        cmd += f' -H "X-JFrog-Art-Api:'

        print(cmd + 'API_KEY_HIDDEN"')
        cmd += f'{self.api_key}"'
        if not dry_run:
            completed = subprocess.run(cmd, stdout=subprocess.PIPE,
                                       shell=True)
            return completed.stdout

if __name__ == "__main__":
    u = Upload()
    u.run()
