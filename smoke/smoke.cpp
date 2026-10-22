// Boost binary smoke test.
//
// Built once per build configuration against the libraries that
// configuration just staged.  Nothing here names a library file: every
// library is pulled in by Boost's auto-linking, so a successful link is
// itself the check that the staged files are named the way Boost's headers
// expect for this compiler, architecture, variant, link and runtime-link.
// Compile with BOOST_LIB_DIAGNOSTIC to have the compiler report every name
// it asked for.
//
// Kept to C++14 so the same source builds with every supported toolset.

#include <boost/version.hpp>
#include <boost/config.hpp>

#include <cstdlib>
#include <ctime>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

#include <boost/atomic.hpp>
#include <boost/chrono.hpp>
#include <boost/container/flat_map.hpp>
#include <boost/date_time/posix_time/posix_time.hpp>
#include <boost/filesystem.hpp>
#include <boost/program_options.hpp>
#include <boost/random/mersenne_twister.hpp>
#include <boost/random/uniform_int_distribution.hpp>
#include <boost/regex.hpp>
#include <boost/system/error_code.hpp>
#include <boost/thread.hpp>
#include <boost/timer/timer.hpp>

#include <boost/archive/text_iarchive.hpp>
#include <boost/archive/text_oarchive.hpp>
#include <boost/archive/text_woarchive.hpp>
#include <boost/serialization/vector.hpp>

#include <boost/iostreams/copy.hpp>
#include <boost/iostreams/device/back_inserter.hpp>
#include <boost/iostreams/filter/bzip2.hpp>
#include <boost/iostreams/filter/zlib.hpp>
#include <boost/iostreams/filtering_stream.hpp>

#if !defined(BOOST_SMOKE_NO_CONTEXT)
#include <boost/context/fiber.hpp>
#endif

#if !defined(BOOST_SMOKE_NO_JSON) && BOOST_VERSION >= 107500
#include <boost/json.hpp>
#define BOOST_SMOKE_JSON
#endif

#if !defined(BOOST_SMOKE_NO_URL) && BOOST_VERSION >= 108100
#include <boost/url.hpp>
#define BOOST_SMOKE_URL
#endif

