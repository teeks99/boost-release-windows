"""Import the Boost.Python smoke extension and exercise it.

    python_ext_test.py <directory holding the .pyd> [<directory holding DLLs> ...]

Python 3.8 and later do not use PATH to resolve an extension module's DLL
dependencies, so the directory holding boost_python's DLL has to be added
explicitly -- which is also what a real user of these binaries has to do.
"""
import os
import sys

module_dir = sys.argv[1]
sys.path.insert(0, module_dir)

for dll_dir in sys.argv[2:]:
    if hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
        os.add_dll_directory(dll_dir)

import boost_smoke_ext  # noqa: E402  (the search paths have to be set up first)

greeting = boost_smoke_ext.greet()
assert greeting == "boost.python works", greeting
total = boost_smoke_ext.add(2, 40)
assert total == 42, total
print("boost.python extension ok on {}".format(sys.version.split()[0]))
