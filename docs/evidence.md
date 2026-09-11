# Bereinigte Befunde

## Telefonische Wiederholungsprobe der Rückfragenkette

Die korrigierte Rückfragenkette wurde erneut telefonisch geprüft, diesmal mit einem korrekt initialisierten Git-Demo-Repository. Die Brücke startete über den Schlüsselbund ohne Passphrase-Dialog und verwendete Cedar. Felix fragte nach Zielgruppe und Inhalt des Berichts. Der Koordinator gab diese Äußerung mit `intent: clarification` zurück; die Formatentscheidung blieb ausdrücklich offen.

Der Arbeits-Thread erläuterte, dass der fiktive Bericht für den Vorstand des Demo-Vereins Sonnenhof zum Lesen bestimmt ist und erzeugten sowie verbrauchten Strom der vergangenen Woche enthält. Er stellte eine neue Ask-Frage zur Formatwahl. Die Brücke übernahm deren neue Request-ID im laufenden Gespräch; insgesamt waren zwei Ask-IDs zugeordnet.

Danach wurde eine Auflegebitte erkannt. Telegram und GPT-Live wurden bestätigt geschlossen; das Ende wurde als `caller_requested` eingeleitet. Es wurden 2.363.840 PCM-Bytes zu GPT-Live und 2.240.000 zurück übertragen. Es kam keine endgültige Formatentscheidung in T3 an, daher bleibt die neue Ask-Frage offen. Dies ist kein Nachweis einer abschließenden Entscheidung im selben Telefonat. Der Nutzer bestätigte anschließend ausdrücklich, dass die Erklärung zum Vorstand und den Stromdaten sowie die erneute Frage nach CSV oder PDF am Telefon ankamen. Damit ist die hörbare Rückfragenkette qualitativ bestätigt; eine endgültige Formatentscheidung wurde in diesem Gespräch weiterhin nicht zurückgegeben.

Der Gesprächs-Koordinator meldete keine Fehler. PeakShare blieb von diesem Test getrennt und sein Telefonwächter gestoppt.

## Isolierte Dreiminutenprobe und gefundene Fehler

Für die automatische Probe wurde ein eigenes T3-Projekt „Telefonbrücke - Eskalationsprobe“ mit der Aufgabe „Demo-Wochenbericht: CSV oder PDF?“ erstellt. Die Frage wurde um 00:02:07.064 UTC erzeugt; der Wächter begann um 00:05:08.389 UTC zu wählen, nach **181,33 Sekunden**. Der Anruf mit Cedar wurde angenommen und der Audioweg erreichte `active`.

Felix traf keine Formatwahl. Er bat um Hilfe und fragte, welcher Bericht gemeint sei. Nach seiner bestätigten Bitte wurde um 00:06:47.263 UTC folgende Eingabe an die ursprüngliche Ask-ID zurückgegeben: „Bitte zuerst klären, welcher Demo-Wochenbericht gemeint ist. Die Formatwahl bleibt vorerst offen.“ Der Arbeits-Thread reagierte um 00:06:51.971 UTC und hielt fest, dass die Formatwahl offen blieb. Beide Agenten-Turns endeten regulär mit `completed` und ohne Session-Fehler.

Die roten Meldungen in beiden Threads waren `checkpoint.capture.failed`: Das zunächst leere Demo-Verzeichnis hatte kein eigenes Git-Repository und erbte das übergeordnete Repository. Der Testaufbau wurde anschließend mit einem eigenen lokalen Repository und initialem Commit korrigiert. Die alten Meldungen wurden nicht aus T3 gelöscht.

Der Anruf endete am eingestellten **180-Sekunden-Testlimit ab Anrufstart**, nicht durch eine erkannte Auflegebitte. Telegram `ended`, bestätigtes Medien-Cleanup und `live_close_confirmed=true` wurden gemeldet. Übertragen wurden 5.591.680 PCM-Bytes zu GPT-Live und 5.350.400 Bytes zurück. Diese Werte sind keine Latenz- oder Kostenmessung.

