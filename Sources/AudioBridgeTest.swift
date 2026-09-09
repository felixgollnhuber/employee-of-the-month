import Foundation
import AVFoundation
import AudioToolbox
import CoreAudio

struct Failure: Error, CustomStringConvertible {
    let description: String
    init(_ message: String) { description = message }
}
func check(_ code: OSStatus, _ action: String) throws {
    if code != noErr { throw Failure("\(action): OSStatus \(code)") }
}
func devices() throws -> [(AudioDeviceID, String)] {
    var address = AudioObjectPropertyAddress(mSelector: kAudioHardwarePropertyDevices,
        mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
    var size: UInt32 = 0
    try check(AudioObjectGetPropertyDataSize(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size), "device count")
    var ids = [AudioDeviceID](repeating: 0, count: Int(size) / MemoryLayout<AudioDeviceID>.size)
    try check(AudioObjectGetPropertyData(AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &ids), "device list")
    return try ids.map { id in
        var name: CFString = "" as CFString
        var length = UInt32(MemoryLayout<CFString>.size)
        var property = AudioObjectPropertyAddress(mSelector: kAudioObjectPropertyName,
            mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain)
        try check(AudioObjectGetPropertyData(id, &property, 0, nil, &length, &name), "device name")
        return (id, name as String)
    }
}
func select(_ name: String, in inventory: [(AudioDeviceID, String)]) throws -> AudioDeviceID {
    let found = inventory.filter { $0.1 == name }
    guard found.count == 1 else { throw Failure("Expected exactly one device named \(name); found \(found.count). No fallback.") }
    return found[0].0
}
func bind(_ node: AVAudioIONode, to device: AudioDeviceID) throws {
    guard let unit = node.audioUnit else { throw Failure("Missing audio unit") }
    var value = device
    try check(AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
        kAudioUnitScope_Global, 0, &value, UInt32(MemoryLayout<AudioDeviceID>.size)), "bind device")
    var actual: AudioDeviceID = 0
    var size = UInt32(MemoryLayout<AudioDeviceID>.size)
    try check(AudioUnitGetProperty(unit, kAudioOutputUnitProperty_CurrentDevice,
        kAudioUnitScope_Global, 0, &actual, &size), "read back device")
    guard actual == device else { throw Failure("Audio device readback mismatch") }
}

// Only aggregate levels leave the audio callback. No samples or recordings are saved.
final class Meter {
    private let lock = NSLock()
    private var samples = 0
    private var sum = 0.0
    private var peak: Float = 0
    func add(_ buffer: AVAudioPCMBuffer) {
        guard let data = buffer.floatChannelData else { return }
        var total = 0.0
        var maximum: Float = 0
        for c in 0..<Int(buffer.format.channelCount) {
            for i in 0..<Int(buffer.frameLength) {
                let v = data[c][i]
                total += Double(v * v)
                maximum = max(maximum, abs(v))
            }
        }
        lock.lock()
        samples += Int(buffer.frameLength) * Int(buffer.format.channelCount)
        sum += total
        peak = max(peak, maximum)
        lock.unlock()
    }
    func take() -> (Int, Double, Float) {
        lock.lock(); defer { lock.unlock() }
        let result = (samples, samples > 0 ? sqrt(sum / Double(samples)) : 0, peak)
        samples = 0; sum = 0; peak = 0
        return result
    }
}

do {
    let args = CommandLine.arguments
    guard args.count >= 2, ["--check-devices", "--prepare", "--voice-ready", "--transport-test", "--meter-return", "--meter-input"].contains(args[1]) else {
        throw Failure("Usage: AudioBridgeTest --check-devices | --prepare phrase.aiff | --voice-ready phrase.aiff")
    }
    let inventory = try devices()
    let outputID = try select("PHONE_TO_CODEX", in: inventory)
    let inputName = ["--transport-test", "--meter-input"].contains(args[1]) ? "PHONE_TO_CODEX" : "CODEX_TO_PHONE"
    let inputID = try select(inputName, in: inventory)
    print("Output: PHONE_TO_CODEX [\(outputID)]; receiver: \(inputName) [\(inputID)]")
    if args[1] == "--check-devices" { exit(0) }
    guard args.count == 3 else { throw Failure("Exactly one local test phrase file required") }
    let file = try AVAudioFile(forReading: URL(fileURLWithPath: args[2]))
    let seconds = Double(file.length) / file.processingFormat.sampleRate
    guard seconds > 0 && seconds <= 10 else { throw Failure("Test phrase must be at most 10 seconds") }
    let send = AVAudioEngine()
    let receive = AVAudioEngine()
    try bind(send.outputNode, to: outputID)
    try bind(receive.inputNode, to: inputID)
    let inputFormat = receive.inputNode.outputFormat(forBus: 0)
    let outputFormat = send.outputNode.inputFormat(forBus: 0)
    guard inputFormat.channelCount > 0, outputFormat.channelCount > 0 else { throw Failure("Device has no usable channels") }
    print("Receiver format: \(inputFormat); output format: \(outputFormat)")
    let player = AVAudioPlayerNode()
    send.attach(player)
    send.connect(player, to: send.mainMixerNode, format: file.processingFormat)
    send.prepare()
    if args[1] == "--prepare" {
        print("Graphs configured and devices read back. No engine started; no playback or input capture.")
        exit(0)
    }
    // Transport test uses the same virtual device on both ends; return meter never plays.
    let meter = Meter()
    receive.inputNode.installTap(onBus: 0, bufferSize: 2048, format: inputFormat) { buffer, _ in meter.add(buffer) }
    defer { player.stop(); send.stop(); receive.stop(); receive.inputNode.removeTap(onBus: 0) }
    try receive.start()
    if !args[1].hasPrefix("--meter-") { try send.start() }
    try bind(send.outputNode, to: outputID)
    try bind(receive.inputNode, to: inputID)
    player.scheduleFile(file, at: nil)
    let start = ProcessInfo.processInfo.systemUptime
    var totalSamples = 0
    var signalWindows = 0
    let duration = args[1] == "--voice-ready" ? 20 : 6
    for tick in 0..<duration {
        if tick == 2 && !args[1].hasPrefix("--meter-") { player.play(); print("TEST_INPUT_STARTED at 2 s; phrase duration \(seconds) s") }
        RunLoop.current.run(until: Date().addingTimeInterval(1))
        let (n, rms, peak) = meter.take()
        totalSamples += n
        if rms > 0.001 { signalWindows += 1 }
        print(String(format: "t=%.1f samples=%d rms=%.6f peak=%.6f", ProcessInfo.processInfo.systemUptime - start, n, rms, peak))
        fflush(stdout)
    }
    print("DONE samples=\(totalSamples) windowsAboveMinus60dBFS=\(signalWindows). Levels alone do not prove speech content or context.")
    if totalSamples == 0 { throw Failure("Receiver produced no samples") }
} catch {
    fputs("ERROR: \(error)\n", stderr)
    exit(1)
}
