import os
import subprocess
import tarfile
from urllib.request import urlretrieve

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
