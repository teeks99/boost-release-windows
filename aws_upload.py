import os
import boto3
from botocore.exceptions import ClientError
from pathlib import Path

#pkg_type="release"
pkg_type="beta"
beta="1"
ver_major="86"
ver_minor="0"
s3_bucket = "boost-archives"
staging_path = "staging"
class Upload:
    def __init__(self):
        self.s3 = boto3.client('s3')

    def run(self):
        dir_name = f"1.{ver_major}.{ver_minor}"
        if pkg_type == "beta":
            dir_name += f"_b{beta}"

        os.chdir(os.path.join("bin", dir_name))

        files = os.listdir()

        dotted_name = f"1.{ver_major}.{ver_minor}"
        if pkg_type == "beta":
            dotted_name += f".beta{beta}"
        self.server_path = f"{staging_path}/{dotted_name}/binaries/"

        print("Will upload files:")
        for file_name in files:
            self.upload_file(file_name, dry_run=True)
        input("If this is correct, press Enter to continue...")

        for file_name in files:
            result = self.upload_file(file_name)
            if not result:
                raise Exception("Could not upload: " + file_name +
                                ". Message: " + result)

    def upload_file(self, file_name, dry_run=False):
        object_name = self.server_path + file_name

        try:
            print(f"Uploading {file_name} to {s3_bucket} / {object_name}")
            if not dry_run:
                response = self.s3.upload_file(
                    file_name, s3_bucket, object_name)
        except ClientError as e:
            print(e)
            return False
        return True

if __name__ == "__main__":
    u = Upload()
    u.run()
