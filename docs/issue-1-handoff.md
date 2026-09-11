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

## Implementierung von Issue #1

`serve-t3` ergänzt Telegram-Folgenachrichten, mehrteilige Textdialoge, Rückrufe und eingehende Statusanrufe. Vorgänge und Versandzustand werden privat und dauerhaft gespeichert. Der gemeinsame Dienst hält `watch.lock` und `session.lock` und übernimmt bekannte Kontaktversuche des bisherigen Wächters. Verhalten, Start und Fehlerfälle stehen in [telegram-service.md](telegram-service.md).

Die Implementierung wurde mit 127 Offline-Tests, elf nativen Descriptor-/IPC-Prüfungen und dem nativen PCM-Selbsttest geprüft. Eine neue native Laufzeit wurde separat kompiliert und gelinkt. Im anschließend ausdrücklich beauftragten Live-Test wurden Folgenachricht, Text-Rückfrage, eingehender Rückruf, eindeutige T3-Rückgabe und Auflegen gemeinsam geprüft. Auch ein neuer Statusanruf wurde geprüft. Der Nutzer bestätigte Verständlichkeit, Auflegen und Statusauskunft. Der eigene Wächter wurde dafür gezielt pausiert und nach dem Test bis zum ursprünglichen Endzeitpunkt wiederhergestellt; seine native Laufzeit blieb unverändert. Eine beim Dienstende noch unbestätigte Statusnachricht zeigt einen offenen Punkt beim Leeren der Versandwarteschlange. Die noch offenen Live-Fälle und die genaue Evidenz stehen in [evidence.md](evidence.md).

## Laufzeit und Entwicklungsstand

Die geprüfte Laufzeit mit kompiliertem TDLib-/tgcalls-Stack und Python-Umgebung liegt auf diesem Mac im separaten Worktree `../codex-phone-bridge-telegram`. Ein dort gestarteter Wächter kann während der Arbeit im Hauptcheckout weiterlaufen, ohne dessen Änderungen automatisch zu laden. Der aktuelle Quellstand wird auch im Hauptcheckout bereitgestellt.

Private Konfiguration liegt unter `~/Library/Application Support/CodexPhoneBridge/telegram/default/`. Der Datensatz `project-pilot.json` dokumentiert den zuletzt gestarteten Wächter dieses Projekts einschließlich dessen beim Start erfasster PID und Logpfad. `peakshare-pilot.json` gehört zu einem anderen, pausierten Versuch. Keine Schlüssel ausgeben, kopieren oder versionieren.

**Ein TDLib-Client pro Profil:** Die vorhandenen Anrufe verwenden `session.lock`. Ein neuer dauerhaft angemeldeter Dienst für eingehende Anrufe muss den bisherigen Anrufpfad integrieren beziehungsweise geordnet übernehmen. Vor einem konkurrierenden Live-Test den konkret dokumentierten eigenen Wächter prüfen und gezielt beenden; keine Prozesse nach Namensmustern abschießen. Der Keychain-Helfer liegt an einem stabilen Installationspfad außerhalb des Checkouts und wird durch normale Builds nicht ersetzt.

`make check` bleibt vollständig ohne Anrufe, API-Sitzungen, Audiozugriff oder echten Schlüsselbundzugriff. Native Builds und Tests dürfen ein laufendes Gespräch nicht beeinflussen. Für neue T3-Testprojekte vor dem Erstellen der T3-Threads ein eigenes Git-Repository mit initialem Commit anlegen, damit Checkpoints funktionieren.

## Dialogsemantik

Eine T3-Empfangsbestätigung bedeutet nur, dass eine Nutzereingabe angekommen ist. Eine Rückfrage wie „Welcher Bericht ist gemeint?“ darf keine abgeschlossene Formatentscheidung vortäuschen. Die Erklärung aus dem Arbeits-Thread muss zurück ins Gespräch gelangen. Bei Auflegen oder Nichtabheben verbleibende Fragen bleiben offen und bilden den Ausgangspunkt für den Folgedialog in Issue #1.

## Aktueller gemeinsamer Betrieb

Auf ausdrücklichen Nutzerauftrag läuft nun ein gemeinsamer LaunchAgent für alle T3-Projekte einschließlich PeakShare. Er ersetzt die einzelnen Wächter. Neue Feature-Aufträge können nach Bestätigung als T3-Threads mit vorgeschlagenem Account, Modell und Reasoning gestartet werden. `service-health.json` und `service-pilot.json` im privaten Profil sind die aktuellen Laufzeitquellen. Die bisherigen Pilotdateien sind als durch den gemeinsamen Dienst abgedeckt gekennzeichnet. Siehe [all-projects-service.md](all-projects-service.md).

Der Dienst besitzt jetzt ein privates `call-history.json` mit laufend gespeicherten Textverläufen und Kontext für Rückrufe. Gespeicherte unbestätigte Vorschläge können unter derselben ID erneut vorgelesen und erst mit einer neuen aktuellen Bestätigung gestartet werden. Ein partieller Verlauf des letzten helth-Gesprächs wurde eindeutig aus T3 wiederhergestellt. Siehe den Abschnitt Kontext beim Rückruf in `all-projects-service.md`.

## Explizite Folgenachrichten an bestehende Threads

Der neue getrennte Versandpfad unterstützt ausdrücklich adressierte Folgenachrichten auch an completed und settled Threads. Eindeutige Titel oder Thread-IDs, offene Rückfragen und dauerhafte Versandreservierungen werden vor der Zustellung geprüft. Formulierungen, Wiederholungsregeln und die Grenzen des Offline-Nachweises stehen in [thread-followups.md](thread-followups.md). Die aktualisierte feste Dienstinstallation wurde auf gesonderten Nutzerauftrag aktiviert; der genaue Release- und Laufzeitnachweis steht in der verlinkten Dokumentation.

Beendete Telefongespräche markieren ihre technischen Koordinations-Threads automatisch über das T3-Settled-Feature. Die Zuordnung von Gesprächsende zu Settled-Status, die dauerhafte Vormerkung in `conversations.json` und die verbleibenden Einschränkungen stehen im Abschnitt Settled-Status in `telegram-service.md`. Fachliche Arbeits-Threads sind davon ausgenommen.
