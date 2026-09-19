"""Import the Boost.Python smoke extension and exercise it."""
import sys

sys.path.insert(0, sys.argv[1])

import boost_smoke_ext  # noqa: E402  (path has to be set up first)

greeting = boost_smoke_ext.greet()
assert greeting == "boost.python works", greeting
total = boost_smoke_ext.add(2, 40)
assert total == 42, total
print("boost.python extension ok on {}".format(sys.version.split()[0]))
