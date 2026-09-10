cc_binary(
    name = "media_probe",
    srcs = ["media_probe.cpp"],
    copts = [
        "-Isubmodules/TgVoipWebrtc/tgcalls",
        "-Isubmodules/TgVoipWebrtc/tgcalls/tgcalls",
        "-Ithird-party/webrtc/webrtc",
        "-Ithird-party/webrtc/dependencies",
        "-Ithird-party/webrtc/absl",
        "-Ithird-party/libyuv",
        "-DWEBRTC_MAC",
        "-DWEBRTC_POSIX",
        "-DRTC_ENABLE_VP9",
        "-DNDEBUG",
        "-std=c++17",
    ],
    deps = ["//submodules/TgVoipWebrtc:tgcalls_core", "//third-party/webrtc:bridge_macos_adm", ":strict_devices"],
)

cc_library(
    name = "strict_devices",
    srcs = ["strict_devices.cpp"],
    hdrs = ["strict_devices.h"],
    copts = ["-std=c++17"],
    linkopts = ["-framework", "CoreAudio", "-framework", "CoreFoundation"],
)

cc_binary(
    name = "media_runtime",
    srcs = ["media_runtime.cpp"],
    copts = [
        "-Isubmodules/TgVoipWebrtc/tgcalls",
        "-Isubmodules/TgVoipWebrtc/tgcalls/tgcalls",
        "-Ithird-party/webrtc/webrtc",
        "-Ithird-party/webrtc/dependencies",
        "-Ithird-party/webrtc/absl",
        "-Ithird-party/libyuv",
        "-DWEBRTC_MAC", "-DWEBRTC_POSIX", "-DRTC_ENABLE_VP9", "-DNDEBUG",
        "-std=c++17",
    ],
    deps = ["//submodules/TgVoipWebrtc:tgcalls_core", "//third-party/webrtc:bridge_macos_adm", ":strict_devices"],
)
