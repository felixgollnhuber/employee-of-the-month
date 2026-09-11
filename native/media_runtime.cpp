// Private JSON-lines IPC. Ordinary stdout/stderr from dependencies is discarded.
// Only an explicit --allow-audio flag allows construction of a media instance.
#include "tgcalls/Instance.h"
#include "tgcalls/v2/InstanceV2Impl.h"
#include "tgcalls/platform/PlatformInterface.h"
#include "tgcalls/third-party/json11.hpp"
#include "api/make_ref_counted.h"
#include "rtc_base/logging.h"
#include "strict_devices.h"
#include "pcm_audio.h"
#include <array>
#include <chrono>
#include <future>
#include <iostream>
#include <mutex>
#include <set>
#include <poll.h>
#include <signal.h>
#include <sys/resource.h>
#include <unistd.h>

using Json = json11::Json;
namespace {
int ipc = -1;
std::mutex outputMutex;
void emit(Json::object value) {
    const auto data = Json(value).dump() + "\n";
    std::lock_guard<std::mutex> lock(outputMutex);
    size_t offset = 0;
    while (offset < data.size()) {
        const auto n = write(ipc, data.data() + offset, data.size() - offset);
        if (n <= 0) _Exit(3);
        offset += n;
    }
}
std::string requiredString(const Json &j, const char *key, size_t maximum = 65536) {
    if (!j[key].is_string() || j[key].string_value().size() > maximum) throw std::runtime_error("invalid_field");
    return j[key].string_value();
}
std::vector<uint8_t> unhex(const std::string &s, size_t expected = 0) {
    if (s.size() % 2 || s.size() > 2 * 1024 * 1024 || (expected && s.size() != expected * 2)) throw std::runtime_error("invalid_binary");
    std::vector<uint8_t> result;
    auto digit = [](char c) -> int { if (c >= '0' && c <= '9') return c-'0'; if (c >= 'a' && c <= 'f') return c-'a'+10; return -1; };
    for (size_t i = 0; i < s.size(); i += 2) {
        const int a = digit(s[i]), b = digit(s[i+1]);
        if (a < 0 || b < 0) throw std::runtime_error("invalid_binary");
        result.push_back(uint8_t(a * 16 + b));
    }
    return result;
}
std::string hex(const std::vector<uint8_t> &data) {
    constexpr char d[] = "0123456789abcdef";
    std::string result; result.reserve(data.size() * 2);
    for (auto b : data) { result += d[b >> 4]; result += d[b & 15]; }
    return result;
}
class StrictADM : public tgcalls::DefaultWrappedAudioDeviceModule {
    std::atomic<bool> inputSet{false}, outputSet{false};
    int deny() { emit({{"event", "state"}, {"state", "failed"}}); return -1; }
public:
    explicit StrictADM(webrtc::scoped_refptr<webrtc::AudioDeviceModule> module)
        : DefaultWrappedAudioDeviceModule(module) {}
    int32_t SetRecordingDevice(WindowsDeviceType) override { return deny(); }
    int32_t SetPlayoutDevice(WindowsDeviceType) override { return deny(); }
    int32_t SetRecordingDevice(uint16_t index) override {
        uint32_t id = 0;
        if (!index || !bridge_resolve_audio_device(true, &id)) return deny();
        const int code = DefaultWrappedAudioDeviceModule::SetRecordingDevice(index);
        inputSet = code == 0; return code;
    }
    int32_t SetPlayoutDevice(uint16_t index) override {
        uint32_t id = 0;
        if (!index || !bridge_resolve_audio_device(false, &id)) return deny();
        const int code = DefaultWrappedAudioDeviceModule::SetPlayoutDevice(index);
        outputSet = code == 0; return code;
    }
    int32_t InitRecording() override { return inputSet && bridge_devices_alive() ? DefaultWrappedAudioDeviceModule::InitRecording() : deny(); }
    int32_t InitPlayout() override { return outputSet && bridge_devices_alive() ? DefaultWrappedAudioDeviceModule::InitPlayout() : deny(); }
    int32_t StartRecording() override { return inputSet && bridge_devices_alive() ? DefaultWrappedAudioDeviceModule::StartRecording() : deny(); }
    int32_t StartPlayout() override { return outputSet && bridge_devices_alive() ? DefaultWrappedAudioDeviceModule::StartPlayout() : deny(); }
    void Stop() override {
        StopRecording(); StopPlayout(); Terminate();
    }
};

tgcalls::Descriptor descriptor(const Json &data, const std::shared_ptr<PcmChannel> &pcm) {
    if (requiredString(data, "version") != "12.0.0" || !data["outgoing"].is_bool() || !data["outgoing"].bool_value())
        throw std::runtime_error("unsupported_version_or_direction");
    auto bytes = unhex(requiredString(data, "key_hex", 512), 256);
    auto key = std::make_shared<std::array<uint8_t, 256>>();
    std::copy(bytes.begin(), bytes.end(), key->begin());
    tgcalls::Descriptor d{.encryptionKey = tgcalls::EncryptionKey(key, true)};
    d.version = "12.0.0";
    d.mediaDevicesConfig.audioInputId = requiredString(data, "input_uid", 256);
    d.mediaDevicesConfig.audioOutputId = requiredString(data, "output_uid", 256);
    d.initialInputDeviceId = d.mediaDevicesConfig.audioInputId;
    d.initialOutputDeviceId = d.mediaDevicesConfig.audioOutputId;
    if (d.initialInputDeviceId.empty() || d.initialOutputDeviceId.empty() || d.initialInputDeviceId == d.initialOutputDeviceId
        || d.initialInputDeviceId == "default" || d.initialOutputDeviceId == "default"
        || d.initialInputDeviceId[0] == '#' || d.initialOutputDeviceId[0] == '#') throw std::runtime_error("explicit_uids_required");
    if (!data["enable_p2p"].is_bool()) throw std::runtime_error("missing_privacy_policy");
    d.config.enableP2P = data["enable_p2p"].bool_value();
    d.config.initializationTimeout = 20; d.config.receiveTimeout = 20;
    d.config.maxApiLayer = 92;
    d.config.enableCallUpgrade = false;
    d.config.enableAEC = false; d.config.enableNS = false; d.config.enableAGC = false;
    d.config.customParameters = requiredString(data, "custom_parameters");
    std::string error;
    if (!Json::parse(d.config.customParameters, error).is_object() || !error.empty()) throw std::runtime_error("invalid_custom_parameters");
    if (!data["rtc_servers"].is_array() || data["rtc_servers"].array_items().empty() || data["rtc_servers"].array_items().size() > 256)
        throw std::runtime_error("invalid_relay_set");
    for (const auto &server : data["rtc_servers"].array_items()) {
        const int id = server["id"].int_value(), port = server["port"].int_value();
        if (!server["id"].is_number() || server["id"].number_value() != id || id < 0 || id > 255
            || !server["port"].is_number() || server["port"].number_value() != port || port < 1 || port > 65535
            || !server["is_turn"].is_bool() || !server["is_tcp"].is_bool()) throw std::runtime_error("invalid_relay");
        tgcalls::RtcServer s;
        s.id = uint8_t(id); s.port = uint16_t(port);
        s.host = requiredString(server, "host", 128); if (s.host.empty()) throw std::runtime_error("empty_relay");
        s.login = requiredString(server, "login", 4096); s.password = requiredString(server, "password", 4096);
        s.isTurn = server["is_turn"].bool_value(); s.isTcp = server["is_tcp"].bool_value();
        if (s.login == "reflector" && (!id || unhex(s.password, 16).size() != 16)) throw std::runtime_error("invalid_reflector");
        d.rtcServers.push_back(std::move(s));
    }
    d.initialNetworkType = tgcalls::NetworkType::Unknown;
    d.stateUpdated = [](tgcalls::State state) {
        const char *name = state == tgcalls::State::Established ? "connected" : state == tgcalls::State::Failed ? "failed"
            : state == tgcalls::State::Reconnecting ? "reconnecting" : "connecting";
        emit({{"event", "state"}, {"state", name}});
    };
    d.signalingDataEmitted = [](const std::vector<uint8_t> &bytes) {
        if (bytes.size() > 512 * 1024) _Exit(4);
        emit({{"event", "signaling"}, {"data_hex", hex(bytes)}});
    };
    if (data["audio_mode"].string_value() == "pcm16") {
        if (d.initialInputDeviceId != kPcmInput || d.initialOutputDeviceId != kPcmOutput)
            throw std::runtime_error("invalid_pcm_endpoints");
        d.createWrappedAudioDeviceModule = [pcm](webrtc::TaskQueueFactory *) {
            auto result = makePcmAudio(pcm);
            if (!result) _Exit(5); // Never fall back to physical hardware.
            return result;
        };
    } else {
    if (!data["audio_mode"].is_null() && data["audio_mode"].string_value() != "devices")
        throw std::runtime_error("invalid_audio_mode");
    d.createWrappedAudioDeviceModule = [](webrtc::TaskQueueFactory *factory) -> webrtc::scoped_refptr<tgcalls::WrappedAudioDeviceModule> {
        if (!bridge_devices_alive()) _Exit(5);
        auto underlying = webrtc::AudioDeviceModule::Create(webrtc::AudioDeviceModule::kPlatformDefaultAudio, factory);
        if (!underlying || underlying->Init() != 0) _Exit(5); // Never let tgcalls take its default fallback.
        return rtc::make_ref_counted<StrictADM>(underlying);
    };
    }
    return d;
}
}

