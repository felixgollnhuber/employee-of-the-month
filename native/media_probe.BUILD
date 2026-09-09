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
    deps = ["//submodules/TgVoipWebrtc:tgcalls_core", "//third-party/webrtc:bridge_macos_adm"],
)
