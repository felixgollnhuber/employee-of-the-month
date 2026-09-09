# Manuelle Laufzeittests

Diese Schritte sind von `make check` getrennt. Sie setzen bewusst vorbereitete Audio-Routen und einen passenden Zeitpunkt voraus. Während eines anderen laufenden Voice-Gesprächs keine Audio-Tests starten.

## Voraussetzungen

Die virtuellen Geräte müssen exakt `PHONE_TO_CODEX` und `CODEX_TO_PHONE` heißen. Der Prototyp lehnt fehlende oder mehrdeutige Gerätenamen ab und liest die Bindung zurück. Er ändert keine globalen Standardgeräte.

Eine passende Routing-Anwendung muss separat installiert und eingerichtet werden. Im Ausgangsversuch wurde Loopback 2.5.0 Trial verwendet. Dieses Repository installiert oder lizenziert keine Drittsoftware.

## CLI

Aus dem Repository-Verzeichnis:

```sh
make build fixture
.build/AudioBridgeTest --check-devices
```

| Modus | Wirkung |
| --- | --- |
| `--check-devices` | Geräte inventarisieren und beide Namen prüfen; keine Audio-Engine |
| `--prepare DATEI` | Datei öffnen, Graphen vorbereiten und Gerätebindung prüfen; keine gestartete Audio-Engine |
| `--transport-test DATEI` | Datei nach zwei Sekunden auf PHONE_TO_CODEX abspielen und dort sechs Sekunden Pegel messen |
| `--meter-input DATEI` | Sechs Sekunden PHONE_TO_CODEX messen, keine Wiedergabe |
| `--meter-return DATEI` | Sechs Sekunden CODEX_TO_PHONE messen, keine Wiedergabe |
| `--voice-ready DATEI` | Datei nach zwei Sekunden nach PHONE_TO_CODEX senden und zwanzig Sekunden CODEX_TO_PHONE messen |

Alle Modi außer `--check-devices` verlangen derzeit eine lesbare lokale Audiodatei mit einer Dauer größer Null und höchstens zehn Sekunden. Auch die reinen Messmodi öffnen die Datei und bereiten beide Graphen vor, spielen sie aber nicht ab. Dies ist eine bekannte Einschränkung der Prototyp-CLI.

Beispiel für einen bewusst gestarteten reinen Transporttest:

```sh
.build/AudioBridgeTest --transport-test .build/test-tone.wav
```

Der Ton ist keine Sprachphrase. `--voice-ready` startet trotz seines Namens weder Codex noch Voice; Original-Voice muss zuvor in der gewünschten Task manuell gestartet sein. Für die Prüfung von Sprache eine eigene kurze lokale Sprachdatei verwenden oder den bewusst angeschlossenen Mikrofonweg nutzen.

## Aussage und Ende

Die Messung speichert nur aggregierte Pegel, keine Audiosamples. Ein erfolgreicher Exit bestätigt empfangene Frames, nicht ein vorhandenes Signal oder eine korrekte Antwort. RMS, Peak und Inhalt müssen getrennt bewertet werden. Capture kann eine macOS-Mikrofonfreigabe erfordern.

Am regulären Testende werden die eigenen Audio-Engines beendet. Das Programm beendet weder eine Codex-Voice-Sitzung noch einen Telefonanruf und stellt keine extern eingerichteten Routen zurück. Diese Schritte bleiben manuell, bis ein Controller implementiert ist.

Für einen späteren Telefonversuch beide Richtungen getrennt halten, das lokale Mikrofon aus dem Telefon-Hinweg entfernen und Monitoring auf Rückkopplung prüfen. Nach dem Anruf Telefonverbindung und die zugehörige Voice-Sitzung beenden. Geräteverlust und Abbruch müssen noch als vollständige Fehlerfälle getestet werden.
