# Projektregeln

- Deutsch mit korrekten Umlauten; nur normale Bindestriche verwenden.
- Gewählter Weg seit 11. September 2026: GPT-Live 1 direkt über die OpenAI-API, mit Telegram-Audio und Rückgabe an den zugehörigen T3-Thread. Der Nutzer hat die zusätzliche Voice-Abrechnung akzeptiert. Die frühere Bindung an Original-Desktop-Voice ist damit ersetzt. Kein stiller Wechsel auf Realtime oder TTS.
- Build und Offline-Prüfungen dürfen laufende Voice-Gespräche nicht beeinflussen.
- Audio-Capture, Wiedergabe, Routenänderungen und echte Anrufe sind eigenständige bewusste Laufzeitaktionen. `make check` muss ohne sie auskommen.
- Keine Zugangsdaten, Aufnahmen, Installer, App-Bundles oder proprietären Quellcodekopien versionieren.
- Historische Messungen, Nutzerbestätigung und offene Gates klar unterscheiden.
- Für Arbeiten an Issue #1 zuerst `docs/issue-1-handoff.md` lesen. Dort sind vorhandene Bausteine, die getrennte Laufzeit und die exklusive Telegram-Profilsitzung beschrieben.
