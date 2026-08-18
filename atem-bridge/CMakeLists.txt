cmake_minimum_required(VERSION 3.20)
project(atem-bridge)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# SDK paths (update these based on installed SDK location)
set(BLACKMAGIC_SDK_PATH "C:/Program Files/Blackmagic Design/ATEM Switchers SDK" CACHE PATH "Blackmagic ATEM SDK path")

# Include directories
include_directories(
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    "${BLACKMAGIC_SDK_PATH}/Include"
)

# Source files
set(SOURCES
    src/main.cpp
    src/atem_controller.cpp
    src/atem_callbacks.cpp
    src/http_server.cpp
)

set(HEADERS
    include/atem_controller.h
    include/atem_state.h
    include/http_server.h
)

# Create executable
add_executable(atem-bridge ${SOURCES} ${HEADERS})

# Link libraries
if(MSVC)
    # Link against Blackmagic SDK libraries
    set(BM_LIB_PATH "${BLACKMAGIC_SDK_PATH}/Lib/x64")
    target_link_directories(atem-bridge PRIVATE ${BM_LIB_PATH})
    target_link_libraries(atem-bridge PRIVATE BMDSwitcherAPI)
    
    # Windows libraries
    target_link_libraries(atem-bridge PRIVATE ws2_32 userenv)
endif()

# Output directory
set_target_properties(atem-bridge PROPERTIES
    RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin"
)

# Compiler flags
if(MSVC)
    target_compile_options(atem-bridge PRIVATE /W4 /WX)
else()
    target_compile_options(atem-bridge PRIVATE -Wall -Wextra -Werror)
endif()

# Enable testing
enable_testing()

# TODO: Add test executable
# add_executable(atem-bridge-test tests/test_atem.cpp)
# target_link_libraries(atem-bridge-test PRIVATE gtest_main)
# add_test(NAME AtemTests COMMAND atem-bridge-test)
