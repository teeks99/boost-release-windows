
jfrog_boost = "https://boostorg.jfrog.io/artifactory/main/"
archives_boost = "https://archives.boost.io/"
REPOS = {
    "jfrog": {
        "master-snapshot": {
            "url": jfrog_boost + "master/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "-snapshot",
        },
        "beta-rc": {
            "url": jfrog_boost + "beta/1.{version}.{minor_version}.beta{beta}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}_rc{rc}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}"
        },
        "beta": {
            "url": jfrog_boost + "beta/1.{version}.{minor_version}.beta{beta}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}"
        },
        "rc": {
            "url": jfrog_boost + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}_rc{rc}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": ""
        },
        "release": {
            "url": jfrog_boost + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": ""
        }
    },
    "archives": {
        "master-snapshot": {
            "url": archives_boost + "master/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "-snapshot",
        },
        "beta-rc": {
            "url": archives_boost + "beta/1.{version}.{minor_version}.beta{beta}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}_rc{rc}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}"
        },
        "beta": {
            "url": archives_boost + "beta/1.{version}.{minor_version}.beta{beta}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}"
        },
        "rc": {
            "url": archives_boost + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}_rc{rc}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": ""
        },
        "release": {
            "url": archives_boost + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": ""
        }
    },
    "local": {
        "b1": {
            "url": "none",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
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