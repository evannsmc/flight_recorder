// C++ writer micro-benchmark: same scenario as bench_logging.py (ticks x 20 float64, a 100x10 plan every 50 ticks).
//   cpp_bench <out.h5> [ticks]
#include <flight_recorder/recorder.hpp>

#include <algorithm>
#include <chrono>
#include <cstdio>
#include <random>
#include <string>
#include <vector>

int main(int argc, char** argv) {
  const std::string path = argc > 1 ? argv[1] : "/tmp/cpp_bench.h5";
  const size_t ticks = argc > 2 ? std::stoul(argv[2]) : 30000;
  std::mt19937_64 rng(0);
  std::normal_distribution<double> nd;
  std::vector<double> rows(ticks * 20), plan(1000);
  for (auto& v : rows) v = nd(rng);

  std::vector<std::string> cols{"time"};
  for (int i = 1; i < 20; ++i) cols.push_back("c" + std::to_string(i));
  fr::Recorder rec(path);
  auto& s = rec.stream("ticks", cols, ticks);

  using clk = std::chrono::steady_clock;
  std::vector<double> dt(ticks);
  for (size_t k = 0; k < ticks; ++k) {
    if (k % 50 == 0)
      for (auto& v : plan) v = nd(rng);
    const auto t0 = clk::now();
    s.append_row(&rows[20 * k], 20);
    if (k % 50 == 0) rec.record("plans", static_cast<int64_t>(k / 50), {{"plan", fr::Array(plan.data(), {100, 10})}});
    dt[k] = std::chrono::duration<double, std::micro>(clk::now() - t0).count();
  }
  const auto t0 = clk::now();
  rec.save();
  const double save_ms = std::chrono::duration<double, std::milli>(clk::now() - t0).count();
  std::vector<double> sorted = dt;
  std::sort(sorted.begin(), sorted.end());
  std::printf("{\"append_us_p50\": %.3f, \"append_us_p99\": %.3f, \"save_ms\": %.1f}\n", sorted[ticks / 2],
              sorted[ticks * 99 / 100], save_ms);
  return 0;
}
