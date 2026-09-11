#pragma once
#include <cstdint>
#include <string>
#include <vector>
struct BridgeDevice { std::string uid, name; bool input, output; };
std::vector<BridgeDevice> bridge_list_devices();
bool bridge_set_devices(const std::string &input, const std::string &output);
bool bridge_devices_alive();
extern "C" bool bridge_resolve_audio_device(bool input, uint32_t *result);
