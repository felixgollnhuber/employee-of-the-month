# Mitarbeiter des Monats - Codex Phone Bridge

Ein lokaler Prototyp, der bei Rückfragen aus T3 Code über Telegram anrufen und mit **GPT-Live 1** sprechen kann. Die Arbeitsaufgaben bleiben in T3. Ein separater Gesprächs-Thread ordnet die Telefonantwort ein und gibt sie an die ursprüngliche Rückfrage zurück.

## Stand: 11. September 2026

Der Nutzer hat den Wechsel von Original-Desktop-Voice zur direkten GPT-Live-API ausdrücklich gewählt. Die Stimme ist auf Cedar eingestellt; die API hat diese Konfiguration angenommen. Für den aktuellen Audioweg sind **weder Loopback noch BlackHole noch eine laufende Codex-Desktop-Voice-Sitzung erforderlich**.

- **Echtes Gespräch bestätigt:** Telegram-Anruf verbunden, PCM in beide Richtungen übertragen, Nutzer hat mit dem Agenten gesprochen. Nach manuellem Auflegen wurden Telegram und GPT-Live bestätigt beendet.
- **T3-Rückgabe praktisch geprüft:** Ein eigener T3-Gesprächs-Thread hat eine synthetische bestätigte Testantwort an die offene Rückfrage eines Test-Threads zurückgegeben. T3 bestätigte die Auflösung. Die beiden Test-Threads wurden anschließend archiviert.
- **Gesamtprobe mit T3 erfolgreich:** Im echten Telefonat kamen Blau und die spätere Korrektur Grün vor. Die bestätigte Rückgabe enthielt ausschließlich `testfarbe: Grün`; die T3-Testaufgabe wurde fortgesetzt. Die Auflegebitte wurde erkannt und Telegram sowie GPT-Live wurden bestätigt beendet.
- **100 Offline-Tests bestanden**, ebenso neun native Descriptor-/IPC-Prüfungen und der echte PCM-Callback-Test ohne Audiogeräte oder Anruf.
- **PeakShare-Pilot pausiert:** Auf Nutzerwunsch wird der automatische Dreiminutenablauf zunächst in einem eigenen Demo-Projekt geprüft. Der PeakShare-Wächter wurde beendet; der laufende PeakShare-Arbeits-Thread bleibt unverändert. Es ist kein dauerhaft installierter Dienst aktiv.

Der manuell gestartete Gesamttest aus T3-Rückfrage, Telefongespräch, bestätigter Antwort, T3-Fortsetzung und Auflegen ist erfolgreich. Der automatische Projektwächter wurde noch nicht als dauerhaft laufender Dienst erprobt. Der Prototyp ist kein fertig ausgerollter Dienst.

## Audioweg

```text
Telegram am iPhone <-> TDLib + tgcalls <-> PCM16, mono, 16 kHz <-> GPT-Live 1
                                               |
                         Kontext und bestätigte Antworten
                                               |
                        T3-Gesprächs-Thread -> T3-Arbeits-Thread
```

Der native Audioadapter liefert und empfängt PCM über private Prozess-Pipes. Er öffnet in diesem Modus keine macOS-Audiogeräte. Der alte Geräteadapter bleibt für historische Versuche vorhanden, wird im GPT-Live-Pfad aber nicht verwendet. Beide Wege verwenden echte Telegram-Signalisierung und die gebaute Medienbibliothek.

## Build und Offline-Prüfung

Aus dem jeweiligen Quellcheckout:

```sh
make check
make live-deps
make telegram-runtime
```

`make check` benötigt Python 3, Swift und die Xcode Command Line Tools. Es startet keine Netzverbindung, Audioaufnahme, Wiedergabe oder Anrufe. `make live-deps` installiert die gepinnte WebSocket-Abhängigkeit unter `.build/live-venv`. `make telegram-runtime` baut die gepinnten Telegram-Abhängigkeiten und prüft den nativen Code ohne Anruf oder Hardware-Audio; dabei sind Downloads möglich.

