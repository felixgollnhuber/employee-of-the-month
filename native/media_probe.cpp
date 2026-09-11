// Link and inspect the real Telegram media library without constructing a call.
#include "tgcalls/Instance.h"
#include "tgcalls/v2/InstanceV2Impl.h"
#include <iostream>

int main() {
    tgcalls::Register<tgcalls::InstanceV2Impl>();
    const auto versions = tgcalls::Meta::Versions();
    const auto maxLayer = tgcalls::Meta::MaxLayer();
    if (versions.empty() || maxLayer <= 0) {
        std::cerr << "Native media registration failed\n";
        return 1;
    }
    std::cout << "{\"mode\":\"native-media-metadata-only\",\"max_layer\":" << maxLayer
              << ",\"library_versions\":[";
    bool first = true;
    for (const auto &version : versions) {
        // Versions originate in the pinned implementation; reject unexpected text.
        if (version.find_first_not_of("0123456789.") != std::string::npos) return 2;
        if (!first) std::cout << ',';
        first = false;
        std::cout << '"' << version << '"';
    }
    std::cout << "],\"call_created\":false,\"audio_opened\":false,\"live_adapter_ready\":false}\n";
}
