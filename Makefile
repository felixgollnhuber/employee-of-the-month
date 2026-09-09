.PHONY: build fixture check telegram-check telegram-native

build: .build/AudioBridgeTest

.build/AudioBridgeTest: Sources/AudioBridgeTest.swift
	mkdir -p .build
	swiftc Sources/AudioBridgeTest.swift -o .build/AudioBridgeTest

fixture:
	python3 scripts/generate-tone.py .build/test-tone.wav

check: build fixture
	python3 scripts/check-offline.py
	python3 -m unittest discover -s tests -v

telegram-check:
	python3 -m unittest discover -s tests -v
	python3 -m telegram_bridge demo

telegram-native:
	python3 scripts/build-tdlib.py
	python3 -m telegram_bridge native-check