Die Brücke hatte die übergebene Rückfrage zu früh wie eine erledigte fachliche Frage behandelt und den Quellthread nicht weiter beobachtet. Dies wurde korrigiert: klare Antwortart, zusätzlicher begrenzter Aufgabenkontext, Rückgabe echter Quellantworten, Übernahme neuer Ask-Fragen und normale bestätigte Folgeeingaben nach einem beendeten Turn. Ein echter T3-Regressionstest mit synthetischen Nutzeraussagen bestätigte anschließend die Kette Rückfrage -> Erläuterung -> neue Ask-Frage -> PDF-Entscheidung. Die beiden neuen Test-Threads meldeten keine Fehler und besaßen einen beziehungsweise zwei erfolgreiche Checkpoints. Aktuell bestehen 100 Offline-Tests. Ein erneuter Telefonversuch mit dieser Rückfragenkorrektur wurde nicht gestartet.

Der PeakShare-Telefonwächter bleibt auf Nutzerwunsch gestoppt. Issue #1 ist weiterhin offen.

## Cedar und verzögerter PeakShare-Pilot

Die Voice-Konfiguration wurde auf Nutzerwunsch auf `cedar` gestellt. Eine echte GPT-Live-Sitzung mit dieser Stimme wurde von der API akzeptiert und anschließend bestätigt geschlossen; für diese Konfigurationsprüfung wurde kein Telefonanruf gestartet und kein Audio abgespielt.

Der Wächter prüft nun das ursprüngliche Datum einer noch offenen T3-Rückfrage und wartet standardmäßig 180 Sekunden. Tests zeigen: vor 180 Sekunden kein Anruf, ab 180 Sekunden ein Anrufversuch, bei einer Antwort nach 170 Sekunden kein Anruf. Wiederanlauf und doppelte Versuche bleiben getrennt abgesichert. Insgesamt bestehen 94 Offline-Tests.

Für das Hauptprojekt PeakShare wurde ein begrenzter Pilotprozess gestartet: 11. September 2026, 01:49 bis spätestens 05:49 Uhr (Europe/Vienna), höchstens drei Anrufe, je höchstens 180 Sekunden. Der Start wurde bestätigt und der Prozess anschließend als laufend geprüft. Beim Start gab es keine offene PeakShare-Rückfrage. Ein tatsächlich durch den Dreiminutentimer ausgelöster PeakShare-Anruf ist damit noch nicht nachgewiesen. Eingehende Anrufe und Telegram-Folgedialoge bleiben Issue #1.

## Gesamtprobe mit gesprochener T3-Antwort und Auflegen

Die zunächst angehaltene Telefonprobe wurde nach der Schlüsselbund-Einrichtung mit einer neuen offenen Rückfrage im T3-Thread „Telefonprobe - Rückfrage zur Testfarbe“ gestartet. Für diesen Anruf wurde eine Eingabefunktion gesetzt, die bei jeder unerwarteten Passphrase-Abfrage einen Fehler ausgelöst hätte. Die Anmeldung und der Anruf funktionierten ohne diese Abfrage.

Im an den Gesprächs-Koordinator übergebenen Nutzertranskript kamen sowohl Blau als auch Grün vor. Die echte Rückgabe an die zugehörige T3-Rückfrage enthielt `testfarbe: Grün`; T3 bestätigte `user-input.resolved`, und die Quellaufgabe erreichte `completed`. Danach meldete die Brücke `caller_requested_hangup=true`, `ending` mit Grund `caller_requested`, bestätigtes Medien-Cleanup sowie Telegram `ended` und `live_close_confirmed=true`.

Es wurden 2.330.240 PCM-Bytes an GPT-Live und 2.160.000 PCM-Bytes zurück an Telegram übertragen. Daraus wird weder eine genaue Gesprächs-, Latenz- noch Kostenmessung abgeleitet. Der Test belegt den manuell gestarteten Gesamtpfad einschließlich korrigierter Telefonantwort und sprachgesteuertem Auflegen. Er belegt noch keinen dauerhaften automatischen Wächterbetrieb oder die in Issue #1 geplanten Folge-Nachrichten und Rückrufe. Der abgeschlossene Test-Thread und sein Gesprächs-Koordinator bleiben zur Nachvollziehbarkeit in T3 erhalten.

## Automatische Datenbank-Anmeldung über den Schlüsselbund

