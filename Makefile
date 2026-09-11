.PHONY: build fixture check telegram-check telegram-native telegram-media telegram-runtime live-deps keychain-helper

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

telegram-media:
	python3 scripts/build-media.py
	python3 -m telegram_bridge media-check

telegram-runtime: telegram-media
	python3 scripts/check-media-runtime.py
	.build/vendor/telegram-ios/bazel-bin/bridge_probe/media_runtime --pcm-self-test

live-deps:
	python3 -m venv .build/live-venv
	.build/live-venv/bin/python -m pip install -r requirements-live.txt

keychain-helper: .build/KeychainStore

.build/KeychainStore: Sources/KeychainStore.swift
	mkdir -p .build
	swiftc Sources/KeychainStore.swift -o .build/KeychainStore
