#!/usr/bin/env python3
"""Check generated fixture and invalid CLI input; never open an audio device."""
import struct
import subprocess
import wave

with wave.open('.build/test-tone.wav', 'rb') as tone:
    assert (tone.getnchannels(), tone.getsampwidth(), tone.getframerate(), tone.getnframes()) == (2, 2, 44100, 22050)
    raw = tone.readframes(tone.getnframes())
    samples = struct.unpack('<' + 'h' * (len(raw) // 2), raw)
    assert samples[::2] == samples[1::2]
    assert 3000 <= max(abs(value) for value in samples) <= 3277
    assert samples[:2] == samples[-2:] == (0, 0)
result = subprocess.run(['.build/AudioBridgeTest', '--invalid'], capture_output=True, text=True)
assert result.returncode == 1 and 'Usage:' in result.stderr
assert not result.stdout
print('Offline checks passed. No audio device accessed.')
