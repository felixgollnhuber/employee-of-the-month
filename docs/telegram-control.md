# Telegram-Steuerung: überprüfte Zwischenimplementierung

Stand: 10. September 2026. Telegram ist der gewählte Weg. WhatsApp-Business-App bleibt erhalten, FaceTime und andere Telefonanbieter werden nicht weiterverfolgt. Original-Codex-Voice bleibt zwingend; keine eigene Realtime- oder TTS-Antwort.

## Tatsächlich gebaut und ausgeführt

- Offizielles TDLib an Revision `d1085f9cebc5a62379991ae1652673954f229c1f` lokal aus Source gebaut: Version **1.8.67**, `libtdjson.dylib`, **Mach-O arm64**.
- Echte C-API über Python-Standardbibliothek/ctypes geladen. Synchronen JSON-Parser ausgeführt, native Clientinstanz erzeugt, Version über asynchrones Request/Response gelesen, `authorizationStateWaitTdlibParameters` empfangen und `authorizationStateClosed` bestätigt.
- Dieser native Check setzt keine TDLib-Parameter, meldet niemanden an, verlangt keinen Code, öffnet keine Datenbank und tätigt keine Anrufe. Keine Audio-Engine oder Route wird angefasst.
- 27 Offline-Tests prüfen Call-Zustände, Rennen, Aussonderung fremder Calls, idempotentes Ende, fehlende Medienfähigkeit, Konfigurationsrechte und Authentifizierungsschritte. Die bestehende Swift-/Tongenerator-Prüfung besteht ebenfalls.
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

API-ID, API-Hash, ausdrücklich gewählte Absendernummer und Ziel-@username werden verdeckt eingelesen. Speicherung ausschließlich unter `~/Library/Application Support/CodexPhoneBridge/telegram/default/config.json`, Verzeichnis 0700/Datei 0600. Kein Argument mit Secrets, kein Git-Eintrag, kein Überschreiben bestehender Profile. Das ist lokale Zugriffsbeschränkung, keine Verschlüsselung der API-Konfigurationsdatei. Benutzername als Ziel muss noch kontrolliert in eine Telegram-User-ID aufgelöst werden; diese Netzfunktion ist noch nicht implementiert.

Die eigene API-ID/API-Hash stammen von [Telegram](https://my.telegram.org), nicht von einem fremden Userbot-Projekt. Nach ausdrücklicher späterer Entscheidung zur Anmeldung:

```sh
python3 -m telegram_bridge login
```

**Dieser Befehl kann einen Telegram-Anmeldecode anfordern. Er wurde nicht ausgeführt.** Er unterstützt vorhandene Konten mit Code/2FA, private verschlüsselte TDLib-Datenbank und Profil-Lock. Die Datenbank-Passphrase wird lokal eingelesen, mit scrypt und eigenem Salt abgeleitet und nicht gespeichert. Datei-/Chat-/Nachrichtendatenbanken sowie Secret Chats sind deaktiviert; andere Updates werden ignoriert. Registrierung, kostenpflichtige/Premium-Authentifizierung, E-Mail- und QR-Sonderflüsse stoppen ausdrücklich. Login ruft niemanden an. Live-Authentifizierung ist noch ungetestet.

Die Angabe `telegram_authenticated=not_checked_offline` im Preflight bedeutet genau das: Offline wird kein vorhandener Login behauptet oder widerlegt.

## Tatsächlicher Medien-Buildbefund

Offizielles `TelegramMessenger/tgcalls`, Revision `efd330ca04f74706024a5abdfb5b41f4e4dd1065`, besitzt ein macOS-Swift-Package mit ARM64/WebRTC-Definitionen und separaten Audiogeräte-IDs. Es erwartet jedoch externe `SharedHeaders` für WebRTC, Abseil, OpenSSL, Opus, FFmpeg usw.; es deklariert diese nicht als selbständig auflösbare Swift-Package-Abhängigkeiten.

Ein echter lokaler `swift build -c release --jobs 2` im isolierten Vendor-Checkout scheiterte an **`absl/types/optional.h` fehlt**. Zusätzlich meldet der aktuelle Package-Scan das enthaltene CLI-main als widersprüchlich zum Library-Produkt. Es wurde kein beliebiges fremdes Binärpaket eingesetzt und kein Audio gestartet.

Folge: `UnavailableMedia` sperrt jeden Start, bis ein passender offizieller WebRTC-/tgcalls-Build und unser Medienadapter existieren. Derzeit gibt es **keinen ausführbaren Live-Anrufbefehl**. Nur Zugangsdaten einzutragen macht die Telefonie noch nicht funktionsfähig. Diese Medienintegration ist offene Entwicklungsarbeit, kein Nutzerfehler. [Offizielles Package](https://github.com/TelegramMessenger/tgcalls/blob/efd330ca04f74706024a5abdfb5b41f4e4dd1065/Package.swift), [Medieninterface](https://github.com/TelegramMessenger/tgcalls/blob/efd330ca04f74706024a5abdfb5b41f4e4dd1065/tgcalls/Instance.h).

## Nächste Schritte und getrennte Gates

1. Accountwahl von Felix, API-Daten nur lokal, bestätigter Telegram-Empfänger. Anmeldung nur bewusst durchführen. Keine neue Kontoregistrierung automatisch starten.
2. Passenden offiziellen WebRTC-Source/Build zur gepinnten tgcalls-Version einbinden und ein Audio-only-Backend mit Geräte-Readback, State-/Signaling-Brücke und echtem Stop implementieren. Das Protokoll aus diesem Backend exportieren, dann Zielauflösung und authentifizierten Eventloop verbinden.
3. Erst damit einen klar begrenzten echten Telegram-Anruf testen: Klingeln am gesperrten iPhone, Annahme, bidirektionales Audio, Fehler und Auflegen. Keine Fake-Media-Konfiguration im Netz verwenden.
4. Audio-Routing bleibt eigene Schicht. Loopback Trial rauscht; BlackHole/Process Tap sind noch nicht gebaut/installiert. Für Dauerbetrieb nicht auf Trial-Rauschen oder manuelle Resets bauen.
5. Original-Codex-Voice-Autostart ist nicht bewiesen. Ein Telegram-Call bedeutet keine gestartete Codex-Voice-Sitzung. T3-Ereignisse kommen erst nach diesen Nachweisen.

## Lizenz und Nutzung

TDLib: Boost Software License 1.0. tgcalls: LGPLv3; spätere Bibliotheks-/Binärdistribution muss deren Bedingungen beachten. Das Repo übernimmt keinen Drittanbieter-Source oder Binary und vergibt noch keine neue Gesamtlizenz. [TDLib-Lizenz](https://github.com/tdlib/td/blob/d1085f9cebc5a62379991ae1652673954f229c1f/LICENSE_1_0.txt), [tgcalls-Lizenz](https://github.com/TelegramMessenger/tgcalls/blob/efd330ca04f74706024a5abdfb5b41f4e4dd1065/LICENSE).

Telegram erlaubt reguläre eigene Clients, verlangt eigenes api_id und Wissen/Zustimmung des Nutzers. Die zusätzlichen KI-Content-Bedingungen und deren eng begrenzte Einwilligungsausnahme bleiben für die spätere eigene Audio-Weiterleitung zu beachten. Kein Chat-Scraping oder Training; diese Implementierung öffnet keine Kontaktlisten. Keine pauschale rechtliche Freigabe des kompletten künftigen Produkts. [API-Bedingungen](https://core.telegram.org/api/terms), [Content-Regeln](https://telegram.org/tos/content-licensing).
