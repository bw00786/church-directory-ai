cmake_minimum_required(VERSION 3.20)
project(atem-bridge CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

include(FetchContent)

# --- Third-party dependencies (header-only, fetched at configure time) -------
FetchContent_Declare(
    httplib
    GIT_REPOSITORY https://github.com/yhirose/cpp-httplib.git
    GIT_TAG v0.15.3
)
FetchContent_Declare(
    nlohmann_json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG v3.11.3
)
FetchContent_MakeAvailable(httplib nlohmann_json)

# --- Source files ------------------------------------------------------------
set(SOURCES
    src/main.cpp
    src/atem_controller.cpp
    src/atem_callbacks.cpp
    src/http_server.cpp
)

set(HEADERS
    include/atem_controller.h
    include/atem_callbacks.h
    include/atem_state.h
    include/http_server.h
    include/logger.h
)

# --- Blackmagic ATEM SDK (Windows / COM only) --------------------------------
# The SDK ships an IDL that MIDL compiles into BMDSwitcherAPI.h. We generate it
# into the build directory and add that directory to the include path.
if(WIN32)
    set(BLACKMAGIC_IDL_DIR "${CMAKE_CURRENT_SOURCE_DIR}/include/include")
    set(BLACKMAGIC_IDL "${BLACKMAGIC_IDL_DIR}/BMDSwitcherAPI.idl")
    set(GENERATED_HEADER "${CMAKE_CURRENT_BINARY_DIR}/BMDSwitcherAPI.h")
    set(GENERATED_IID "${CMAKE_CURRENT_BINARY_DIR}/BMDSwitcherAPI_i.c")

    find_program(MIDL_EXECUTABLE midl)
    if(NOT MIDL_EXECUTABLE)
        message(FATAL_ERROR "midl.exe not found. Run CMake from a Visual Studio Developer Command Prompt.")
    endif()

   add_custom_command(
    OUTPUT ${GENERATED_HEADER} ${GENERATED_IID}
    COMMAND ${MIDL_EXECUTABLE}
            /nologo
            /env x64
            /header BMDSwitcherAPI.h
            /iid BMDSwitcherAPI_i.c
            /I "${BLACKMAGIC_IDL_DIR}"
            "${BLACKMAGIC_IDL}"
    WORKING_DIRECTORY ${CMAKE_CURRENT_BINARY_DIR}
    DEPENDS ${BLACKMAGIC_IDL}
    COMMENT "Compiling BMDSwitcherAPI.idl with MIDL"
    VERBATIM
)
    list(APPEND SOURCES ${GENERATED_HEADER})
endif()

# --- Executable --------------------------------------------------------------
add_executable(atem-bridge ${SOURCES} ${HEADERS})

target_include_directories(atem-bridge PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${CMAKE_CURRENT_BINARY_DIR}
)

target_link_libraries(atem-bridge PRIVATE
    httplib::httplib
    nlohmann_json::nlohmann_json
)

# --- Platform specifics ------------------------------------------------------
if(WIN32)
    target_compile_definitions(atem-bridge PRIVATE
        interface=struct
        WIN32_LEAN_AND_MEAN
        NOMINMAX
        _CRT_SECURE_NO_WARNINGS
    )
    # COM / OLE runtime plus Winsock for the HTTP server.
    target_link_libraries(atem-bridge PRIVATE ole32 oleaut32 ws2_32)
endif()

# Output directory
set_target_properties(atem-bridge PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

# Compiler flags
if(MSVC)
    target_compile_options(atem-bridge PRIVATE /W4)
else()
    target_compile_options(atem-bridge PRIVATE -Wall -Wextra)
endif()

# Enable testing
enable_testing()

# TODO: Add test executable
# add_executable(atem-bridge-test tests/test_atem.cpp)
# target_link_libraries(atem-bridge-test PRIVATE gtest_main)
# add_test(NAME AtemTests COMMAND atem-bridge-test)
