// flight_recorder (C++ writer): same HDF5 layout as the Python Recorder (flight_recorder/format.py), readable with
// flight_recorder.FlightLog. Header-only; needs the HDF5 C library (Ubuntu: libhdf5-dev).
//
//   fr::Recorder rec("flight.h5", {{"robot", "skie_2"}});
//   auto& ticks = rec.stream("ticks", {"time", "x", "y", "z"});   // at init
//   ticks.append(t, x, y, z);                                      // hot path: no allocation (except 1 block / 4096 rows)
//   rec.record("plans", 12, {{"tube", fr::Array(tube_data, {rows, 10})}}, {{"t_start", 3.2}});
//   rec.event(t, "backup", "no certified plan");
//   rec.flush();                                                   // optional, e.g. from a low-priority timer
//   rec.save();                                                    // final flush
//
// Threading: each Stream has ONE writer thread; flush()/save() may run on another thread concurrently. Rows live in
// fixed-size blocks that are never moved or freed while the recorder exists, and the row count is published with
// release/acquire ordering, so a concurrent flush only ever reads complete rows. record()/event() take a mutex.
#pragma once

#include <hdf5.h>

#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <ctime>
#include <initializer_list>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>
#include <variant>
#include <vector>

namespace fr {

constexpr const char* kFormatName = "flight_recorder";
constexpr int64_t kFormatVersion = 1;
constexpr hsize_t kChunkRows = 4096;
constexpr unsigned kGzipLevel = 4;

// A metadata / record attribute value
using Value = std::variant<double, int64_t, std::string, std::vector<double>>;
using Attributes = std::vector<std::pair<std::string, Value>>;

// An N-d float64 array to store as a record (row-major); owns a copy of the data
struct Array {
  std::vector<double> data;
  std::vector<hsize_t> shape;
  Array() = default;
  Array(const double* values, std::vector<hsize_t> dims) : shape(std::move(dims)) {
    hsize_t n = 1;
    for (hsize_t d : shape) n *= d;
    data.assign(values, values + n);
  }
  Array(std::vector<double> values, std::vector<hsize_t> dims) : data(std::move(values)), shape(std::move(dims)) {}
};

namespace detail {

inline void check(herr_t status, const char* what) {
  if (status < 0) throw std::runtime_error(std::string("flight_recorder: HDF5 call failed: ") + what);
}
inline hid_t check_id(hid_t id, const char* what) {
  if (id < 0) throw std::runtime_error(std::string("flight_recorder: HDF5 call failed: ") + what);
  return id;
}

// RAII for HDF5 identifiers
class Id {
 public:
  Id() = default;
  Id(hid_t id, herr_t (*closer)(hid_t)) : id_(id), closer_(closer) {}
  Id(const Id&) = delete;
  Id& operator=(const Id&) = delete;
  Id(Id&& o) noexcept : id_(o.id_), closer_(o.closer_) { o.id_ = -1; }
  Id& operator=(Id&& o) noexcept {
    if (this != &o) {
      if (id_ >= 0 && closer_) closer_(id_);
      id_ = o.id_;
      closer_ = o.closer_;
      o.id_ = -1;
    }
    return *this;
  }
  ~Id() {
    if (id_ >= 0 && closer_) closer_(id_);
  }
  operator hid_t() const { return id_; }  // NOLINT