namespace {

int failures = 0;
int checks = 0;

void report(const char* name, bool ok, const std::string& detail) {
    ++checks;
    if (!ok) {
        ++failures;
    }
    std::cout << (ok ? "  ok   " : "  FAIL ") << name;
    if (!detail.empty()) {
        std::cout << "  (" << detail << ")";
    }
    std::cout << std::endl;
}

#define SMOKE(name, expr)                                                     \
    do {                                                                      \
        std::string detail;                                                   \
        bool ok = false;                                                      \
        try {                                                                 \
            ok = (expr);                                                      \
        } catch (const std::exception& e) {                                   \
            detail = std::string("threw: ") + e.what();                       \
        } catch (...) {                                                       \
            detail = "threw an unknown exception";                            \
        }                                                                     \
        report(name, ok, detail);                                             \
    } while (false)

bool check_system() {
    boost::system::error_code ec =
        boost::system::errc::make_error_code(boost::system::errc::io_error);
    return ec.value() != 0 && !ec.message().empty();
}

bool check_filesystem() {
    namespace fs = boost::filesystem;
    std::ostringstream name;
    name << "boost-smoke-" << static_cast<unsigned long>(std::time(BOOST_NULLPTR))
         << "-" << std::rand() << ".txt";
    fs::path file = fs::temp_directory_path() / name.str();
    {
        std::ofstream out(file.string().c_str());
        out << "boost" << std::endl;
    }
    bool ok = fs::exists(file) && fs::file_size(file) > 0;
    fs::remove(file);
    return ok && !fs::exists(file);
}

bool check_thread_and_atomic() {
    boost::atomic<int> counter(0);
    boost::thread_group group;
    for (int i = 0; i < 4; ++i) {
        group.create_thread([&counter]() {
            for (int j = 0; j < 1000; ++j) {
                counter.fetch_add(1);
            }
            boost::this_thread::sleep_for(boost::chrono::milliseconds(1));
        });
    }
    group.join_all();
    return counter.load() == 4000;
}

bool check_chrono() {
    boost::chrono::steady_clock::time_point start =
        boost::chrono::steady_clock::now();
    boost::this_thread::sleep_for(boost::chrono::milliseconds(5));
    boost::chrono::nanoseconds elapsed =
        boost::chrono::steady_clock::now() - start;
    return elapsed.count() > 0;
}

bool check_date_time() {
    boost::posix_time::ptime when(
        boost::gregorian::date(2026, 9, 19),
        boost::posix_time::hours(13) + boost::posix_time::minutes(45));
    return boost::posix_time::to_simple_string(when) ==
           "2026-Sep-19 13:45:00";
}

bool check_regex() {
    boost::regex pattern("boost_([a-z_]+)-vc(\\d+)-mt");
    boost::smatch match;
    std::string subject = "libboost_filesystem-vc143-mt-gd-x64-1_92.lib";
    return boost::regex_search(subject, match, pattern) &&
           match[1] == "filesystem";
}

bool check_program_options() {
    namespace po = boost::program_options;
    po::options_description options("smoke");
    options.add_options()("count", po::value<int>()->default_value(0), "count");
    const char* argv[] = {"smoke", "--count", "42"};
    po::variables_map values;
    po::store(po::parse_command_line(3, const_cast<char**>(argv), options),
              values);
    po::notify(values);
    return values["count"].as<int>() == 42;
}

bool check_serialization() {
    std::vector<int> original;
    for (int i = 0; i < 10; ++i) {
        original.push_back(i * i);
    }
    std::stringstream stream;
    {
        boost::archive::text_oarchive archive(stream);
        archive << original;
    }
    std::vector<int> restored;
    {
        boost::archive::text_iarchive archive(stream);
        archive >> restored;
    }
    return restored == original;
}

bool check_wserialization() {
    std::wstringstream stream;
    std::vector<int> original(3, 7);
    {
        boost::archive::text_woarchive archive(stream);
        archive << original;
    }
    return !stream.str().empty();
}

// Round-trips through a compressor and back, which only links (and only
// works) if Boost.Iostreams was built with the bundled zlib and bzip2
// sources the release is supposed to use.
template <typename Compressor, typename Decompressor>
bool check_iostreams_filter() {
    namespace io = boost::iostreams;
    std::string plain;
    for (int i = 0; i < 200; ++i) {
        plain += "the quick brown fox jumps over the lazy dog ";
    }

    std::string squeezed;
    {
        io::filtering_ostream out;
        out.push(Compressor());
        out.push(io::back_inserter(squeezed));
        out.write(plain.data(), static_cast<std::streamsize>(plain.size()));
        io::close(out);
    }

    std::string restored;
    {
        io::filtering_ostream out;
        out.push(Decompressor());
        out.push(io::back_inserter(restored));
        out.write(squeezed.data(),
                  static_cast<std::streamsize>(squeezed.size()));
        io::close(out);
    }
    return !squeezed.empty() && squeezed.size() < plain.size() &&
           restored == plain;
}

bool check_random() {
    boost::random::mt19937 engine(2026u);
    boost::random::uniform_int_distribution<> dice(1, 6);
    int total = 0;
    for (int i = 0; i < 100; ++i) {
        int roll = dice(engine);
        if (roll < 1 || roll > 6) {
            return false;
        }
        total += roll;
    }
    return total >= 100 && total <= 600;
}

bool check_timer() {
    boost::timer::cpu_timer timer;
    volatile double value = 0.0;
    for (int i = 1; i < 200000; ++i) {
        value += 1.0 / i;
    }
    timer.stop();
    return !timer.format().empty();
}

bool check_container() {
    boost::container::flat_map<std::string, int> map;
    map["boost"] = BOOST_VERSION;
    map["smoke"] = 1;
    return map.find("boost") != map.end() &&
           map["boost"] == BOOST_VERSION && map.size() == 2;
}

#if !defined(BOOST_SMOKE_NO_CONTEXT)
bool check_context() {
    int hops = 0;
    boost::context::fiber other(
        [&hops](boost::context::fiber&& caller) {
            ++hops;
            caller = std::move(caller).resume();
            ++hops;
            return std::move(caller);
        });
    other = std::move(other).resume();
    other = std::move(other).resume();
    return hops == 2;
}
#endif

#if defined(BOOST_SMOKE_JSON)
bool check_json() {
    boost::json::value parsed = boost::json::parse(
        R"({"library":"boost","version":)" +
        std::to_string(BOOST_VERSION) + "}");
    return parsed.at("library").as_string() == "boost";
}
#endif

#if defined(BOOST_SMOKE_URL)
bool check_url() {
    boost::urls::url_view view("https://www.boost.org/users/download/?x=1");
    return view.host() == "www.boost.org" && view.path() == "/users/download/";
}
#endif

}  // namespace

int main() {
    std::cout << "Boost " << (BOOST_VERSION / 100000) << "."
              << (BOOST_VERSION / 100 % 1000) << "." << (BOOST_VERSION % 100)
              << "  compiler " << BOOST_COMPILER << "  stdlib "
              << BOOST_STDLIB << std::endl;
#if defined(BOOST_ALL_DYN_LINK)
    std::cout << "linking: shared" << std::endl;
#else
    std::cout << "linking: static" << std::endl;
#endif
#if defined(_DLL)
    std::cout << "runtime: shared" << std::endl;
#else
    std::cout << "runtime: static" << std::endl;
#endif

    SMOKE("system", check_system());
    SMOKE("filesystem", check_filesystem());
    SMOKE("thread+atomic", check_thread_and_atomic());
    SMOKE("chrono", check_chrono());
    SMOKE("date_time", check_date_time());
    SMOKE("regex", check_regex());
    SMOKE("program_options", check_program_options());
    SMOKE("serialization", check_serialization());
    SMOKE("wserialization", check_wserialization());
    SMOKE("iostreams+zlib",
          (check_iostreams_filter<boost::iostreams::zlib_compressor,
                                  boost::iostreams::zlib_decompressor>()));
    SMOKE("iostreams+bzip2",
          (check_iostreams_filter<boost::iostreams::bzip2_compressor,
                                  boost::iostreams::bzip2_decompressor>()));
    SMOKE("random", check_random());
    SMOKE("timer", check_timer());
    SMOKE("container", check_container());
#if !defined(BOOST_SMOKE_NO_CONTEXT)
    SMOKE("context", check_context());
#endif
#if defined(BOOST_SMOKE_JSON)
    SMOKE("json", check_json());
#endif
#if defined(BOOST_SMOKE_URL)
    SMOKE("url", check_url());
#endif

    std::cout << (checks - failures) << "/" << checks << " checks passed"
              << std::endl;
    return failures == 0 ? EXIT_SUCCESS : EXIT_FAILURE;
}
