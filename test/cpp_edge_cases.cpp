// Edge cases of the C++ writer, checked from test/test_cpp_edge_cases.py.
// Usage: cpp_edge_cases <output_dir>. Exits non-zero (with a message) on the first failed check.
#include <flight_recorder/recorder.hpp>

#include <cstdio>
#include <cstdlib>
#include <string>

#define CHECK(cond)                                                     \
  do {                                                                  \
    if (!(cond)) {                                                      \
      std::fprintf(stderr, "CHECK failed line %d: %s\n", __LINE__, #cond); \
      return 1;                                                         \
    }                                                                   \
  } while (0)

template <typename F>
bool throws(F&& f) {
  try {
    f();
  } catch (const std::exception&) {
    return true;
  }
  return false;
}

static herr_t my_handler(hid_t, void*) { return 0; }

int main(int argc, char** argv) {
  if (argc < 2) return 2;
  const std::string dir = argv[1];

  // 1. extension policy: HDF5 extensions kept, anything else gets .h5 appended
  CHECK(fr::detail::h5_path("a/run.hdf5") == "a/run.hdf5");
  CHECK(fr::detail::h5_path("a/run.H5") == "a/run.H5");
  CHECK(fr::detail::h5_path("a/run") == "a/run.h5");
  CHECK(fr::detail::h5_path("a.b/run") == "a.b/run.h5");
  CHECK(fr::detail::h5_path("a/run.csv") == "a/run.csv.h5");

  // 2. flush() must not leave HDF5 error printing disabled for the rest of the process
  H5Eset_auto2(H5E_DEFAULT, my_handler, nullptr);
  {
    fr::Recorder rec(dir + "/errors.hdf5");
    rec.stream("s", {"a"}).append(1.0);
    CHECK(rec.save() == dir + "/errors.hdf5");
  }
  H5E_auto2_t func = nullptr;
  void* data = nullptr;
  H5Eget_auto2(H5E_DEFAULT, &func, &data);
  CHECK(func == my_handler);

  // 3. the destructor flushes when save() is forgotten
  {
    fr::Recorder rec(dir + "/forgot");  // -> forgot.h5
    auto& s = rec.stream("ticks", {"t", "x"});
    for (int i = 0; i < 1000; ++i) s.append(0.01 * i, 2.0 * i);
    rec.event(1.0, "note", "written by the destructor");
  }

  // 4. scalar arrays; 5. size/shape mismatch rejected; 6. array/attribute name clash rejected
  {
    fr::Recorder rec(dir + "/records.h5");
    rec.record("gains", 1, {{"k", fr::Array(std::vector<double>{2.5}, {})}, {"K", fr::Array(std::vector<double>{1, 2, 3, 4}, {2, 2})}},
               {{"note", std::string("scalar + matrix")}});
    CHECK(throws([] { fr::Array(std::vector<double>{1, 2, 3}, {2, 2}); }));
    CHECK(throws([&] { rec.record("g", 1, {{"t", fr::Array(std::vector<double>{1}, {1})}}, {{"t", 1.0}}); }));
    rec.save();
    // records are released once written: a second flush must not duplicate or fail
    rec.record("gains", 2, {{"k", fr::Array(std::vector<double>{3.5}, {})}});
    rec.save();
  }
  std::printf("ok\n");
  return 0;
}