 private:
  hid_t id_{-1};
  herr_t (*closer_)(hid_t){nullptr};
};

inline Id group(hid_t loc, const std::string& name) {
  if (H5Lexists(loc, name.c_str(), H5P_DEFAULT) > 0) return Id(check_id(H5Gopen2(loc, name.c_str(), H5P_DEFAULT), "H5Gopen2"), H5Gclose);
  return Id(check_id(H5Gcreate2(loc, name.c_str(), H5P_DEFAULT, H5P_DEFAULT, H5P_DEFAULT), "H5Gcreate2"), H5Gclose);
}

inline Id vlen_string_type() {
  Id t(H5Tcopy(H5T_C_S1), H5Tclose);
  check(H5Tset_size(t, H5T_VARIABLE), "H5Tset_size");
  check(H5Tset_cset(t, H5T_CSET_UTF8), "H5Tset_cset");
  return t;
}

inline void write_attr(hid_t obj, const std::string& name, const Value& value) {
  if (H5Aexists(obj, name.c_str()) > 0) check(H5Adelete(obj, name.c_str()), "H5Adelete");
  if (const auto* d = std::get_if<double>(&value)) {
    Id space(H5Screate(H5S_SCALAR), H5Sclose);
    Id a(check_id(H5Acreate2(obj, name.c_str(), H5T_NATIVE_DOUBLE, space, H5P_DEFAULT, H5P_DEFAULT), "H5Acreate2"), H5Aclose);
    check(H5Awrite(a, H5T_NATIVE_DOUBLE, d), "H5Awrite");
  } else if (const auto* i = std::get_if<int64_t>(&value)) {
    Id space(H5Screate(H5S_SCALAR), H5Sclose);
    Id a(check_id(H5Acreate2(obj, name.c_str(), H5T_NATIVE_INT64, space, H5P_DEFAULT, H5P_DEFAULT), "H5Acreate2"), H5Aclose);
    check(H5Awrite(a, H5T_NATIVE_INT64, i), "H5Awrite");
  } else if (const auto* s = std::get_if<std::string>(&value)) {
    Id type = vlen_string_type();
    Id space(H5Screate(H5S_SCALAR), H5Sclose);
    Id a(check_id(H5Acreate2(obj, name.c_str(), type, space, H5P_DEFAULT, H5P_DEFAULT), "H5Acreate2"), H5Aclose);
    const char* c = s->c_str();
    check(H5Awrite(a, type, &c), "H5Awrite");
  } else {
    const auto& v = std::get<std::vector<double>>(value);
    const hsize_t n = v.size();
    Id space(H5Screate_simple(1, &n, nullptr), H5Sclose);
    Id a(check_id(H5Acreate2(obj, name.c_str(), H5T_NATIVE_DOUBLE, space, H5P_DEFAULT, H5P_DEFAULT), "H5Acreate2"), H5Aclose);
    check(H5Awrite(a, H5T_NATIVE_DOUBLE, v.data()), "H5Awrite");
  }
}

inline void write_string_array_attr(hid_t obj, const std::string& name, const std::vector<std::string>& values) {
  if (H5Aexists(obj, name.c_str()) > 0) check(H5Adelete(obj, name.c_str()), "H5Adelete");
  std::vector<const char*> ptrs;
  for (const auto& s : values) ptrs.push_back(s.c_str());
  Id type = vlen_string_type();
  const hsize_t n = values.size();
  Id space(H5Screate_simple(1, &n, nullptr), H5Sclose);
  Id a(check_id(H5Acreate2(obj, name.c_str(), type, space, H5P_DEFAULT, H5P_DEFAULT), "H5Acreate2"), H5Aclose);
  check(H5Awrite(a, type, ptrs.data()), "H5Awrite");
}

// Resizable 1-d dataset (created on first use), appended to at `offset`
inline void append_1d(hid_t loc, const std::string& name, hid_t type, hsize_t chunk, bool compress, hsize_t offset,
                      hsize_t count, const void* data) {
  Id ds;
  if (H5Lexists(loc, name.c_str(), H5P_DEFAULT) > 0) {
    ds = Id(check_id(H5Dopen2(loc, name.c_str(), H5P_DEFAULT), "H5Dopen2"), H5Dclose);
  } else {
    const hsize_t zero = 0, unlimited = H5S_UNLIMITED;
    Id space(H5Screate_simple(1, &zero, &unlimited), H5Sclose);
    Id dcpl(H5Pcreate(H5P_DATASET_CREATE), H5Pclose);
    check(H5Pset_chunk(dcpl, 1, &chunk), "H5Pset_chunk");
    if (compress) {
      check(H5Pset_shuffle(dcpl), "H5Pset_shuffle");
      check(H5Pset_deflate(dcpl, kGzipLevel), "H5Pset_deflate");
    }
    ds = Id(check_id(H5Dcreate2(loc, name.c_str(), type, space, H5P_DEFAULT, dcpl, H5P_DEFAULT), "H5Dcreate2"), H5Dclose);
  }
  const hsize_t total = offset + count;
  check(H5Dset_extent(ds, &total), "H5Dset_extent");
  if (count == 0) return;
  Id file_space(H5Dget_space(ds), H5Sclose);
  check(H5Sselect_hyperslab(file_space, H5S_SELECT_SET, &offset, nullptr, &count, nullptr), "H5Sselect_hyperslab");
  Id mem_space(H5Screate_simple(1, &count, nullptr), H5Sclose);
  check(H5Dwrite(ds, type, mem_space, file_space, H5P_DEFAULT, data), "H5Dwrite");
}

inline std::string iso_now() {
  const std::time_t t = std::time(nullptr);
  char buf[32];
  std::strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%S", std::localtime(&t));
  return buf;
}

inline std::string hostname() {
  char buf[256] = {0};
  gethostname(buf, sizeof(buf) - 1);
  return buf;
}

}  // namespace detail

// A table with fixed float64 columns. One writer thread; see the threading note at the top.
class Stream {
 public:
  static constexpr size_t kBlockRows = 4096;

