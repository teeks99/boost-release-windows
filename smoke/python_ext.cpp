// A minimal Boost.Python extension module.
//
// Building and importing this is the only way to tell that boost_python was
// built against the right interpreter: a library that merely exists in the
// staged directory can still be unusable.

#include <boost/python.hpp>

#include <string>

namespace {

std::string greet() {
    return "boost.python works";
}

int add(int left, int right) {
    return left + right;
}

}  // namespace

BOOST_PYTHON_MODULE(boost_smoke_ext) {
    boost::python::def("greet", greet);
    boost::python::def("add", add);
}
