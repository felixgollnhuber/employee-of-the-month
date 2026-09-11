# Arbeitsstand für Issue #1

Issue: https://github.com/felixgollnhuber/codex-phone-bridge/issues/1

## Bereits vorhanden

- Echte ausgehende Telegram-Anrufe mit direktem GPT-Live-1-Audio, ohne virtuelle macOS-Audiogeräte.
- Cedar als konfigurierte Stimme; Persona „Mitarbeiter des Monats“.
- Telegram-Datenbankschlüssel im macOS-Schlüsselbund. Keine erneute Passphrase-Eingabe bei normalem Betrieb.
- T3-Ask-Anbindung mit dreiminütiger Verzögerung, fester Thread-/Rückfrage-Zuordnung, Rückfragen und Übernahme neuer Ask-Fragen.
- Unterscheidung zwischen übergebener Rückfrage und bestätigter fachlicher Entscheidung.
- Ein Projektwächter erfasst auch neu angelegte Threads dieses T3-Projekts. Der Arbeits-Thread benötigt dafür keine Telefonlogik.
- 100 Offline-Tests. Echte Telefongespräche, Auflegen und Rückfragenkette wurden geprüft; die genauen Aussagegrenzen stehen in `docs/evidence.md`.

## Noch umzusetzen

Die in Issue #1 beschriebenen Telegram-Folgenachrichten bei Nichterreichbarkeit, mehrteilige Textdialoge, Rückrufe und allgemein eingehende Statusanrufe sind noch nicht implementiert. Der vorhandene `T3Delegation`-Dialog ist ein Ausgangspunkt, ersetzt aber keinen eingehenden Telegram-Dienst oder dauerhaft gespeicherten Gesprächszustand.

## Laufzeit und Entwicklungsstand

Die geprüfte Laufzeit mit kompiliertem TDLib-/tgcalls-Stack und Python-Umgebung liegt auf diesem Mac im separaten Worktree `../codex-phone-bridge-telegram`. Ein dort gestarteter Wächter kann während der Arbeit im Hauptcheckout weiterlaufen, ohne dessen Änderungen automatisch zu laden. Der aktuelle Quellstand wird auch im Hauptcheckout bereitgestellt.

Private Konfiguration liegt unter `~/Library/Application Support/CodexPhoneBridge/telegram/default/`. Der Datensatz `project-pilot.json` dokumentiert den zuletzt gestarteten Wächter dieses Projekts einschließlich dessen beim Start erfasster PID und Logpfad. `peakshare-pilot.json` gehört zu einem anderen, pausierten Versuch. Keine Schlüssel ausgeben, kopieren oder versionieren.

**Ein TDLib-Client pro Profil:** Die vorhandenen Anrufe verwenden `session.lock`. Ein neuer dauerhaft angemeldeter Dienst für eingehende Anrufe muss den bisherigen Anrufpfad integrieren beziehungsweise geordnet übernehmen. Vor einem konkurrierenden Live-Test den konkret dokumentierten eigenen Wächter prüfen und gezielt beenden; keine Prozesse nach Namensmustern abschießen. Der Keychain-Helfer liegt an einem stabilen Installationspfad außerhalb des Checkouts und wird durch normale Builds nicht ersetzt.

`make check` bleibt vollständig ohne Anrufe, API-Sitzungen, Audiozugriff oder echten Schlüsselbundzugriff. Native Builds und Tests dürfen ein laufendes Gespräch nicht beeinflussen. Für neue T3-Testprojekte vor dem Erstellen der T3-Threads ein eigenes Git-Repository mit initialem Commit anlegen, damit Checkpoints funktionieren.

## Dialogsemantik

Eine T3-Empfangsbestätigung bedeutet nur, dass eine Nutzereingabe angekommen ist. Eine Rückfrage wie „Welcher Bericht ist gemeint?“ darf keine abgeschlossene Formatentscheidung vortäuschen. Die Erklärung aus dem Arbeits-Thread muss zurück ins Gespräch gelangen. Bei Auflegen oder Nichtabheben verbleibende Fragen bleiben offen und bilden den Ausgangspunkt für den Folgedialog in Issue #1.
