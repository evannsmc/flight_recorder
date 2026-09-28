// C++ writer example (also the fixture of test/test_cpp_roundtrip.py).
//
//   cpp_example <out.h5> [rows] [--concurrent-flush]
//
// Writes a 100 Hz "ticks" stream with known values (x = sin(t), y = cos(t), z = -t, yaw = row), a "plans" record
// every 50 rows, and an event every 1000 rows. With --concurrent-flush a second thread flushes every 2 ms while the
// main thread appends, exercising the lock-free append / concurrent flush design.
#include <flight_recorder/recorder.hpp>

#include <atomic>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <thread>

int main(int argc, char** argv) {
  if (argc < 2) {
    std::fprintf(stderr, "usage: %s <out.h5> [rows] [--concurrent-flush]\n", argv[0]);
    return 2;
  }
  const std::string path = argv[1];
  const long rows = argc > 2 ? std::stol(argv[2]) : 10000;
  const bool concurrent = argc > 3 && std::strcmp(argv[3], "--concurrent-flush") == 0;

  fr::Recorder rec(path, {{"robot", std::string("example")}, {"control_rate_hz", 100.0}, {"gains", std::vector<double>{1, 2, 3}}});
  auto& ticks = rec.stream("ticks", {"time", "x", "y", "z", "yaw"}, 1024);  // small capacity: exercises block growth

  std::atomic<bool> done{false};
  std::thread flusher;
  if (concurrent) {
    flusher = std::thread([&] {
      while (!done.load()) {
        rec.flush();
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
      }
    });
  }

  for (long i = 0; i < rows; ++i) {
    const double t = 0.01 * static_cast<double>(i);
    ticks.append(t, std::sin(t), std::cos(t), -t, static_cast<double>(i));
    if (i % 50 == 0) {
      std::vector<double> tube(2 * 10);
      for (size_t k = 0; k < tube.size(); ++k) tube[k] = static_cast<double>(i) + 0.1 * static_cast<double>(k);
      rec.record("plans", static_cast<int64_t>(i / 50), {{"tube", fr::Array(tube, {2, 10})}},
                 {{"t_start", t}, {"seq", static_cast<int64_t>(i / 50)}, {"note", std::string("plan")}});
    }
    if (i % 1000 == 999) rec.event(t, "checkpoint", "row " + std::to_string(i));
  }
  done.store(true);
  if (flusher.joinable()) flusher.join();
  std::printf("%s\n", rec.save().c_str());
  return 0;
}
