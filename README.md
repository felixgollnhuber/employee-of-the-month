# Codex Phone Bridge

Experimenteller macOS-Prototyp für einen echten eingehenden Handy-Anruf bei Rückfragen oder Blockaden in Codex. Das Gespräch soll mit dem **Original-Voice-Modus der installierten Codex-App in der betreffenden Task** stattfinden. Später ist eine Anbindung an T3 Code vorgesehen. Eine separate Realtime-API-Sprachsession oder TTS-Antwort erfüllt dieses Ziel nicht.

## Stand: 10. September 2026

Der lokale Audioweg ist erprobt, eine vollständige Telefonbrücke ist noch nicht implementiert.

- Loopback 2.5.0 Trial wurde im ursprünglichen Versuch installiert und mit zwei virtuellen Geräten eingerichtet.
- Der virtuelle Hinweg wurde mit einem neutralen Ton tatsächlich gemessen.
- Anschließend wurde das MacBook-Mikrofon für einen lokalen Live-Test ergänzt. Der Nutzer bestätigte, dass seine Sprache verstanden wurde und er die Original-Voice-Antwort perfekt hörte. Dies ist eine qualitative Nutzerbestätigung, keine instrumentierte Duplex- oder Latenzmessung.
- Echter Anruf, Anbieterwahl, automatische Voice-Steuerung, Kontextprüfung, Fehlerbehandlung und T3-Code-Integration sind offen.

**Gewählter Telefonieweg: Telegram über einen regulären eigenen Benutzerclient.** Die WhatsApp-Business-App bleibt unverändert. Kein weiterer FaceTime-/Providervergleich ist Teil der Umsetzung.

Das Repository enthält einen Telegram-Anrufsteuerungsadapter, die offizielle TDLib-JSON-C-API-Anbindung, lokale Accountkonfiguration, expliziten Login sowie kontrollierte Zielauflösung und Eventloop. TDLib 1.8.67 und ein kohärenter offizieller tgcalls/WebRTC-Medienbaum wurden nativ für arm64 gebaut und ohne Anmeldung/Audio geprüft. Eine echte gelinkte Medienprobe meldet Bibliotheksversionen und Layer 92; 34 Offline-Tests bestehen. **Echte Telegram-Anrufe sind noch gesperrt:** Der Laufzeitadapter zwischen TDLib-Ready/Signaling und tgcalls einschließlich sicherer Audiogerätebindung fehlt noch. Die sichtbare Demo bleibt ausdrücklich Simulation. [Stand und Befehle](docs/telegram-control.md).

## Aufbau

```text
Handy -> Telefonverbindung -> Telefonie-Client am Mac
      -> PHONE_TO_CODEX -> Original-Codex-Voice in der Zieltask

Original-Codex-Voice -> App-Capture -> CODEX_TO_PHONE
                    -> Telefonie-Client -> Handy
```

Für den späteren Telefonbetrieb soll PHONE_TO_CODEX ausschließlich die Ausgabe des Telefonie-Clients erhalten. CODEX_TO_PHONE soll ausschließlich Codex-App-Audio an den Mikrofoneingang des Telefonie-Clients liefern. App-Capture isoliert nicht automatisch eine einzelne Task und kann App-Klänge enthalten.

Beim letzten lokalen Test war zusätzlich das physische MacBook-Mikrofon im Hinweg aktiv. Dies ist ein Testaufbau, keine fertige Telefonkonfiguration. Vor einem Telefonversuch diese Quelle bewusst deaktivieren, damit Raumgeräusche nicht mitgesendet werden. Aktuelle Routen müssen vor einem neuen Test geprüft werden; das Repository verändert sie nicht.

## Struktur

- `Sources/AudioBridgeTest.swift`: exakt benannte Gerätebindung, begrenzte Wiedergabe, RMS-/Peak-Messung ohne gespeicherte Audiosamples.
- `scripts/generate-tone.py`: neutraler WAV-Testton, lokal erzeugt.
- `Makefile`: Build und Prüfung ohne Audiozugriff.
- `docs/evidence.md`: bereinigte historische Befunde und ihre Grenzen.
- `docs/manual-test.md`: CLI-Modi und manuelle Testfolge.
- `telegram_bridge/`: TDLib-Bindung, Audio-only-Call-Lifecycle, private lokale Konfiguration und expliziter Login.
- `dependencies.json`: geprüfte offizielle TDLib-/tgcalls-Revisionen.
- `scripts/build-media.py`, `native/`: zusammengehöriger Telegram-/WebRTC-Build und echte native Metadatenprobe ohne Audiozugriff.
- `docs/telegram-control.md`: native Buildbefunde, Offlinechecks und offene Live-/Medien-Gates.

## Lokaler Build und Prüfung

Voraussetzungen: macOS, Xcode Command Line Tools mit `swiftc`, Python 3 und `make`. Keine zusätzlichen Python-Pakete erforderlich. Die Laufzeittests benötigen die vorbereiteten virtuellen Geräte; der Build nicht.

```sh
make build
make check
```

`make check` kompiliert das Programm, erzeugt und prüft eine 0,5 Sekunden lange Stereo-WAV-Datei und prüft die Ablehnung eines ungültigen CLI-Modus. Es startet keine Audio-Engine, öffnet kein Mikrofon, steuert keine App und tätigt keinen Anruf. Geprüft mit Apple Swift 6.3.3 auf arm64 und Python 3.14.6.

Die ursprüngliche Sprach-AIFF wird nicht übernommen. Der Generator erzeugt stattdessen einen neutralen 440-Hz-Ton mit Ein-/Ausblendung unter `.build/test-tone.wav`. Für einen bewussten Sprachtest kann eine eigene lokale Audiodatei mit maximal zehn Sekunden verwendet werden. Der Ton selbst prüft keine Spracherkennung.

## Nächste Gates

1. In der richtigen Codex-Task Original-Voice manuell starten und Kontext mit einer vorher ausschließlich dort hinterlegten Information prüfen.
2. Getrenntes Telegram-Absenderkonto bewusst auswählen und eigene API-Daten lokal eingeben. Gepinntes tgcalls/WebRTC-Medienbackend bauen, Zielauflösung und Live-Eventloop verbinden. Ein ausgehender Telegram-Audioanruf muss am iPhone tatsächlich klingeln.
3. Sprachverständlichkeit beider Richtungen, Echo, Unterbrechung und zusätzliche Latenz mit einem echten Anruf prüfen.
4. Offline-geprüften Call-Adapter an das echte Medienbackend anbinden. Feste Task-Zuordnung und gemeinsames idempotentes Beenden von Telegram und eigener Voice-Sitzung ergänzen. Nichtabheben, Geräteverlust, App-Fehler und Verbindungsabbruch im echten Aufbau testen.
5. Erst danach automatische Rückfragen und T3-Code-Anbindung ergänzen.

Ein möglicher Controller-Zustandsablauf ist `IDLE -> PREFLIGHT -> DIALING -> ANSWERED -> STARTING_VOICE -> ACTIVE -> STOPPING -> IDLE`. Dieser Ablauf ist nur ein Entwurf. Interne App-Symbole sind kein nachgewiesener öffentlicher Steuerungsendpunkt.

## Repository-Grenzen

Keine Installer, Drittanbieter-App-Bundles, proprietären Quellcodekopien, Rohinventare, Gesprächsaufnahmen, Zugangsdaten oder vorgefertigten Binärdateien. Lokale Ausgaben bleiben ignoriert. Eine Open-Source-Lizenz wurde nicht gewählt oder erteilt.
