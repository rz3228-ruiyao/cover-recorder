cmake_minimum_required(VERSION 3.20)

project(CoverRecorderNative VERSION 0.1.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if (MSVC)
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    add_compile_options(/utf-8)
endif()

set(TRACKTION_ENGINE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../.spikes/tracktion_engine" CACHE PATH "Path to Tracktion Engine checkout")

if (NOT EXISTS "${TRACKTION_ENGINE_DIR}/CMakeLists.txt")
    message(FATAL_ERROR "Tracktion Engine not found at ${TRACKTION_ENGINE_DIR}. Clone it into .spikes/tracktion_engine first.")
endif()

set(TE_ADD_EXAMPLES OFF CACHE BOOL "Do not build Tracktion examples" FORCE)
add_subdirectory("${TRACKTION_ENGINE_DIR}" "${CMAKE_BINARY_DIR}/tracktion_engine")

juce_add_gui_app(CoverRecorderNative
    PRODUCT_NAME "Cover Recorder"
    COMPANY_NAME "Cover Recorder"
    BUNDLE_ID "local.cover-recorder.native"
    MICROPHONE_PERMISSION_ENABLED TRUE
    MICROPHONE_PERMISSION_TEXT "Audio recording")

juce_generate_juce_header(CoverRecorderNative)

target_sources(CoverRecorderNative PRIVATE
    Source/Main.cpp)

target_include_directories(CoverRecorderNative PRIVATE
    "${TRACKTION_ENGINE_DIR}/examples/common")

target_compile_definitions(CoverRecorderNative PRIVATE
    JUCE_MODAL_LOOPS_PERMITTED=1
    JUCE_STRICT_REFCOUNTEDPOINTER=1
    JUCE_USE_CURL=0
    JUCE_WEB_BROWSER=0
    TRACKTION_ENABLE_TIMESTRETCH_SOUNDTOUCH=1)

target_link_libraries(CoverRecorderNative PRIVATE
    tracktion::tracktion_core
    tracktion::tracktion_engine
    tracktion::tracktion_graph
    juce::juce_audio_devices
    juce::juce_audio_formats
    juce::juce_audio_processors
    juce::juce_audio_utils
    juce::juce_data_structures
    juce::juce_dsp
    juce::juce_gui_extra
    juce::juce_recommended_config_flags
    juce::juce_recommended_lto_flags
    juce::juce_recommended_warning_flags)
