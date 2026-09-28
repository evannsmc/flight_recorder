cmake_minimum_required(VERSION 3.16)
project(flight_recorder VERSION 0.1.0 LANGUAGES CXX)

if(NOT CMAKE_CXX_STANDARD)
  set(CMAKE_CXX_STANDARD 17)
endif()
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(ament_cmake REQUIRED)
find_package(ament_cmake_python REQUIRED)
find_package(HDF5 REQUIRED COMPONENTS C)

# ---- C++: header-only library target ------------------------------------------------------------------------------
#   find_package(flight_recorder REQUIRED)
#   target_link_libraries(my_node flight_recorder::flight_recorder)
add_library(flight_recorder INTERFACE)
target_include_directories(flight_recorder INTERFACE
  $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
  $<INSTALL_INTERFACE:include>
  ${HDF5_INCLUDE_DIRS})
target_link_libraries(flight_recorder INTERFACE ${HDF5_C_LIBRARIES})
target_compile_features(flight_recorder INTERFACE cxx_std_17)

install(DIRECTORY include/ DESTINATION include)
install(TARGETS flight_recorder EXPORT export_flight_recorder)
ament_export_targets(export_flight_recorder HAS_LIBRARY_TARGET)
ament_export_include_directories(include)

# ---- C++ example (also used by the C++ -> Python round-trip test) ---------------------------------------------------
add_executable(cpp_example examples/cpp_example.cpp)
target_link_libraries(cpp_example flight_recorder)
target_compile_options(cpp_example PRIVATE -Wall -Wextra -Wpedantic)
add_executable(cpp_bench benchmarks/cpp_bench.cpp)
target_link_libraries(cpp_bench flight_recorder)
install(TARGETS cpp_example cpp_bench DESTINATION lib/${PROJECT_NAME})

# ---- Python -------------------------------------------------------------------------------------------------------
ament_python_install_package(${PROJECT_NAME})
install(PROGRAMS scripts/flight_recorder DESTINATION lib/${PROJECT_NAME})

# ---- tests --------------------------------------------------------------------------------------------------------
if(BUILD_TESTING)
  find_package(ament_cmake_pytest REQUIRED)
  foreach(t test_recorder test_compat test_cpp_roundtrip test_gc)
    ament_add_pytest_test(${t} test/${t}.py
      ENV CPP_EXAMPLE=${CMAKE_CURRENT_BINARY_DIR}/cpp_example
      APPEND_ENV PYTHONPATH=${CMAKE_CURRENT_SOURCE_DIR}
      TIMEOUT 300)
  endforeach()
endif()

ament_package()
