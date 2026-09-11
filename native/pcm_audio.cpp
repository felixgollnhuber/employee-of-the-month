#include "pcm_audio.h"
#include "modules/audio_device/include/audio_device_default.h"
#include "api/make_ref_counted.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <condition_variable>
#include <cstring>
#include <thread>

bool PcmChannel::push(const std::vector<uint8_t> &bytes) {
    if (bytes.empty() || bytes.size() % 2 || bytes.size() > 64000) return false;
    std::lock_guard<std::mutex> lock(mutex);
    if (incoming.size() + bytes.size() / 2 > kPcmRate * 10) { failed = true; return false; }
    for (size_t i = 0; i < bytes.size(); i += 2)
        incoming.push_back(int16_t(uint16_t(bytes[i]) | uint16_t(bytes[i + 1]) << 8));
    return true;
}
void PcmChannel::clear() { std::lock_guard<std::mutex> lock(mutex); incoming.clear(); }
void PcmChannel::take(int16_t *samples) {
    std::lock_guard<std::mutex> lock(mutex);
    for (int i = 0; i < kPcmFrames; ++i) {
        if (incoming.empty()) { samples[i] = 0; ++underrunFrames; }
        else { samples[i] = incoming.front(); incoming.pop_front(); ++inputFrames; }
    }
}

namespace {
class PcmAudio : public webrtc::webrtc_impl::AudioDeviceModuleDefault<tgcalls::WrappedAudioDeviceModule> {
    std::shared_ptr<PcmChannel> channel;
    mutable std::mutex transportMutex;
    std::mutex waitMutex;
    std::condition_variable wake;
    webrtc::AudioTransport *transport = nullptr;
    std::atomic<bool> initialized{false}, playing{false}, recording{false}, done{false}, active{true};
    std::thread worker;
    void launch() {
        if (worker.joinable()) return;
        worker = std::thread([this] {
            auto next = std::chrono::steady_clock::now();
            while (!done) {
                {
                    std::lock_guard<std::mutex> lock(transportMutex);
                    if (transport && active && !channel->failed) {
                        if (recording) {
                            std::array<int16_t, kPcmFrames> samples{};
                            channel->take(samples.data());
                            uint32_t level = 0;
                            if (transport->RecordedDataIsAvailable(samples.data(), kPcmFrames, 2, 1,
                                kPcmRate, 10, 0, 0, false, level) != 0) channel->failed = true;
                        }
                        if (playing) {
                            std::array<int16_t, kPcmFrames> samples{};
                            size_t count = 0;
                            int64_t elapsed = -1, ntp = -1;
                            if (transport->NeedMorePlayData(kPcmFrames, 2, 1, kPcmRate,
                                samples.data(), count, &elapsed, &ntp) != 0 || count > kPcmFrames) {
                                channel->failed = true;
                            } else if (channel->output) {
                                std::vector<uint8_t> bytes(kPcmFrames * 2);
                                for (int i = 0; i < kPcmFrames; ++i) {
                                    auto value = uint16_t(samples[i]);
                                    bytes[i * 2] = value & 255; bytes[i * 2 + 1] = value >> 8;
                                }
                                channel->outputFrames += kPcmFrames;
                                channel->output(bytes);
                            }
                        }
                    }
                }
                next += std::chrono::milliseconds(10);
                if (std::chrono::steady_clock::now() - next > std::chrono::milliseconds(30))
                    next = std::chrono::steady_clock::now() + std::chrono::milliseconds(10);
                std::unique_lock<std::mutex> lock(waitMutex);
                wake.wait_until(lock, next, [this] { return done.load(); });
            }
        });
    }
    static int name(uint16_t index, char *label, char *uid, const char *value) {
        if (index != 0 || !label || !uid) return -1;
        std::strncpy(label, value, webrtc::kAdmMaxDeviceNameSize);
        std::strncpy(uid, value, webrtc::kAdmMaxGuidSize);
        return 0;
    }
public:
    explicit PcmAudio(std::shared_ptr<PcmChannel> value) : channel(std::move(value)) {}
    ~PcmAudio() override { Stop(); }
    void setIsActive(bool value) override { active = value; }
    int32_t Init() override { if (done) return -1; initialized = true; return 0; }
    bool Initialized() const override { return initialized; }
    int32_t Terminate() override { Stop(); return 0; }
    void Stop() override {
        done = true; playing = false; recording = false; wake.notify_all();
        if (worker.joinable()) worker.join();
        initialized = false; channel->clear();
    }
    int32_t RegisterAudioCallback(webrtc::AudioTransport *value) override {
        std::lock_guard<std::mutex> lock(transportMutex); transport = value; return 0;
    }
    int32_t ActiveAudioLayer(AudioLayer *value) const override { *value = kDummyAudio; return 0; }
    int16_t PlayoutDevices() override { return 1; }
    int16_t RecordingDevices() override { return 1; }
    int32_t PlayoutDeviceName(uint16_t i, char *n, char *g) override { return name(i, n, g, kPcmOutput); }
    int32_t RecordingDeviceName(uint16_t i, char *n, char *g) override { return name(i, n, g, kPcmInput); }
    int32_t SetPlayoutDevice(uint16_t i) override { return i == 0 ? 0 : -1; }
    int32_t SetRecordingDevice(uint16_t i) override { return i == 0 ? 0 : -1; }
    int32_t SetPlayoutDevice(WindowsDeviceType) override { return -1; }
    int32_t SetRecordingDevice(WindowsDeviceType) override { return -1; }
    int32_t PlayoutIsAvailable(bool *v) override { *v = true; return 0; }
    int32_t RecordingIsAvailable(bool *v) override { *v = true; return 0; }
    int32_t InitPlayout() override { return initialized && !done ? 0 : -1; }
    int32_t InitRecording() override { return initialized && !done ? 0 : -1; }
    bool PlayoutIsInitialized() const override { return initialized && !done; }
    bool RecordingIsInitialized() const override { return initialized && !done; }
    int32_t StartPlayout() override { if (!initialized || done) return -1; playing = true; launch(); return 0; }
    int32_t StartRecording() override { if (!initialized || done) return -1; recording = true; launch(); return 0; }
    int32_t StopPlayout() override { playing = false; return 0; }
    int32_t StopRecording() override { recording = false; return 0; }
    bool Playing() const override { return playing; }
    bool Recording() const override { return recording; }
    int32_t SetStereoPlayout(bool v) override { return v ? -1 : 0; }
    int32_t SetStereoRecording(bool v) override { return v ? -1 : 0; }
    int32_t StereoPlayout(bool *v) const override { *v = false; return 0; }
    int32_t StereoRecording(bool *v) const override { *v = false; return 0; }
    int32_t SpeakerVolumeIsAvailable(bool *v) override { *v = false; return 0; }
    int32_t MicrophoneVolumeIsAvailable(bool *v) override { *v = false; return 0; }
    int32_t SpeakerMuteIsAvailable(bool *v) override { *v = false; return 0; }
    int32_t MicrophoneMuteIsAvailable(bool *v) override { *v = false; return 0; }
    int32_t PlayoutDelay(uint16_t *v) const override { *v = 10; return 0; }
};
}
webrtc::scoped_refptr<tgcalls::WrappedAudioDeviceModule> makePcmAudio(std::shared_ptr<PcmChannel> channel) {
    auto result = rtc::make_ref_counted<PcmAudio>(std::move(channel));
    if (result->Init() != 0) return nullptr;
    return result;
}

