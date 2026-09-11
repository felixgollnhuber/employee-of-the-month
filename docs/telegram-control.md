# Telegram-Steuerung: überprüfte Zwischenimplementierung

**Historische Aufbau- und Prüfnotizen.** Seit der Nutzerentscheidung vom 11. September 2026 verwendet der aktuelle Pfad GPT-Live 1 direkt und benötigt keine Desktop-Voice-Automatisierung oder virtuellen Audiogeräte. Ein echtes Gespräch wurde bestätigt. Aktuelle Befehle und Grenzen stehen in der [README](../README.md), aktuelle Nachweise in [evidence.md](evidence.md). Die nachfolgenden Abschnitte dokumentieren die früheren Zwischenstände.

## Einzelner Klingeltest ab 11. September 2026

**Live-Ergebnis am 11. September 2026:** Ein ausdrücklich angeforderter Test lief gegen das konfigurierte persönliche Empfängerkonto. Die bestehende Sitzung wurde erfolgreich geöffnet und der Absender geprüft. Telegram meldete nacheinander `dialing`, `ringing` mit `delivery_reported=true`, Annahme mit `answer_reported=true`, `ending` und bestätigtes `ended`. Genau ein Testlauf wurde gestartet, Exitcode 0. `audio_opened=false` und `media_connected=false` blieben während des gesamten Tests unverändert. Die Zustell-/Annahme-/Endemeldungen sind echte Telegram-Laufzeitbefunde; eine gesonderte Nutzerbestätigung hörbaren Klingelns oder ein Nachweis von Gesprächsaudio liegt damit nicht vor.

Für den ausdrücklich gewünschten ersten Anruf gibt es jetzt einen eigenen Signaling-Test:

```sh
python3 -m telegram_bridge ring-test --allow-call --seconds 20 --passphrase-dialog
```

Er öffnet die bestehende verschlüsselte Sitzung mit einem verdeckten lokalen macOS-Passphrase-Dialog, prüft Absender und konfigurierten Empfänger und startet genau einen echten Telegram-Anruf. Die Eingabe wird ausschließlich im Prozess verwendet und weder protokolliert noch gespeichert. Ohne `--passphrase-dialog` ist die verdeckte Terminal-Eingabe möglich. Ein fehlender Login löst keine automatische Codeanforderung aus.

Der Test beendet den Anruf beim ersten `callStateExchangingKeys` oder `callStateReady`, sonst nach der vorgegebenen Frist (1 bis 30 Sekunden). Bestätigtes Auflegen wird zusätzlich bis zu fünf Sekunden abgewartet; fehlende Bestätigung bleibt `end_unconfirmed`. Verzögerte Startantworten werden innerhalb dieses Abbruchfensters ebenfalls aufgelegt. `--allow-call` ist erforderlich. `make check` führt diesen Befehl nicht aus.

Dies ist ein Test von Zustellung, Annahme und Auflegen. Es werden keine Audiogeräte, Medieninstanzen oder Codex-Voice-Sitzungen gestartet. Das angebotene Protokoll wird vorher gegen die tatsächlich gebaute Medienbibliothek geprüft; keine Offline-Testbibliothek wird im Telegram-Netz verwendet. `delivery_reported` bedeutet eine Telegram-Zustellmeldung, keine Nutzerbestätigung eines hörbaren Klingelns. `answer_reported` bezeichnet den Übergang zur Schlüssel-/Verbindungsaushandlung, keine funktionierende Sprachverbindung.

