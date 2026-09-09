# Local build extension: compile the actual macOS AudioDeviceModule from the
# same pinned WebRTC source. No null/fake AudioDeviceModule factory is supplied.
cc_library(
    name = "bridge_macos_adm",
    srcs = [
        "webrtc/modules/audio_device/audio_device_impl.cc",
        "webrtc/modules/audio_device/mac/audio_device_mac.cc",
        "webrtc/modules/audio_device/mac/audio_mixer_manager_mac.cc",
        "webrtc/modules/third_party/portaudio/pa_ringbuffer.c",
    ],
    hdrs = glob(["webrtc/modules/audio_device/**/*.h", "webrtc/modules/third_party/portaudio/*.h"]),
    copts = [
        "-Ithird-party/webrtc/webrtc",
        "-Ithird-party/webrtc/absl",
        "-DWEBRTC_MAC",
        "-DWEBRTC_INCLUDE_INTERNAL_AUDIO_DEVICE",
    ] + platform_shared_flags + arm64_specific_flags + optimization_flags,
    cxxopts = ["-std=c++17"],
    deps = [":webrtc"],
    linkopts = ["-framework", "CoreAudio", "-framework", "AudioToolbox", "-framework", "ApplicationServices"],
    visibility = ["//visibility:public"],
)
