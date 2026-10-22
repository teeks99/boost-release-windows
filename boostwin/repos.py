"""Where to fetch the Boost source archive from, per repo and release type.

The templates are filled in with ``version``, ``minor_version``, ``beta``,
``rc``, ``file_extension`` and the derived ``archive_suffix``.
"""

JFROG_BOOST = "https://boostorg.jfrog.io/artifactory/main/"
ARCHIVES_BOOST = "https://archives.boost.io/"


def _http_repo(base):
    return {
        "master-snapshot": {
            "url": base + "master/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "-snapshot",
        },
        "beta-rc": {
            "url": base + "beta/1.{version}.{minor_version}.beta{beta}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}_rc{rc}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}",
        },
        "beta": {
            "url": base + "beta/1.{version}.{minor_version}.beta{beta}/source/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "_b{beta}",
        },
        "rc": {
            "url": base + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}_rc{rc}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "",
        },
        "release": {
            "url": base + "release/1.{version}.{minor_version}/source/",
            "file": "boost_1_{version}_{minor_version}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "",
        },
        "snapshot": {  # alias used by stage_release.py
            "url": base + "master/",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "-snapshot",
        },
    }


REPOS = {
    "jfrog": _http_repo(JFROG_BOOST),
    "archives": _http_repo(ARCHIVES_BOOST),
    "local": {
        "b1": {
            "url": "none",
            "file": "boost_1_{version}_{minor_version}{archive_suffix}.{file_extension}",
            "source_archive_output": "boost_1_{version}_{minor_version}",
            "archive_suffix": "",
        },
    },
    "git": {
        "develop": {
            "url": "https://github.com/boostorg/boost",
            "branch": "develop",
            "source_archive_output": "boost",
            "archive_suffix": "",
            "file": "",
        },
        "master": {
            "url": "https://github.com/boostorg/boost",
            "branch": "master",
            "source_archive_output": "boost",
            "archive_suffix": "",
            "file": "",
        },
    },
}
