#pragma once
#include "tgcalls/platform/PlatformInterface.h"
#include <atomic>
#include <deque>
#include <functional>
#include <mutex>
#include <vector>

constexpr const char *kPcmInput = "phonebridge.pcm.to-telegram";
constexpr const char *kPcmOutput = "phonebridge.pcm.from-telegram";
constexpr int kPcmRate = 16000;
constexpr int kPcmFrames = 160;

// This is an in-memory transport, not a CoreAudio device or a fake call engine.
struct PcmChannel {
    std::mutex mutex;
    std::deque<int16_t> incoming;
    std::function<void(const std::vector<uint8_t>&)> output;
    std::atomic<uint64_t> inputFrames{0}, outputFrames{0}, underrunFrames{0};
    std::atomic<bool> failed{false};
    bool push(const std::vector<uint8_t> &bytes);
    void clear();
    void take(int16_t *samples);
};

webrtc::scoped_refptr<tgcalls::WrappedAudioDeviceModule> makePcmAudio(std::shared_ptr<PcmChannel> channel);
bool checkPcmAudio();
