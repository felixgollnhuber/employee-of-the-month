#include "strict_devices.h"
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <mutex>
#include <vector>

namespace {
std::mutex guard;
std::string inputUID, outputUID;
AudioObjectID inputID = 0, outputID = 0;
bool valid(const std::string &s) {
    return !s.empty() && s.size() <= 256 && s != "default" && s[0] != '#'
        && s.find_first_of("\r\n\0", 0, 3) == std::string::npos;
}
AudioObjectID resolve(const std::string &uid, bool input) {
    AudioObjectPropertyAddress p{kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, 0};
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &p, 0, nullptr, &size) != noErr || size > 65536) return 0;
    std::vector<AudioObjectID> ids(size / sizeof(AudioObjectID));
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &p, 0, nullptr, &size, ids.data()) != noErr) return 0;
    AudioObjectID found = 0;
    for (auto id : ids) {
        p = {kAudioDevicePropertyDeviceUID, kAudioObjectPropertyScopeGlobal, 0};
        CFStringRef value = nullptr;
        size = sizeof(value);
        if (AudioObjectGetPropertyData(id, &p, 0, nullptr, &size, &value) != noErr || !value) continue;
        char text[1024]{};
        const bool match = CFStringGetCString(value, text, sizeof(text), kCFStringEncodingUTF8) && uid == text;
        CFRelease(value);
        if (!match) continue;
        if (found) return 0; // Ambiguous UID must never select a device.
        p = {kAudioDevicePropertyDeviceIsAlive, kAudioObjectPropertyScopeGlobal, 0};
        UInt32 alive = 0; size = sizeof(alive);
        if (AudioObjectGetPropertyData(id, &p, 0, nullptr, &size, &alive) != noErr || !alive) return 0;
        p = {kAudioDevicePropertyStreams, input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput, 0};
        size = 0;
        if (AudioObjectGetPropertyDataSize(id, &p, 0, nullptr, &size) != noErr || size == 0) return 0;
        found = id;
    }
    return found;
}
}

bool bridge_set_devices(const std::string &input, const std::string &output) {
    std::lock_guard<std::mutex> lock(guard);
    if (!valid(input) || !valid(output) || input == output || !inputUID.empty()) return false;
    // Configuration only. Resolving device IDs happens exclusively at live start.
    inputUID = input; outputUID = output;
    return true;
}
extern "C" bool bridge_resolve_audio_device(bool input, uint32_t *result) {
    std::lock_guard<std::mutex> lock(guard);
    const auto &uid = input ? inputUID : outputUID;
    if (!valid(uid)) return false;
    const auto id = resolve(uid, input);
    auto &previous = input ? inputID : outputID;
    if (!id || (previous && previous != id)) return false;
    previous = id;
    *result = id;
    return true;
}
bool bridge_devices_alive() {
    uint32_t a = 0, b = 0;
    return bridge_resolve_audio_device(true, &a) && bridge_resolve_audio_device(false, &b);
}

std::vector<BridgeDevice> bridge_list_devices() {
    AudioObjectPropertyAddress p{kAudioHardwarePropertyDevices, kAudioObjectPropertyScopeGlobal, 0};
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &p, 0, nullptr, &size) != noErr || size > 65536) return {};
    std::vector<AudioObjectID> ids(size / sizeof(AudioObjectID));
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &p, 0, nullptr, &size, ids.data()) != noErr) return {};
    std::vector<BridgeDevice> result;
    for (auto id : ids) {
        auto stringProperty = [&](AudioObjectPropertySelector selector) {
            AudioObjectPropertyAddress a{selector, kAudioObjectPropertyScopeGlobal, 0};
            CFStringRef value = nullptr; UInt32 n = sizeof(value); char text[1024]{};
            if (AudioObjectGetPropertyData(id, &a, 0, nullptr, &n, &value) != noErr || !value) return std::string();
            CFStringGetCString(value, text, sizeof(text), kCFStringEncodingUTF8); CFRelease(value);
            return std::string(text);
        };
        auto streams = [&](bool input) {
            AudioObjectPropertyAddress a{kAudioDevicePropertyStreams, input ? kAudioObjectPropertyScopeInput : kAudioObjectPropertyScopeOutput, 0};
            UInt32 n = 0;
            return AudioObjectGetPropertyDataSize(id, &a, 0, nullptr, &n) == noErr && n > 0;
        };
        result.push_back({stringProperty(kAudioDevicePropertyDeviceUID), stringProperty(kAudioObjectPropertyName), streams(true), streams(false)});
    }
    return result;
}
