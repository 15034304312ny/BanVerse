cmake_minimum_required(VERSION 3.22)
project(deepseek_chat_shiboken16k LANGUAGES CXX)

if(NOT DEFINED SHIBOKEN_SOURCE_ROOT)
    message(FATAL_ERROR "SHIBOKEN_SOURCE_ROOT is required")
endif()
if(NOT DEFINED TARGET_PYTHON_ROOT)
    message(FATAL_ERROR "TARGET_PYTHON_ROOT is required")
endif()
if(NOT DEFINED HOST_PYTHON)
    message(FATAL_ERROR "HOST_PYTHON is required")
endif()

set(lib_root "${SHIBOKEN_SOURCE_ROOT}/libshiboken")
set(support_root
    "${SHIBOKEN_SOURCE_ROOT}/shibokenmodule/files.dir/shibokensupport")
set(embed_dir "${CMAKE_CURRENT_BINARY_DIR}/embed")
file(MAKE_DIRECTORY "${embed_dir}")

set(shiboken_MAJOR_VERSION 6)
set(shiboken_MINOR_VERSION 11)
set(shiboken_MICRO_VERSION 1)
set(Python_VERSION_MAJOR 3)
set(Python_VERSION_MINOR 11)
set(Python_VERSION_PATCH 13)
configure_file("${lib_root}/sbkversion.h.in"
               "${CMAKE_CURRENT_BINARY_DIR}/sbkversion.h" @ONLY)
configure_file("${lib_root}/embed/signature_bootstrap.py"
               "${embed_dir}/signature_bootstrap.py" COPYONLY)

file(GLOB_RECURSE support_python_files CONFIGURE_DEPENDS
    "${support_root}/*.py")
add_custom_command(
    OUTPUT
        "${embed_dir}/signature_bootstrap_inc.h"
        "${embed_dir}/signature_inc.h"
    COMMAND
        "${HOST_PYTHON}" -E
        "${lib_root}/embed/embedding_generator.py"
        --cmake-dir "${embed_dir}"
        --use-pyc false
        --quiet
    DEPENDS
        "${lib_root}/embed/embedding_generator.py"
        "${embed_dir}/signature_bootstrap.py"
        ${support_python_files}
    VERBATIM
)

set(libshiboken_sources
    "${lib_root}/basewrapper.cpp"
    "${lib_root}/bindingmanager.cpp"
    "${lib_root}/bufferprocs_py37.cpp"
    "${lib_root}/debugfreehook.cpp"
    "${lib_root}/gilstate.cpp"
    "${lib_root}/helper.cpp"
    "${lib_root}/pep384ext.cpp"
    "${lib_root}/pep384impl.cpp"
    "${lib_root}/sbkarrayconverter.cpp"
    "${lib_root}/sbkbindingutils.cpp"
    "${lib_root}/sbkcontainer.cpp"
    "${lib_root}/sbkconverter.cpp"
    "${lib_root}/sbkcppstring.cpp"
    "${lib_root}/sbkenum.cpp"
    "${lib_root}/sbkerrors.cpp"
    "${lib_root}/sbkfeature_base.cpp"
    "${lib_root}/sbkmodule.cpp"
    "${lib_root}/sbknumpy.cpp"
    "${lib_root}/sbksmartpointer.cpp"
    "${lib_root}/sbkstaticstrings.cpp"
    "${lib_root}/sbkstring.cpp"
    "${lib_root}/sbktypefactory.cpp"
    "${lib_root}/shibokenbuffer.cpp"
    "${lib_root}/threadstatesaver.cpp"
    "${lib_root}/voidptr.cpp"
    "${lib_root}/signature/signature.cpp"
    "${lib_root}/signature/signature_globals.cpp"
    "${lib_root}/signature/signature_extend.cpp"
    "${lib_root}/signature/signature_helper.cpp"
    "${embed_dir}/signature_bootstrap_inc.h"
    "${embed_dir}/signature_inc.h"
)

add_library(shiboken16k SHARED ${libshiboken_sources})
set_target_properties(shiboken16k PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
    OUTPUT_NAME "shiboken6.abi3"
    PREFIX "lib"
    POSITION_INDEPENDENT_CODE ON
)
target_include_directories(shiboken16k PRIVATE
    "${lib_root}"
    "${lib_root}/signature"
    "${CMAKE_CURRENT_BINARY_DIR}"
    "${TARGET_PYTHON_ROOT}/Include"
    "${TARGET_PYTHON_ROOT}"
)
target_compile_definitions(shiboken16k PRIVATE
    ANDROID
    BUILD_LIBSHIBOKEN
    NDEBUG
    Py_LIMITED_API=0x030a0000
    SHIBOKEN_NO_EMBEDDING_PYC=1
)
target_compile_options(shiboken16k PRIVATE
    -fvisibility=hidden
    -fvisibility-inlines-hidden
)
target_link_libraries(shiboken16k PRIVATE
    "${TARGET_PYTHON_ROOT}/android-build/libpython3.11.so"
    log
    dl
)
target_link_options(shiboken16k PRIVATE
    "LINKER:-z,max-page-size=16384"
)