  Stream(std::string name, std::vector<std::string> columns, size_t capacity)
      : name_(std::move(name)), columns_(std::move(columns)), width_(columns_.size()) {
    if (width_ == 0) throw std::invalid_argument("flight_recorder: a stream needs at least one column");
    // Preallocate enough blocks for `capacity` rows, so the hot path never allocates within that capacity
    for (size_t r = 0; r < capacity; r += kBlockRows) ensure_block(r / kBlockRows);
  }

  const std::string& name() const { return name_; }
  const std::vector<std::string>& columns() const { return columns_; }
  size_t size() const { return n_.load(std::memory_order_acquire); }

  // Hot path: ticks.append(t, x, y, z). The number of values must equal the number of columns.
  template <typename... Ts>
  void append(Ts... values) {
    const double row[] = {static_cast<double>(values)...};
    append_row(row, sizeof...(Ts));
  }

  void append_row(const double* row, size_t count) {
    if (count != width_) throw std::invalid_argument("flight_recorder: wrong number of values for stream " + name_);
    const size_t n = n_.load(std::memory_order_relaxed);
    double* dst = ensure_block(n / kBlockRows) + (n % kBlockRows) * width_;
    for (size_t i = 0; i < width_; ++i) dst[i] = row[i];
    n_.store(n + 1, std::memory_order_release);  // publish the complete row
  }

  // Column `c` of rows [start, stop) as a contiguous vector (used by flush)
  std::vector<double> gather(size_t c, size_t start, size_t stop) const {
    std::vector<double> out;
    out.reserve(stop - start);
    for (size_t r = start; r < stop; ++r) out.push_back(blocks_[r / kBlockRows][(r % kBlockRows) * width_ + c]);
    return out;
  }

 private:
  static constexpr size_t kMaxBlocks = 1 << 16;  // 268M rows per stream

  double* ensure_block(size_t b) {
    if (b >= kMaxBlocks) throw std::length_error("flight_recorder: stream " + name_ + " is full");
    if (!blocks_[b]) blocks_[b] = std::make_unique<double[]>(kBlockRows * width_);
    return blocks_[b].get();
  }