Auf diesem Mac bleibt der geprüfte Laufzeitbuild im separaten Worktree `../codex-phone-bridge-telegram`. Folgearbeiten können im Hauptcheckout stattfinden. [Übergabe für Issue #1](docs/issue-1-handoff.md) beschreibt die verfügbaren Bausteine und die Abstimmung mit einem aktiven Wächter.

## Lokale Konfiguration

Alle privaten Daten liegen außerhalb des Repositorys unter `~/Library/Application Support/CodexPhoneBridge/telegram/default/`, in privaten Dateien mit Modus 0600:

- `config.json`: eigene Telegram-API-Daten und bewusst gewähltes Absenderkonto.
- `target.json`: bestätigtes persönliches Telegram-Empfängerkonto.
- `database/` und `key-salt`: verschlüsselte TDLib-Sitzung.
- `database-keychain.json`: Bindung an den im macOS-Schlüsselbund gespeicherten Datenbankschlüssel; enthält selbst keinen Schlüssel.
- `live.json`: eigener OpenAI-Projekt-API-Key und optional `voice` (aktuell `cedar`).
- `t3.json`: T3-Origin und Verweis auf eine lokale private T3-Zugangsdatei. Der T3-Zugang ist vom OpenAI-Key unabhängig.

Auf diesem Mac sind diese Angaben vorhanden. Ein zukünftiges Setup kann `configure`, `configure-target`, `configure-live` und anschließend `login` verwenden. Bestehende Konfiguration und Datenbank nicht neu anlegen. Die bestehende Datenbank kann inzwischen automatisch mit ihrem Schlüssel aus dem macOS-Schlüsselbund geöffnet werden. Dies wurde auf ausdrücklichen Nutzerwunsch eingerichtet und in einem neuen Prozess ohne Eingabedialog überprüft. Die Passphrase selbst wurde nicht als Klartext gespeichert.

Für ein zukünftiges einmaliges Einrichten nach erfolgreichem Login:

```sh
make keychain-helper
.build/live-venv/bin/python -m telegram_bridge remember-login --passphrase-dialog
```

Der Schlüssel wird erst nach Prüfung der Datenbank und des Telegram-Absenders gespeichert. Der kleine Keychain-Helfer liegt an einem stabilen privaten Installationspfad außerhalb des Checkouts. Einzelanrufe und der Wächter laden den Schlüssel danach ohne Passphrase-Abfrage. Ist der Schlüsselbund gesperrt oder der Eintrag nicht verfügbar, bricht die Brücke mit einem Fehler ab, statt wiederholt nach der Passphrase zu fragen. `make check` greift nicht auf den echten Schlüsselbund zu.

```sh
.build/live-venv/bin/python -m telegram_bridge preflight
```

Die Vorprüfung meldet ausschließlich lokale Voraussetzungen. `ready_for_call_attempt=true` behauptet keine gerade verifizierte Telegram-Anmeldung oder verfügbare API-Quote.

## Bewusste Laufzeitaktionen

Ein einzelnes Gespräch, maximal 120 Sekunden ab Anrufstart zuzüglich begrenzter Aufräumzeit:

```sh
.build/live-venv/bin/python -m telegram_bridge voice-test \
  --allow-call --seconds 120 --passphrase-dialog
```

Die vorhandene Telegram-Sitzung wird über den Schlüsselbund geöffnet. Der Dialog dient nur noch als Ausweichweg für Profile, bei denen die einmalige Einrichtung noch nicht erfolgt ist. GPT-Live wird erst bei `callStateReady` gestartet. Nichtabheben startet daher keine GPT-Live-Sitzung. Der Nutzer kann am Handy auflegen oder eine kurze direkte Auflegebitte sagen. Negierte Bitten wie „nicht auflegen“ lösen kein Ende aus. Spracherkennung und natürliche Formulierungen bleiben praktisch zu erproben; der harte Zeitrahmen bleibt aktiv.

Eine bestimmte offene T3-Rückfrage mit dem Gespräch verbinden:

```sh
.build/live-venv/bin/python -m telegram_bridge voice-test \
  --allow-call --seconds 120 --passphrase-dialog \
  --t3-thread THREAD_ID --t3-request REQUEST_ID
```

Optional `--context-file DATEI` mit einer bewusst gewählten kurzen Zusammenfassung ergänzen. Ohne diese Datei werden Aufgabentitel, Rückfrage und ein begrenzter Ausschnitt der letzten Aufgaben-Nachrichten verwendet. Tool-Ausgaben werden ausgelassen und gängige Schlüsselformate ausgeblendet.

Eine Rückfrage des Anrufers ist eine eigene Antwortart und keine fachliche Entscheidung. Nach der Weitergabe wartet die Brücke auf die Reaktion der Arbeitsaufgabe und bringt deren Erläuterung ins Gespräch zurück. Eine neue Ask-Frage derselben Aufgabe wird übernommen. Wenn der Arbeits-Thread ohne weitere Ask-Frage beendet wurde, kann eine spätere bestätigte Entscheidung als normale Folgeeingabe an denselben Thread gehen; erledigte Ask-IDs werden dafür nicht wiederverwendet. Unterbrochene Arbeit wird dabei nicht automatisch neu gestartet.

## Begrenzter T3-Anrufwächter

```sh
.build/live-venv/bin/python -m telegram_bridge watch-t3 \
  --allow-calls --project-id PROJECT_ID \
  --max-calls 1 --watch-seconds 900 --seconds 120 --question-delay-seconds 180
```

Der Beispielbefehl beobachtet 15 Minuten lang genau das ausgewählte T3-Projekt und startet höchstens einen Anruf. Die Wartezeit zählt ab dem ursprünglichen Fragezeitpunkt und bleibt auch nach einem Neustart korrekt. Vor dem Anruf wird erneut geprüft, ob die Frage noch offen ist. Mit `--question-delay-seconds 0` ist ein bewusst gewählter Sofortmodus möglich. Bei eingerichteter Schlüsselbund-Anmeldung ist auch beim Wächter keine Passphrase-Eingabe nötig. Bei älteren, noch nicht eingerichteten Profilen bleibt eine eingegebene Passphrase nur für diesen begrenzten Lauf im Speicher. Versuche werden vor dem Wählen ohne Gesprächsinhalte in `watch-attempts.json` vermerkt. Ein Neustart versucht dieselbe Rückfrage nicht erneut. Nach Nichtabheben oder Unterbrechung bleibt eine offene Frage offen; es werden noch keine Telegram-Nachrichten versendet.

Der PeakShare-Pilot wurde am 11. September 2026 um 01:49 Uhr (Europe/Vienna) mit einer Grenze bis 05:49 Uhr gestartet und anschließend auf Nutzerwunsch vorzeitig pausiert, um zunächst ein separates Beispiel zu prüfen. Seine PID, Laufzeitgrenzen und der private Logpfad stehen lokal in `peakshare-pilot.json`. GPT-Live wird während des Wartens nicht gestartet und verbraucht dabei keine Voice-Minuten.

Der Wächter prüft derzeit per begrenzten HTTP-Abfragen. Er ist noch kein installierter Systemdienst und reagiert auf echte T3-Nutzerrückfragen, nicht automatisch auf beliebige Fehlertexte oder Berechtigungsfreigaben.

## Kosten und Zugang

GPT-Live 1 benötigt einen eigenen OpenAI-API-Key und wird separat vom ChatGPT-Abo abgerechnet: derzeit 0,05 US-Dollar pro aktiver Sitzungsminute, sekundengenau. Die Gesprächskoordination läuft über den bestehenden T3-Providerzugang. Der Code konfiguriert keine zusätzlichen Responses-API-Modelle. [OpenAI-Preise](https://developers.openai.com/api/docs/pricing), [Client-Delegation](https://developers.openai.com/api/docs/guides/live-delegation).

## Weitere Dokumentation

- [Telegram-Folgedialoge, Rückrufe und Statusanrufe mit serve-t3](docs/telegram-service.md).
- [T3-Integration und Abo-/API-Entscheidung](docs/t3-voice-integration.md).
- [Nachweise und Aussagegrenzen](docs/evidence.md).
- [Historischer Telegram-/Desktop-Aufbau](docs/telegram-control.md).
- [Historische manuelle Tests mit virtuellen Audiogeräten](docs/manual-test.md).
- [Issue #1: Telegram-Folgedialog, Rückrufe und eingehende Statusanrufe](https://github.com/felixgollnhuber/codex-phone-bridge/issues/1). Für später festgehalten, noch nicht implementiert.

Keine Zugangsdaten, Tonaufnahmen, Installer, App-Bundles, fremden Quellcodekopien oder vorgefertigten Binärdateien werden versioniert. Für eine mögliche Veröffentlichung wurde noch keine eigene Gesamtlizenz gewählt.
