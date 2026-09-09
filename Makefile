.PHONY: build fixture check

build: .build/AudioBridgeTest

.build/AudioBridgeTest: Sources/AudioBridgeTest.swift
	mkdir -p .build
	swiftc Sources/AudioBridgeTest.swift -o .build/AudioBridgeTest

fixture:
	python3 scripts/generate-tone.py .build/test-tone.wav

check: build fixture
	python3 scripts/check-offline.py