  std::string name_;
  std::vector<std::string> columns_;
  size_t width_;
  // Fixed-size array of block pointers: blocks are never moved, so a concurrent flush can read old rows safely
  std::unique_ptr<std::unique_ptr<double[]>[]> blocks_{new std::unique_ptr<double[]>[kMaxBlocks]};
  std::atomic<size_t> n_{0};
  size_t flushed_{0};  // flush-thread only
  friend class Recorder;
};

class Recorder {
 public:
  explicit Recorder(std::string path = "", const Attributes& metadata = {}) : path_(std::move(path)) {
    set_metadata("format", std::string(kFormatName));
    set_metadata("format_version", kFormatVersion);
    set_metadata("writer", std::string("cpp"));
    set_metadata("created", detail::iso_now());
    set_metadata("host", detail::hostname());
    for (const auto& [k, v] : metadata) set_metadata(k, v);
  }

  // Declare a stream at init and keep the reference (it stays valid for the recorder's lifetime).
  Stream& stream(const std::string& name, const std::vector<std::string>& columns, size_t capacity = 4096) {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& s : streams_)
      if (s->name() == name) throw std::invalid_argument("flight_recorder: stream " + name + " already exists");
    streams_.push_back(std::make_unique<Stream>(name, columns, capacity));
    return *streams_.back();
  }

  // Store rarely-changing arrays ONCE under records/<group>/<key> (integer keys are zero-padded to 6 digits).
  void record(const std::string& group, const std::string& key, std::map<std::string, Array> arrays,
              const Attributes& attrs = {}) {
    std::lock_guard<std::mutex> lock(mutex_);
    records_.push_back({group, key, std::move(arrays), attrs});
  }
  void record(const std::string& group, int64_t key, std::map<std::string, Array> arrays, const Attributes& attrs = {}) {
    char buf[32];
    std::snprintf(buf, sizeof(buf), "%06lld", static_cast<long long>(key));
    record(group, std::string(buf), std::move(arrays), attrs);
  }

  void event(double t, const std::string& kind, const std::string& detail = "") {
    std::lock_guard<std::mutex> lock(mutex_);
    events_.push_back({t, kind, detail});
  }

  void set_metadata(const std::string& key, const Value& value) {
    std::lock_guard<std::mutex> lock(mutex_);
    for (auto& [k, v] : metadata_)
      if (k == key) {
        v = value;
        return;
      }
    metadata_.emplace_back(key, value);
  }

