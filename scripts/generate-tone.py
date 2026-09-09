#!/usr/bin/env python3
"""Generate a neutral test signal without accessing audio devices."""
import math
from pathlib import Path
import struct
import sys
import wave


def generate(path):
    rate = 44100
    count = rate // 2
    ramp = round(rate * 0.01)
    frames = bytearray()
    for i in range(count):
        envelope = min(1.0, i / ramp, (count - 1 - i) / ramp)
        sample = round(32767 * 0.1 * envelope * math.sin(2 * math.pi * 440 * i / rate))
        frames.extend(struct.pack('<hh', sample, sample))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames)


if __name__ == '__main__':
    generate(Path(sys.argv[1] if len(sys.argv) > 1 else '.build/test-tone.wav'))
