# Changelog

All notable changes will be documented in this file. The project follows [Semantic Versioning](https://semver.org/) once the first release is published.

## Unreleased

- Reject malformed private JSON and credential field types without crashing the offline preflight.
- Reject symlink T3 credentials and named pipes before reading their contents.
- Test Python 3.11 and 3.14 in CI with installed package dependencies and a CLI smoke test outside the source tree.
- Update the T3 setup guide and the single-file test command.
- Update the pinned `websockets` dependency to 17.1.

## 0.1.0 - 2026-09-17

Initial public preview for macOS on Apple Silicon.

- Renamed the project to Employee of the Month and the Python package and command to `eotm`.
- Added English and German conversation configuration.
- Added packaging metadata, CI, contribution guidance, a security policy and issue templates.
- Rewrote the public documentation for installation, configuration, operation, architecture and troubleshooting.
- Preserved existing private profiles and installed services through explicit legacy-path compatibility.
