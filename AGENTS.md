# Projektregeln

- Deutsch mit korrekten Umlauten; nur normale Bindestriche verwenden.
- Ziel ist Original-Voice der installierten Codex-App in der richtigen Task. Keine separate Realtime-API-Session als Ersatz implementieren.
- Build und Offline-Prüfungen dürfen laufende Voice-Gespräche nicht beeinflussen.
- Audio-Capture, Wiedergabe, Routenänderungen und echte Anrufe sind eigenständige bewusste Laufzeitaktionen. `make check` muss ohne sie auskommen.
- Keine Zugangsdaten, Aufnahmen, Installer, App-Bundles oder proprietären Quellcodekopien versionieren.
- Historische Messungen, Nutzerbestätigung und offene Gates klar unterscheiden.
