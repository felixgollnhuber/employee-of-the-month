# Bereinigte Befunde

Diese Zusammenfassung übernimmt eigene technische Beobachtungen des Ausgangsprototyps vom 9. und 10. September 2026. Rohlogs, persönliche Geräteinventare und App-Code wurden nicht übernommen. Die Befunde wurden bei der Repository-Anlage nicht durch neue Audioaktionen wiederholt.

## Lokale Messungen

| Versuch | Ergebnis | Aussagegrenze |
| --- | --- | --- |
| 0,5 Sekunden 440-Hz-Ton auf PHONE_TO_CODEX | 264600 Mono-Frames bei 44,1 kHz in sechs Sekunden; Tonfenster RMS 0,049305, Peak 0,099945; sonst Nullpegel | Digitaler Hinweg bestätigt, keine Sprach- oder Kontextprüfung |
| Rückweg-Pegelmessung auf CODEX_TO_PHONE | 264600 Frames in sechs Sekunden, durchgehend Nullpegel | Frames vorhanden; ohne bestätigte gleichzeitige App-Ausgabe weder Rückwegdefekt noch Erfolg belegt |
| Physisches MacBook-Mikrofon im Hinweg | 264600 Frames; RMS 0,001952 bis 0,002467; Peak maximal 0,009225 | Signal vorhanden; Pegel allein beweisen keine verständliche Sprache |
| Anschließender Live-Voice-Versuch | Nutzer bestätigte verstandene Sprache und perfekt gehörte Antwort | Qualitative Bestätigung des lokalen Gesprächs; kein echter Telefonanruf, keine instrumentierte Latenz- oder Kontextprüfung |

Die aktuelle physische Hörroute ist aus dieser Bestätigung nicht eindeutig rekonstruierbar und wird hier nicht als verifizierte Konfiguration ausgegeben.

## Historische statische App-Untersuchung

Untersucht wurde die installierte Codex-App mit Bundle-ID `com.openai.codex`, Version `26.903.61454`, Build `8378`. Sie lag unter einem App-Namen, der von der Produktbezeichnung abwich. Portabler Code darf sich deshalb nicht auf diesen lokalen Namen verlassen.

Die Untersuchung fand Hinweise auf WebRTC, eine gespeicherte Mikrofonwahl, Audiowiedergabe über ein HTML-Audioelement, taskbezogene Voice-Start-/Stop-Pfade und bedingte Kontextübernahme. Ein globaler Startpfad war einer neuen Task zugeordnet. Eine getrennte Ausgabeauswahl wurde in den untersuchten Dateien nicht gefunden. Ein negativer Texttreffer beweist nicht, dass eine Funktion fehlt.

Diese Beobachtungen sind versionsabhängig und belegen keinen öffentlichen API-Vertrag, keine verfügbare externe Steuerung und keinen tatsächlich aktiven Laufzeitpfad. Der ursprüngliche statische Probe mit minifizierten Suchfragmenten wurde nicht übernommen. Für den aktuellen Build wird das App-Paket nicht gelesen.

## Prüfung bei Repository-Anlage

`make check` wurde am 10. September 2026 erfolgreich ausgeführt. Der Swift-Testcode wurde unverändert kopiert und per Dateivergleich geprüft. Apple Swift 6.3.3 meldet bei der Übergabe einer CFString-Variable an `AudioObjectGetPropertyData` eine Warnung zur Bildung eines `UnsafeMutableRawPointer`. Der Build gelingt; die Speicherübergabe sollte vor weiterer Produktisierung überprüft werden. Die Offline-Prüfung führt diese Geräteabfrage nicht aus und ist kein erneuter Laufzeitnachweis dafür.