Auf ausdrücklichen Nutzerwunsch wurde der abgeleitete Datenbankschlüssel im macOS-Schlüsselbund gespeichert. Vorher wurden die bestehende Telegram-Datenbank geöffnet und der konfigurierte Absender verifiziert. Danach wurde die Anmeldung in einem separaten Prozess ohne Eingabedialog erfolgreich wiederholt. Passphrase oder Datenbankschlüssel stehen nicht in der lokalen Markerdatei. Fünf neue Offline-Tests prüfen unter anderem das Überspringen aller Passphrase-Prompts, fehlgeschlagene Verifikation ohne Speicherung und die Bindung an das Datenbank-Salt. Zusammen mit dem Test des promptfreien Wächterstarts bestehen insgesamt 92 Offline-Tests.

Der erste Speicherdialog scheiterte nach der Eingabe an einer AppleScript-Variablen namens `result`. Dieser Fehler wurde reproduziert und durch eine eigene Variable behoben; die anschließende Einrichtung und der automatische Wiederaufruf waren erfolgreich. Die angehaltene Telefonprobe wurde danach mit einer neuen offenen Test-Rückfrage fortgesetzt.

## Direkter GPT-Live-Anruf und T3-Prüfung am 11. September 2026

- Der eigene API-Key wurde vom Modell-Endpunkt akzeptiert. Eine kurze echte GPT-Live-Sitzung mit 16-kHz-PCM konnte gestartet und bestätigt geschlossen werden.
- Anschließend wurde ein echter Telegram-Anruf mit direkter GPT-Live-Audioverbindung durchgeführt. Der native Zustand erreichte `active`; 1.486.080 PCM-Bytes gingen vom Telegram-Medienadapter an GPT-Live, 1.401.600 Bytes in die Gegenrichtung. Diese Mengen enthalten gegebenenfalls Stille und sind keine Messung der gesprochenen Dauer, Latenz oder Rechnung.
- Der Nutzer bestätigte ausdrücklich, mit dem Agenten gesprochen zu haben. Nach manuellem Auflegen wurden `live_closed=true`, Telegram `ended` und bestätigtes Medien-Cleanup gemeldet. Die Auflegebitte innerhalb des Gesprächs wurde damals noch nicht ausgeführt.
- Die Erkennung kurzer direkter Auflegebitten und die Weitergabe an den Telegram-Controller wurden anschließend ergänzt und offline geprüft, einschließlich negierter Bitten. Ein weiterer echter Anruf zur Prüfung dieses neuen Verhaltens wurde nicht durchgeführt.
- In T3 wurden über die offiziellen lokalen HTTP-Schnittstellen ein isolierter Test-Thread mit offener Testfarben-Rückfrage und ein Gesprächs-Koordinator erzeugt. Der Koordinator erhielt ein ausdrücklich synthetisches bestätigtes Transkript, gab „Blau“ an die passende Rückfrage zurück, und T3 meldete `user-input.resolved`. Beide Test-Threads wurden nach Abschluss archiviert. Dies ist ein echter Backend-Integrationstest, kein Test der sprachlichen Erkennung dieser Testantwort.
- Prüflauf nach der ersten Implementierung: 86 Offline-Tests, neun native Descriptor-/IPC-Prüfungen und ein zusätzlicher nativer PCM-Callback-Test ohne Gerätezugriff. Der native Build besteht; die bereits bekannte Linkerwarnung zur Ausrichtung von `__DATA,__common` bleibt vorhanden.
- Telegram-Chat nach Nichterreichbarkeit sowie Rückrufe und eingehende Statusanrufe wurden auf Nutzerwunsch für später in [Issue #1](https://github.com/felixgollnhuber/codex-phone-bridge/issues/1) festgehalten.

## Telegram-Klingeltest am 11. September 2026

Ein vom Nutzer ausdrücklich gewünschter echter Testanruf wurde mit dem eigenen TDLib-Client gestartet. Die vorhandene Sitzung konnte geöffnet werden, der Absender wurde mit der lokalen Konfiguration abgeglichen und der bestätigte Empfänger kontrolliert aufgelöst. Telegram meldete Zustellung (`ringing`), Annahme und anschließend bestätigtes Auflegen (`ended`). Der Test endete bei Annahme, Exitcode 0, ohne Medieninstanz, Audiozugriff oder Codex-Voice-Start. Dies belegt die Telefonie-Signalisierung, keine Sprachverbindung oder hörbares Klingeln aus unabhängiger Beobachtung. Zugangsdaten, Schlüssel und rohe Telegram-Updates wurden nicht übernommen.

## Frühere Audio-Befunde

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