int main(int argc, char **argv) {
    if (argc == 2 && std::string(argv[1]) == "--pcm-self-test") {
        bool ok = checkPcmAudio();
        std::cout << (ok ? "{\"pcm_callbacks_verified\":true,\"audio_devices_opened\":false,\"call_created\":false}\n" : "{\"pcm_callbacks_verified\":false}\n");
        return ok ? 0 : 1;
    }
    bool allowAudio = argc == 2 && std::string(argv[1]) == "--allow-audio";
    if (argc > 1 && !allowAudio) return 2;
    if (isatty(STDIN_FILENO)) return 2;
    struct rlimit noCore{0, 0}; setrlimit(RLIMIT_CORE, &noCore);
    signal(SIGPIPE, SIG_IGN);
    ipc = dup(STDOUT_FILENO);
    freopen("/dev/null", "w", stdout); freopen("/dev/null", "w", stderr);
    rtc::LogMessage::LogToDebug(rtc::LS_NONE);
    tgcalls::SetLoggingFunction([](const std::string &) {});
    tgcalls::Register<tgcalls::InstanceV2Impl>();
    std::unique_ptr<tgcalls::Descriptor> prepared;
    std::unique_ptr<tgcalls::Instance> call;
    bool pcmMode = false;
    auto pcm = std::make_shared<PcmChannel>();
    pcm->output = [](const std::vector<uint8_t> &bytes) { emit({{"event", "pcm"}, {"data_hex", hex(bytes)}}); };
    bool used = false;
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(240);
    auto stop = [&]() {
        if (!call) return std::string("0");
        const auto relay = std::to_string(call->getPreferredRelayId());
        auto complete = std::make_shared<std::promise<void>>(); auto done = complete->get_future();
        call->stop([complete](tgcalls::FinalState) { complete->set_value(); });
        if (done.wait_for(std::chrono::seconds(5)) != std::future_status::ready) { emit({{"event", "state"}, {"state", "failed"}}); _Exit(6); }
        call.reset(); pcm->clear(); return relay;
    };
    while (std::chrono::steady_clock::now() < deadline) {
        if (call && (pcmMode ? pcm->failed.load() : !bridge_devices_alive())) { emit({{"event", "state"}, {"state", "failed"}}); stop(); break; }
        struct pollfd input{STDIN_FILENO, POLLIN, 0};
        if (poll(&input, 1, 100) <= 0) {
            if (call && (pcmMode ? pcm->failed.load() : !bridge_devices_alive())) { emit({{"event", "state"}, {"state", "failed"}}); stop(); break; }
            continue;
        }
        std::string line;
        if (!std::getline(std::cin, line)) break;
        int id = 0;
        try {
            if (line.size() > 1024 * 1024) throw std::runtime_error("oversize_request");
            std::string error; auto request = Json::parse(line, error);
            if (!error.empty() || !request.is_object()) throw std::runtime_error("invalid_json");
            id = request["id"].int_value();
            auto op = requiredString(request, "op", 32);
            if (op == "devices") {
                Json::array devices;
                for (const auto &d : bridge_list_devices()) devices.push_back(Json::object{
                    {"uid", d.uid}, {"name", d.name}, {"input", d.input}, {"output", d.output}});
                emit({{"id", id}, {"ok", true}, {"devices", devices}, {"audio_opened", false}});
            } else if (op == "prepare") {
                if (prepared || used) throw std::runtime_error("session_already_used");
                prepared = std::make_unique<tgcalls::Descriptor>(descriptor(request["descriptor"], pcm));
                pcmMode = request["descriptor"]["audio_mode"].string_value() == "pcm16";
                emit({{"id", id}, {"ok", true}, {"prepared", true}, {"audio_opened", false}, {"call_created", false},
                      {"relay_count", int(prepared->rtcServers.size())}, {"key_bytes", 256}, {"audio_only", true}});
            } else if (op == "start") {
                if (!allowAudio) throw std::runtime_error("audio_test_not_authorized");
                if (!prepared || used) throw std::runtime_error("not_prepared");
                used = true;
                if (!pcmMode && (!bridge_set_devices(prepared->initialInputDeviceId, prepared->initialOutputDeviceId) || !bridge_devices_alive()))
                    throw std::runtime_error("exact_audio_devices_unavailable");
                call = tgcalls::Meta::Create("12.0.0", std::move(*prepared)); prepared.reset();
                if (!call) throw std::runtime_error("native_create_failed");
                deadline = std::chrono::steady_clock::now() + std::chrono::seconds(180);
                emit({{"id", id}, {"ok", true}, {"started", true}});
            } else if (op == "pcm") {
                if (!call || !pcmMode) throw std::runtime_error("media_not_started");
                if (!pcm->push(unhex(requiredString(request, "data_hex", 128000))))
                    throw std::runtime_error("pcm_buffer_rejected");
                emit({{"id", id}, {"ok", true}});
            } else if (op == "pcm-clear") {
                if (!pcmMode) throw std::runtime_error("media_not_started");
                pcm->clear(); emit({{"id", id}, {"ok", true}});
            } else if (op == "signal") {
                if (!call) throw std::runtime_error("media_not_started");
                call->receiveSignalingData(unhex(requiredString(request, "data_hex", 1024 * 1024)));
                emit({{"id", id}, {"ok", true}});
            } else if (op == "stop") {
                const auto relay = stop(); prepared.reset(); used = true;
                emit({{"id", id}, {"ok", true}, {"stopped", true}, {"relay_id", relay}});
            } else if (op == "status") {
                emit({{"id", id}, {"ok", true}, {"prepared", bool(prepared)}, {"call_created", bool(call)}, {"audio_authorized", allowAudio},
                    {"audio_mode", pcmMode ? "pcm16" : "devices"}, {"pcm_input_frames", double(pcm->inputFrames)}, {"pcm_output_frames", double(pcm->outputFrames)}});
            } else throw std::runtime_error("unknown_operation");
        } catch (const std::exception &error) {
            // Only fixed diagnostic codes, never arbitrary exception/request text.
            const std::set<std::string> codes{
                "invalid_field", "invalid_binary", "unsupported_version_or_direction", "explicit_uids_required",
                "missing_privacy_policy", "invalid_custom_parameters", "invalid_relay_set", "invalid_relay",
                "empty_relay", "invalid_reflector", "oversize_request", "invalid_json", "session_already_used",
                "audio_test_not_authorized", "not_prepared", "exact_audio_devices_unavailable",
                "native_create_failed", "media_not_started", "unknown_operation", "invalid_pcm_endpoints", "invalid_audio_mode", "pcm_buffer_rejected"};
            const std::string code = codes.count(error.what()) ? error.what() : "request_rejected";
            emit({{"id", id}, {"ok", false}, {"error", code}});
        }
    }
    stop();
    return 0;
}