  // Append everything collected since the previous flush. May run on a different thread than the writers.
  std::string flush(const std::string& path = "") {
    std::lock_guard<std::mutex> flush_lock(flush_mutex_);
    if (!path.empty()) {
      // later flushes only append the NEW rows at their offsets; a different file would miss the earlier ones
      if (created_ && path != path_) throw std::invalid_argument("flight_recorder: already writing " + path_);
      path_ = path;
    }
    if (path_.empty()) throw std::invalid_argument("flight_recorder: no path given");

    std::vector<Stream*> streams;
    std::vector<RecordEntry> records;
    std::vector<EventEntry> events;
    Attributes metadata;
    {
      std::lock_guard<std::mutex> lock(mutex_);
      for (auto& s : streams_) streams.push_back(s.get());
      records.assign(records_.begin() + static_cast<long>(records_flushed_), records_.end());
      events.assign(events_.begin() + static_cast<long>(events_flushed_), events_.end());
      metadata = metadata_;
    }

    H5Eset_auto2(H5E_DEFAULT, nullptr, nullptr);  // errors are reported through exceptions
    detail::Id file = created_ ? detail::Id(detail::check_id(H5Fopen(path_.c_str(), H5F_ACC_RDWR, H5P_DEFAULT), "H5Fopen"), H5Fclose)
                               : detail::Id(detail::check_id(H5Fcreate(path_.c_str(), H5F_ACC_TRUNC, H5P_DEFAULT, H5P_DEFAULT), "H5Fcreate"), H5Fclose);
    created_ = true;
    for (const auto& [k, v] : metadata) detail::write_attr(file, k, v);

    detail::Id streams_group = detail::group(file, "streams");
    for (Stream* s : streams) {
      const size_t start = s->flushed_, stop = s->size();
      detail::Id g = detail::group(streams_group, s->name());
      if (H5Aexists(g, "columns") <= 0) detail::write_string_array_attr(g, "columns", s->columns());
      for (size_t c = 0; c < s->columns().size(); ++c) {
        const auto values = s->gather(c, start, stop);
        detail::append_1d(g, s->columns()[c], H5T_NATIVE_DOUBLE, kChunkRows, true, start, stop - start, values.data());
      }
      s->flushed_ = stop;
      detail::write_attr(g, "n_rows", static_cast<int64_t>(stop));
    }

    detail::Id records_group = detail::group(file, "records");
    for (const auto& r : records) {
      detail::Id g = detail::group(records_group, r.group);
      if (H5Lexists(g, r.key.c_str(), H5P_DEFAULT) > 0) detail::check(H5Ldelete(g, r.key.c_str(), H5P_DEFAULT), "H5Ldelete");
      detail::Id rg = detail::group(g, r.key);
      for (const auto& [name, arr] : r.arrays) write_array(rg, name, arr);
      for (const auto& [k, v] : r.attrs) detail::write_attr(rg, k, v);
    }

    detail::Id events_group = detail::group(file, "events");
    {
      hsize_t n0 = 0;
      if (H5Lexists(events_group, "time", H5P_DEFAULT) > 0) {
        detail::Id ds(H5Dopen2(events_group, "time", H5P_DEFAULT), H5Dclose);
        detail::Id sp(H5Dget_space(ds), H5Sclose);
        H5Sget_simple_extent_dims(sp, &n0, nullptr);
      }
      std::vector<double> times;
      std::vector<const char*> kinds, details;
      for (const auto& e : events) {
        times.push_back(e.t);
        kinds.push_back(e.kind.c_str());
        details.push_back(e.detail.c_str());
      }
      detail::Id str = detail::vlen_string_type();
      const hsize_t count = events.size();
      detail::append_1d(events_group, "time", H5T_NATIVE_DOUBLE, 256, false, n0, count, times.data());
      detail::append_1d(events_group, "kind", str, 256, false, n0, count, kinds.data());
      detail::append_1d(events_group, "detail", str, 256, false, n0, count, details.data());
    }
    {
      std::lock_guard<std::mutex> lock(mutex_);
      records_flushed_ += records.size();
      events_flushed_ += events.size();
    }
    return path_;
  }

  std::string save(const std::string& path = "") { return flush(path); }

 private:
  struct RecordEntry {
    std::string group, key;
    std::map<std::string, Array> arrays;
    Attributes attrs;
  };
  struct EventEntry {
    double t;
    std::string kind, detail;
  };

  static void write_array(hid_t loc, const std::string& name, const Array& arr) {
    const int rank = static_cast<int>(arr.shape.size());
    detail::Id space(H5Screate_simple(rank, arr.shape.data(), nullptr), H5Sclose);
    detail::Id dcpl(H5Pcreate(H5P_DATASET_CREATE), H5Pclose);
    if (arr.data.size() > 256) {
      detail::check(H5Pset_chunk(dcpl, rank, arr.shape.data()), "H5Pset_chunk");
      detail::check(H5Pset_shuffle(dcpl), "H5Pset_shuffle");
      detail::check(H5Pset_deflate(dcpl, kGzipLevel), "H5Pset_deflate");
    }
    detail::Id ds(detail::check_id(H5Dcreate2(loc, name.c_str(), H5T_NATIVE_DOUBLE, space, H5P_DEFAULT, dcpl, H5P_DEFAULT), "H5Dcreate2"), H5Dclose);
    detail::check(H5Dwrite(ds, H5T_NATIVE_DOUBLE, H5S_ALL, H5S_ALL, H5P_DEFAULT, arr.data.data()), "H5Dwrite");
  }

  std::string path_;
  bool created_{false};
  std::mutex mutex_, flush_mutex_;
  Attributes metadata_;
  std::vector<std::unique_ptr<Stream>> streams_;
  std::vector<RecordEntry> records_;
  std::vector<EventEntry> events_;
  size_t records_flushed_{0}, events_flushed_{0};
};

}  // namespace fr
