# Distributed under the OSI-approved BSD 3-Clause License.  See accompanying
# file LICENSE.rst or https://cmake.org/licensing for details.

# Re-configure to save learned information.
configure_file(
  ${CMAKE_ROOT}/Modules/CMakeOBJCCompiler.cmake.in
  ${CMAKE_PLATFORM_INFO_DIR}/CMakeOBJCCompiler.cmake
  @ONLY)