Die bisherigen Audio-Test-Gates gelten unverändert für die vollständige Telefonbrücke. 66 Offline-Tests bestanden nach Ergänzung des Klingeltests. Die untenstehenden älteren Aussagen, es gebe keinen CLI-Anrufbefehl, gelten für den damaligen Stand. API-Grundlage: [createCall](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1create_call.html) und [discardCall](https://core.telegram.org/tdlib/docs/classtd_1_1td__api_1_1discard_call.html), abgeglichen mit dem gepinnten lokalen TDLib-Schema.

## Fortsetzung in T3 Code am 10. September 2026

Der bisherige Codex-Thread `01a08826-0a4e-7ae2-b38f-f9c0805d5738` wurde gelesen. Der Nutzer hat die erfolgreiche Telegram-Client-Anmeldung bestätigt. Lokal sind API-Konfiguration und eine Datenbank vorhanden; die bestehende Sitzung wurde bei der Fortsetzung nicht erneut online geöffnet. Die frühere Aussage, es gebe noch keine Datenbank, gilt nicht mehr. Für diese Datenbank ist die bereits gewählte lokale Passphrase erforderlich.

Die Entwicklung läuft im Worktree `codex-phone-bridge-telegram` auf `feat/telegram-control`. Original-Voice bleibt in der installierten Codex-App; der Wechsel der Entwicklungsoberfläche zu T3 Code ersetzt keine Voice-Integration.

Aktuelle Prüfungen: **59 Offline-Tests** einschließlich sechs neuer Tests für die nachträgliche Empfängerkonfiguration bestanden. Die vor dieser Ergänzung erneut ausgeführten TDLib-/Medien-Metadatenchecks und neun nativen Runtime-Prüfungen bestanden ebenfalls. Keine Anmeldung, Wiedergabe, Aufnahme oder Anruf wurde dabei gestartet.

CoreAudio meldet aktuell keines der beiden benötigten Geräte. Die vorhandene Loopback-Konfiguration enthält `PHONE_TO_CODEX` und `CODEX_TO_PHONE`, beide mit `enabled=false`. Diese Einstellung wurde nur gelesen. Die Routen müssen vor einem Live-Test bewusst vorbereitet werden; die frühere Trial-Rauschproblematik bleibt offen.

### Empfänger nach bestehender Anmeldung ergänzen

```sh
python3 -m telegram_bridge configure-target
python3 -m telegram_bridge preflight
```

`configure-target` fragt lokal nach dem bestätigten Telegram-@Benutzernamen des persönlichen Empfängerkontos und speichert ausschließlich diesen in einer privaten `target.json` (0600) neben `config.json`. API-Datei, Datenbank und Salt bleiben unverändert. Ein identischer Empfänger ist wiederholbar, ein bereits anders konfigurierter Empfänger wird nicht überschrieben. Alte Profile mit eingebettetem Empfänger bleiben lesbar; widersprüchliche Ziele werden abgelehnt. Der Befehl fragt Telegram nicht ab und startet keinen Anruf. Die Online-Prüfung der Empfängeridentität erfolgt weiterhin vor dem Anruf im bestehenden Laufzeitpfad.

Der Nutzer hat inzwischen sein persönliches Telegram-Empfängerkonto genannt; dessen Benutzername ist ausschließlich in der privaten lokalen Zielkonfiguration gespeichert. Der Offline-Preflight bestätigt die Zielkonfiguration. Die Online-Prüfung von Identität und Anrufbarkeit steht noch aus.

Noch offen: nutzbarer Audioweg, erster echter Anruf, Original-Voice-Kontextprüfung und automatische Voice-Steuerung. Die folgenden Abschnitte beschreiben die früheren Implementierungs- und Prüfstände; ihre damaligen Aussagen zur fehlenden Anmeldung werden durch die obige Nutzerbestätigung aktualisiert.

## Anmeldung nach zu kurzer lokaler Passphrase erneut starten

Wenn die API-Konfiguration bereits erfolgreich gespeichert wurde, **nur `python3 -m telegram_bridge login` erneut ausführen**. `configure` muss nicht wiederholt werden und überspringt bei vorhandener gültiger privater Konfiguration jetzt alle Secret-Eingaben. Es überschreibt nichts.

Die Mindestlängenprüfung der lokalen Datenbank-Passphrase erfolgt vor Datenbankanlage, TDLib-Initialisierung und Codeanforderung. Zu kurze Eingaben führen jetzt zu einem verständlichen Hinweis und erneuter verdeckter Eingabe. Ein bereits angelegtes Salt kann dabei unverändert weiterverwendet werden. Solange noch keine Datenbank existiert, kann eine neue ausreichend lange lokale Passphrase gewählt werden. Bei vorhandener Datenbank weist der Dialog auf die bisherige Passphrase hin; es gibt keinen automatischen Datenbank- oder Telegram-Passwortreset.

## Aktueller Runtime-Stand

Die native Runtime-Integration ist jetzt implementiert, nicht mehr nur geplant. `telegram_bridge/application.py` komponiert vorhandene authentifizierte Sitzung, kontrollierte Zielauflösung, `LiveCallLoop` und `NativeMedia`. Der Helper `media_runtime` konstruiert einen echten tgcalls-Descriptor und enthält die tatsächlichen Create-/Signaling-/Stop-Aufrufe. Seine Ausführung mit Audio ist doppelt gesperrt: explizite Freigabe am Python-Einstieg und `--allow-audio` im getrennten nativen Prozess. **Aktuell wurden weder Anmeldung noch Call noch Audiostart ausgeführt.**

**Geprüft:** 47 Offline-Tests, native TDLib-Checks und neun echte native Runtime-/IPC-Prüfungen. Letztere erzeugen einen tatsächlichen Descriptor mit 256-Byte-Testschlüssel und fünf Relay-Einträgen, prüfen Ablehnung ungültiger Daten, Startverweigerung ohne Audiofreigabe, Status, wiederholtes Stop und fehlende Payloads in Ausgaben. Sie rufen `Meta::Create` nicht auf. Damit ist der Kontrollpfad geprüft, noch nicht die Audiowiedergabe oder Telegram-Interoperabilität mit einem echten Konto.

Der Apple-Linker meldet beim nativen Binary eine reduzierte Ausrichtung von `__DATA,__common` (0x8000 auf 0x4000). Der Build und die nativen Kontrolltests bestehen; die Warnung wurde nicht unterdrückt. Eine Aussage zur vollständigen Audio-Laufzeitstabilität wird daraus nicht abgeleitet.

```sh
make check
make telegram-runtime
python3 -m telegram_bridge native-check
python3 -m telegram_bridge preflight
```

`make telegram-runtime` kann Build-Abhängigkeiten herunterladen, startet aber in den Checks keine Audiogeräte oder Anrufe. Die bisherige `demo` bleibt Simulation und ist kein Ersatz für diese nativen Prüfungen.

### Daten, Ereignisse und Gerätetreue

`descriptor.py` validiert TDLib-Ready, 256-Byte-Schlüssel, Peer-Privacy und Relay-Daten. Es unterstützt zunächst ausschließlich den echten ausgehandelten Pfad **12.0.0**, Layer **65 bis 92** gemäß gepinntem TDLib-Schema. Unbekannte Versionen werden abgelehnt. Reflector-IDs werden numerisch sortiert auf 1..N abgebildet, Peer-Tags hexkodiert, IPv4/IPv6 und STUN/TURN getrennt übernommen, entsprechend dem offiziellen [OngoingCallContext](https://github.com/TelegramMessenger/Telegram-iOS/blob/6ad963e5b62d354da79040f388ae2b9132fb17b8/submodules/TelegramVoip/Sources/OngoingCallContext.swift). Eine Registrierung aller bekannten Libraryversionen bedeutet keine Freigabe ungeprüfter Adaptervarianten.

Der native Helper übermittelt State-/Signaling-Callbacks über **private Pipes**, nicht über Logdateien. Normale Library-stdout/stderr-Ausgaben sind verworfen, Core-Dumps deaktiviert. IPC muss selbstverständlich Schlüssel und Signaling im Arbeitsspeicher transportieren; diese Frames dürfen nicht als Diagnoselog gespeichert werden. Diagnoseantworten enthalten nur feste Fehlercodes und Metadaten. Stop wartet begrenzt auf den nativen Abschluss, sonst wird der Prozess beendet und der Controller meldet ein unbestätigtes Ende statt Erfolg. Reconnecting ist ein eigener Zustand mit begrenzter Frist.

`strict_devices.cpp` löst ausschließlich die konfigurierten UIDs auf, prüft Ein-/Ausgangsfähigkeit und Lebendigkeit und hält die erste Geräte-ID fest. Der Runtime-Wrapper lehnt Default-Auswahl ab. Zusätzlich fügt `media-source-guard.py` einen engen, reproduzierbaren Hook in die gepinnte macOS-ADM-Initialisierung ein: Der tatsächliche AudioDeviceID kommt ausschließlich aus dem UID-Resolver, niemals aus dem potenziell wechselnden Geräteindex oder Systemdefault. Bei Verlust wird beendet statt automatisch umgebunden. Andere Quellcodeänderungen werden verweigert. Diese Schutzlogik ist kompiliert/geprüft; ein physischer Geräteverlusttest braucht den späteren erlaubten Audiolauf.

### Noch genau benötigter Zugang-/Audiotest

Die bereits offene Accountfrage bleibt unverändert. Wenn die Angaben bereitstehen, lokale Schritte:

```sh
python3 -m telegram_bridge configure
python3 -m telegram_bridge configure-audio
python3 -m telegram_bridge login
```

`configure-audio` zeigt lediglich CoreAudio-Gerätemetadaten, lässt explizit Ein-/Ausgang auswählen und speichert deren UIDs in einer privaten `routing.json` (0600). Es startet keine Geräte und ändert keine globalen Routen. `login` kann einen Code anfordern und darf erst bewusst für das gewählte Konto ausgeführt werden. Keiner dieser Schritte wurde während der Umsetzung ausgeführt.

Die reine Auth-Konfiguration benötigt jetzt **kein Anrufziel**. `configure` fragt nur API-ID, API-Hash und Absendernummer ab; eine bereits bestätigte Nummer kann optional aus einer lokalen privaten Datei über `--sender-file` übernommen werden. Bestehende Profile mit Ziel bleiben kompatibel. Vor jedem Anruf verlangt `require_target` weiterhin ausdrücklich ein bestätigtes Ziel und bricht andernfalls vor nativen Geräten oder Auth-Zugriff ab. API-ID und Hash werden nur im verdeckten lokalen Prompt eingegeben, nicht im Chat, in Shellargumenten oder über automatische Zwischenablageübernahme.

Danach kann der Koordinator die vorhandene Funktion `run_authorized_call_test` für den **konkret freigegebenen** Anruf-/Audiotest aufrufen. Ohne `audio_and_call_authorized=True` greift sie nicht einmal auf Konfiguration oder native Bibliotheken zu. Sie verlangt einen bestehenden Login, prüft dessen Absendernummer gegen das konfigurierte Konto und fordert niemals selbst einen neuen Anmeldecode an. Ein frei automatisch gestarteter CLI-Anruf ist weiterhin nicht angeboten. Hier fehlt kein weiterer Platzhalter in der Runtime-Komposition; offen ist die echte Prüfung am Konto und den Audiogeräten.

Zu prüfen sind dann: echte Empfängerzustellung, kompatibles Server-/Key-Material, macOS-Mikrofonberechtigung, beide Audiorichtungen, Geräteverlust und beidseitiges Auflegen. **Original-Voice-Autostart und der kostenlose Loopback-Ersatz sind weiterhin separate offene Aufgaben.** Trial-Rauschen wurde nicht umgangen. Die folgenden Abschnitte enthalten Hintergrund und frühere Buildbefunde.

Stand: 10. September 2026. Telegram ist der gewählte Weg. WhatsApp-Business-App bleibt erhalten, FaceTime und andere Telefonanbieter werden nicht weiterverfolgt. Original-Codex-Voice bleibt zwingend; keine eigene Realtime- oder TTS-Antwort.

## Tatsächlich gebaut und ausgeführt

- Offizielles TDLib an Revision `d1085f9cebc5a62379991ae1652673954f229c1f` lokal aus Source gebaut: Version **1.8.67**, `libtdjson.dylib`, **Mach-O arm64**.
- Echte C-API über Python-Standardbibliothek/ctypes geladen. Synchronen JSON-Parser ausgeführt, native Clientinstanz erzeugt, Version über asynchrones Request/Response gelesen, `authorizationStateWaitTdlibParameters` empfangen und `authorizationStateClosed` bestätigt.
- Dieser native Check setzt keine TDLib-Parameter, meldet niemanden an, verlangt keinen Code, öffnet keine Datenbank und tätigt keine Anrufe. Keine Audio-Engine oder Route wird angefasst.
- 47 Offline-Tests prüfen Call-Zustände, Rennen, Aussonderung fremder Calls, idempotentes Ende, fehlende Medienfreigabe, Konfigurationsrechte, Authentifizierung, Zielauflösung, Descriptor-Abbildung und den begrenzten Eventloop. Die bestehende Swift-/Tongenerator-Prüfung besteht ebenfalls.
- `demo` führt die echte eigene Steuerlogik mit ausdrücklich simuliertem Transport/Medienadapter aus. Sie ist kein Telegram-Netztest.

## Ausführen

```sh
make check
python3 -m telegram_bridge demo
python3 scripts/build-tdlib.py
python3 -m telegram_bridge native-check
python3 -m telegram_bridge preflight
```

Der Build lädt nur den in `dependencies.json` festgelegten offiziellen Source nach `.build/vendor/td` und baut dort. Kein systemweites Installieren. Voraussetzungen: arm64 macOS, vorhandene CMake/Clang/gperf/OpenSSL-Entwicklungspakete. Ein abweichender oder geänderter vorhandener Checkout wird nicht überschrieben. Native Prüfung ist separat von `make check`, damit Standardprüfungen ohne Downloads und native Telegram-Abhängigkeit laufen.

Erwarteter derzeitiger Preflight: `ready_for_live_call=false`, Exitcode 2. Dies ist bewusst kein grüner Bereitschaftsstatus.

## Implementierte Bausteine

`telegram_bridge/native.py` kapselt `td_create_client_id`, `td_send`, `td_receive`, `td_execute` und bestätigtes Schließen. Genau ein Receive-Loop je Prozess; TDLib-Logs deaktiviert, rohe Telegram-Updates nie ausgeben. [Offizielle JSON-Schnittstelle](https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/telegram/td_json_client.h).

`CallSession` in `control.py` ist ein benutzbarer, transportinjizierter Adapter für **einen** Audioanruf: `start`, `status`, `handle`, `end`, `tick`. Der Transport kann `TDJson.send` sein, sobald authentifizierte Sitzung und echtes Medienbackend integriert sind. Das Protokoll muss vom Medienbackend kommen; keine erfundenen Layer-/Versionsangaben im Live-Betrieb. [Gepinntes API-Schema](https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/td/generate/scheme/td_api.tl).

Die Zustandsfolge trennt `dialing`, `ringing`, `exchanging_keys`, `media_connecting`, `active`, `ending` und bestätigtes `ended`. TDLib-Ready allein wird **nicht** als hörbare Verbindung gewertet. Verspätete createCall-Antwort nach Abbruch wird verworfen/aufgelegt, fremde Call-IDs werden ignoriert, ein zweiter Start derselben Instanz abgelehnt. Ein Stop-Timeout bleibt `end_unconfirmed`. Empfangene Schlüssel-/Signalisierungsdaten verlassen den Adapter nur zum Medienbackend, nicht ins Statusprotokoll. Diese Eigenschaften sind bisher mit Offline-Ereignissen getestet, nicht im Telegram-Netz.

## Sichere lokale Accountvorbereitung

Noch kein Absenderkonto, keine API-Daten und kein bestätigter Telegram-Empfänger vorgegeben. Keine WhatsApp-Nummer automatisch übernommen.

Wenn Felix ein **bereits bestehendes, getrenntes Telegram-Absenderkonto** gewählt hat, kann er lokal in einem Terminal ausführen:

```sh
python3 -m telegram_bridge configure
```

API-ID, API-Hash, ausdrücklich gewählte Absendernummer und Ziel-@username werden verdeckt eingelesen. Speicherung ausschließlich unter `~/Library/Application Support/CodexPhoneBridge/telegram/default/config.json`, Verzeichnis 0700/Datei 0600. Kein Argument mit Secrets, kein Git-Eintrag, kein Überschreiben bestehender Profile. Das ist lokale Zugriffsbeschränkung, keine Verschlüsselung der API-Konfigurationsdatei.

Die spätere Ergänzung `live.py` implementiert diese kontrollierte Auflösung jetzt: erst authentifizierte Sitzung prüfen, dann nur den angegebenen öffentlichen Benutzernamen über `searchPublicChat` auflösen. Private menschliche Zielidentität, aktueller Username, Unterschied zum eigenen Konto und `can_be_called` werden geprüft. Keine Kontaktliste oder Nachrichtenabfrage. Diese Funktion wurde noch nicht an einem echten Konto ausgeführt.

Die eigene API-ID/API-Hash stammen von [Telegram](https://my.telegram.org), nicht von einem fremden Userbot-Projekt. Nach ausdrücklicher späterer Entscheidung zur Anmeldung:

```sh
python3 -m telegram_bridge login
```

**Dieser Befehl kann einen Telegram-Anmeldecode anfordern. Er wurde nicht ausgeführt.** Er unterstützt vorhandene Konten mit Code/2FA, private verschlüsselte TDLib-Datenbank und Profil-Lock. Die Datenbank-Passphrase wird lokal eingelesen, mit scrypt und eigenem Salt abgeleitet und nicht gespeichert. Datei-/Chat-/Nachrichtendatenbanken sowie Secret Chats sind deaktiviert; andere Updates werden ignoriert. Registrierung, kostenpflichtige/Premium-Authentifizierung, E-Mail- und QR-Sonderflüsse stoppen ausdrücklich. Login ruft niemanden an. Live-Authentifizierung ist noch ungetestet.

Die Angabe `telegram_authenticated=not_checked_offline` im Preflight bedeutet genau das: Offline wird kein vorhandener Login behauptet oder widerlegt.

## Tatsächlicher Medien-Buildbefund

Offizielles `TelegramMessenger/tgcalls`, Revision `efd330ca04f74706024a5abdfb5b41f4e4dd1065`, besitzt ein macOS-Swift-Package mit ARM64/WebRTC-Definitionen und separaten Audiogeräte-IDs. Es erwartet jedoch externe `SharedHeaders` für WebRTC, Abseil, OpenSSL, Opus, FFmpeg usw.; es deklariert diese nicht als selbständig auflösbare Swift-Package-Abhängigkeiten.

Ein echter lokaler `swift build -c release --jobs 2` im isolierten Vendor-Checkout scheiterte an **`absl/types/optional.h` fehlt**. Zusätzlich meldet der aktuelle Package-Scan das enthaltene CLI-main als widersprüchlich zum Library-Produkt. Es wurde kein beliebiges fremdes Binärpaket eingesetzt und kein Audio gestartet.

Der historische Standalone-Fehler ist kein endgültiger Blocker. Der folgende kohärente Parent-Build ersetzt diesen Ansatz. `UnavailableMedia` sperrt weiterhin jeden Live-Start bis zum vollständigen Laufzeitadapter. Nur Zugangsdaten einzutragen macht die Telefonie nicht funktionsfähig. [Historisches Package](https://github.com/TelegramMessenger/tgcalls/blob/efd330ca04f74706024a5abdfb5b41f4e4dd1065/Package.swift).

## Kohärenter offizieller Medienbuild

`dependencies.json` enthält jetzt einen zusammengehörigen Telegram-iOS-Source-Snapshot:

- Parent `6ad963e5b62d354da79040f388ae2b9132fb17b8`.
- Dessen tgcalls-Gitlink `e3069322a3d1e16ecb11a5e302242e59ddd7f09e`.
- Dessen WebRTC-Gitlink `3817e906cb6c22ec9cc62023b073e1a668d9cb33`, auf den von Telegram im Parent referenzierten `ali-fareed/webrtc`-Fork.
- Abseil/BoringSSL/Opus/weitere Buildquellen und Bazel-Regeln aus genau diesem Parent-Baum bzw. dessen gepinnten Submodulen. Keine beliebigen separat installierten WebRTC-Header.

Das offizielle Bazel-8.4.2-arm64-Binary wird gegen den dokumentierten SHA-256 geprüft und nur nach `.build/tools` geladen. Das Skript baut einen macOS-CLI-Target, nicht die signierte iOS-App. Ein leeres `build_configuration`-Modul erfüllt nur die unbenutzte Repository-Deklaration; keine API-Schlüssel, Provisioning-Profile oder erfundenen Identitäten werden eingetragen. Sicherheits-/Hashprüfungen werden nicht abgeschaltet.

```sh
python3 scripts/build-media.py
python3 -m telegram_bridge media-check
```

Eigener Code in `native/media_probe.cpp` registriert die echte `InstanceV2Impl` und liest `Meta::Versions()` / `Meta::MaxLayer()` aus dem gelinkten Mediencode. Er ruft **nicht** `Meta::Create` auf, erzeugt keine PeerConnection und öffnet kein Audiogerät. Ein erfolgreicher Probe ist daher ein echter Build-/Linknachweis, noch kein Medien-Laufzeitnachweis. Das upstream `tgcalls_core`-Target verwendet den nicht-iOS-Platform-Adapter; dessen Hardware-/PCM-Anbindung bleibt in unserem Runtime-Adapter explizit zu prüfen. [Parent](https://github.com/TelegramMessenger/Telegram-iOS/tree/6ad963e5b62d354da79040f388ae2b9132fb17b8), [Bazel-Medientarget](https://github.com/TelegramMessenger/Telegram-iOS/blob/6ad963e5b62d354da79040f388ae2b9132fb17b8/submodules/TgVoipWebrtc/BUILD).

**Ausgeführter Build-/Linknachweis:** Der Kernbuild durchlief 1.939 Aktionen erfolgreich. Der nachfolgende Probe-Link deckte auf, dass das offizielle CLI-Beispiel die nicht mitgebaute `AudioDeviceModule::Create`-Factory durch einen Null-Platzhalter ersetzt. Diesen Platzhalter übernehmen wir nicht. `native/macos_adm.BUILD` ergänzt stattdessen ausschließlich Buildregeln für die echte macOS-ADM-/PortAudio-Ringbuffer-Implementierung aus exakt demselben gepinnten WebRTC-Quellbaum. Keine Änderungen am Netzwerk-/Medienquellcode und keine Ersatz-Factory.

Das Ergebnis wurde als **Mach-O arm64** geprüft. Native Ausführung liefert tatsächlich **7.0.0, 8.0.0, 9.0.0, 12.0.0, 13.0.0**, maximaler Layer **92**, `audio_opened=false`, `call_created=false`, `live_adapter_ready=false`. Der vollständige Buildskriptlauf mit dieser Ergänzung und anschließender Metadatenprobe war erfolgreich. Symbole der echten `AudioDeviceMac`-Implementierung sind im Binary enthalten, ihre Funktionen wurden nicht aufgerufen.

Der damalige Laufzeitadapter-Implementierungspunkt ist durch den oben beschriebenen nativen Helper und UID-Guard erledigt. Die fehlende Medienbibliothek ist ebenfalls kein Blocker mehr. Der reale Audio-/Konto-Test bleibt erforderlich.

## Angebundener Live-Eventloop, weiterhin gesperrter Live-Befehl

`LiveCallLoop` verbindet autorisierten TDLib-Client, kontrollierte Zielauflösung, `CallSession` und injiziertes Medienbackend. Mediencallbacks werden über eine begrenzte Queue auf den TDLib-Eventthread überführt. Ein Call ist auf maximal 180 Sekunden beschränkt; im `finally` wird das Ende angefordert und begrenzt bestätigt. Authentifizierungsverlust führt zum Abbruch statt zu stiller Neuanmeldung. `RequestPump` protokolliert keine rohen Telegram-Antworten.

Ohne verfügbares Medienbackend bricht der Loop **vor jeder TDLib-Anfrage** ab. Es gibt weiterhin keinen CLI-Befehl, der einen echten Anruf starten kann. Im realen nativen Offlinecheck wurde `require_authorized` gegen TDLib im unkonfigurierten Zustand geprüft und abgewiesen, ohne Login oder Zielabfrage. Die übrigen Eventloop-Fälle sind Offline-Tests, kein Netzbeweis.

## Nächste Schritte und getrennte Gates

1. Accountwahl von Felix, API-Daten nur lokal, bestätigter Telegram-Empfänger. Anmeldung nur bewusst durchführen. Keine neue Kontoregistrierung automatisch starten.
2. Den fertig vorbereiteten Runtime-Pfad mit vorhandener authentifizierter Sitzung und explizit erlaubtem Audiozugriff prüfen. Native Metadaten allein sind keine Freigabe für den Start.
3. Erst damit einen klar begrenzten echten Telegram-Anruf testen: Klingeln am gesperrten iPhone, Annahme, bidirektionales Audio, Fehler und Auflegen. Keine Fake-Media-Konfiguration im Netz verwenden.
4. Audio-Routing bleibt eigene Schicht. Loopback Trial rauscht; BlackHole/Process Tap sind noch nicht gebaut/installiert. Für Dauerbetrieb nicht auf Trial-Rauschen oder manuelle Resets bauen.
5. Original-Codex-Voice-Autostart ist nicht bewiesen. Ein Telegram-Call bedeutet keine gestartete Codex-Voice-Sitzung. T3-Ereignisse kommen erst nach diesen Nachweisen.

## Lizenz und Nutzung

TDLib: Boost Software License 1.0. tgcalls: LGPLv3; spätere Bibliotheks-/Binärdistribution muss deren Bedingungen beachten. Das Repo übernimmt keinen Drittanbieter-Source oder Binary und vergibt noch keine neue Gesamtlizenz. [TDLib-Lizenz](https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/LICENSE_1_0.txt), [tgcalls-Lizenz](https://github.com/TelegramMessenger/tgcalls/blob/efd330ca04f74706024a5abdfb5b41f4e4dd1065/LICENSE).

Telegram erlaubt reguläre eigene Clients, verlangt eigenes api_id und Wissen/Zustimmung des Nutzers. Die zusätzlichen KI-Content-Bedingungen und deren eng begrenzte Einwilligungsausnahme bleiben für die spätere eigene Audio-Weiterleitung zu beachten. Kein Chat-Scraping oder Training; diese Implementierung öffnet keine Kontaktlisten. Keine pauschale rechtliche Freigabe des kompletten künftigen Produkts. [API-Bedingungen](https://core.telegram.org/api/terms), [Content-Regeln](https://telegram.org/tos/content-licensing).