bool checkPcmAudio() {
    class Transport : public webrtc::AudioTransport {
    public:
        std::atomic<int> input{0}, output{0};
        std::atomic<bool> matched{false};
        int32_t RecordedDataIsAvailable(const void *data, size_t n, size_t bytes, size_t channels,
            uint32_t rate, uint32_t, int32_t, uint32_t, bool, uint32_t &level) override {
            level = 0;
            if (n != kPcmFrames || bytes != 2 || channels != 1 || rate != kPcmRate) return -1;
            if (++input == 1) {
                const auto *p = static_cast<const int16_t *>(data);
                matched = std::all_of(p, p + n, [](int16_t x) { return x == -1234; });
            }
            return 0;
        }
        int32_t NeedMorePlayData(size_t n, size_t, size_t, uint32_t, void *data, size_t &out,
            int64_t *elapsed, int64_t *ntp) override {
            std::fill_n(static_cast<int16_t *>(data), n, 4321); out = n; *elapsed = 0; *ntp = 0; ++output; return 0;
        }
        void PullRenderData(int, int, size_t, size_t, void *, int64_t *, int64_t *) override {}
    } transport;
    auto channel = std::make_shared<PcmChannel>();
    std::mutex mutex; std::condition_variable delivered; bool matchedOutput = false;
    channel->output = [&](const std::vector<uint8_t> &bytes) {
        std::lock_guard<std::mutex> lock(mutex);
        matchedOutput = bytes.size() == kPcmFrames * 2 && bytes[0] == 0xe1 && bytes[1] == 0x10;
        delivered.notify_all();
    };
    std::vector<uint8_t> input(kPcmFrames * 2);
    for (int i = 0; i < kPcmFrames; ++i) { input[2*i] = 0x2e; input[2*i+1] = 0xfb; }
    if (!channel->push(input) || channel->push({1})) return false;
    auto adm = makePcmAudio(channel);
    adm->RegisterAudioCallback(&transport); adm->InitRecording(); adm->InitPlayout();
    // Start both directions before the worker begins processing the first frame.
    adm->StartRecording(); adm->StartPlayout();
    { std::unique_lock<std::mutex> lock(mutex); delivered.wait_for(lock, std::chrono::seconds(2), [&] { return matchedOutput; }); }
    adm->Stop(); adm->RegisterAudioCallback(nullptr);
    std::array<int16_t, kPcmFrames> silence{}; channel->take(silence.data());
    return matchedOutput && transport.matched && transport.input > 0 && transport.output > 0
        && !channel->failed && !adm->Recording() && !adm->Playing()
        && std::all_of(silence.begin(), silence.end(), [](int16_t v) { return v == 0; });
}
