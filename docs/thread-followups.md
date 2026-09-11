# Ausdrückliche Folgenachrichten an bestehende Threads

Die Telefonbrücke kann eine ausdrücklich adressierte Nachricht als normalen T3-Nutzerturn an einen bestehenden Thread senden. Das funktioniert unabhängig von einem früheren Telefonvorgang und auch bei laufenden, completed oder settled Threads.

## Explizite Formulierung

Beispiele für Sprache und Telegram-Text:

- `Sende an Thread „Gesprächskoordination-Threads automatisch setteln“: Bitte prüfe auch den Fehlerfall.`
- `Sende an Thread „Gesprächskoordination-Threads automatisch setteln“ mit der Nachricht Bitte prüfe auch den Fehlerfall.`
- `Sag dem Thread „vollständiger Titel“, dass bitte auch der Fehlerfall geprüft werden soll.`
- Bei gleichen Titeln: `Sende an Thread thread-id: deine Nachricht.`

Bei einer expliziten Einzelaussage stehen Ziel und Nachrichteninhalt weiterhin zusammen. Titel werden vollständig und ohne Unterscheidung der Groß-/Kleinschreibung verglichen. Teiltreffer und Ähnlichkeitsranking wählen niemals das Ziel. Mehrere passende Threads führen zu einer Rückfrage mit Projekt und Thread-ID.

## Natürlicher Gesprächskontext

Im Telefongespräch darf Felix Ziel und Inhalt auch natürlich über mehrere Aussagen festlegen. Die Brücke merkt ausschließlich einen im aktuellen Nutzertranskript exakt genannten vollständigen Thread-Titel oder eine Thread-ID. Diese sitzungsgebundene Auswahl gilt höchstens vier weitere Nutzeraussagen und wird bei einem anderen exakt genannten Thread ersetzt. Historische Anrufe, der zuletzt aktive Vorgang, Titelähnlichkeit oder die Reihenfolge der T3-Shell dürfen kein Ziel auswählen.

Beispiele:

- Nach „Ich meine den Thread Nachrichten an abgeschlossene T3-Threads senden“ genügt „Schick dort Test hin“.
- Nach „Beim Thread Nachrichten an abgeschlossene T3-Threads senden: Die Nachricht ist Test“ genügt „Mach das“.
- Fehlt das Ziel, fragt die Brücke nach Titel oder Thread-ID und akzeptiert die natürliche Antwort.
- Fehlt der Inhalt, fragt die Brücke nach der Nachricht und verwendet die nächste eindeutige Antwort als Inhalt.
- Bei gleichen Titeln nennt die Brücke die passenden Projekte und IDs. Die anschließende Auswahl per ID setzt den begonnenen Sendewunsch fort.

Diese Kontextbindung gilt nur innerhalb des laufenden Telefonats. Telegram-Text ohne Gesprächssitzung verwendet weiterhin die explizite Einzelaussage. Ein Themenwechsel zu einem T3-Rückfragevorgang oder neuen Auftragsvorschlag verwirft die Bindung. „Mach das“ ohne eindeutig gebundenes Ziel und Inhalt löst keine Folgenachricht aus.

## Getrennte Dialogwege

Explizit adressierte Folgenachrichten werden vor einer gebundenen Rückfrage oder einem neuen Auftragsvorschlag ausgewertet. Eine offene Rückfrage im Zielthread blockiert diesen Versandweg und verweist auf den zugehörigen Rückfragevorgang. Sie wird weder automatisch beantwortet noch durch einen normalen Turn ersetzt. Normale Rückfrageantworten laufen weiterhin durch die bestehende Ask-/Bestätigungslogik.

Eine Folgenachricht erstellt keinen Arbeits-Thread und keinen Auftrag. Ein dazwischenliegender anderer Gesprächswunsch löst die aktuelle Bindung an einen Auftragsvorschlag. Ein späteres bloßes Ja kann diesen alten Vorschlag nicht starten. Ein gespeicherter Vorschlag muss erneut vorgelegt und frisch bestätigt werden.

## Zustellung und Wiederholungen

Die Anwendung liest Shell und Ziel-Snapshot über die vorhandene T3-Schnittstelle und prüft Thread-ID, Projekt, Titel, Verfügbarkeit und offene Rückfragen. Archivierte, gelöschte, interne Koordinationsthreads und Projekte außerhalb des Dienstumfangs sind ausgeschlossen. Sie speichert dann den exakten `thread.turn.start`-Befehl einschließlich stabiler Command-/Message-ID vor dem Dispatch dauerhaft. Laufzeit- und Interaktionsmodus bleiben die des Zielthreads.

Die lokal geprüfte T3-Implementierung in `apps/server/src/orchestration/decider.ts`, Fall `thread.turn.start`, hebt durch die neue Aktivität einen vorhandenen Settlement-Override auf. Dafür ist kein separater `thread.unsettle`-Aufruf nötig. Die Bridge greift nicht auf die T3-Datenbank oder eine zweite Provider-Sitzung zu.

Ein wiederholter Callback desselben Sprachsegments oder dieselbe Telegram-Nachricht sendet nicht erneut. Ein geändertes bereits verarbeitetes Sprachsegment darf denselben Versandplatz nicht neu belegen. Nach Neustart bleibt die Reservierung erhalten. Bei unklarer Zustellung erfolgt nur ein Readback derselben Message-ID, kein automatischer erneuter Versand. Auch ein neuer Sendewunsch mit identischem Ziel und Inhalt umgeht eine unbestätigte Reservierung nicht. Nach bestätigtem Empfang ist eine neue, ausdrücklich wiederholte Nutzeraussage eine neue Nachricht.

Nur der Readback von Message-ID, Rolle und identischem Text im richtigen Thread/Projekt bestätigt den Empfang. Das ist keine Bestätigung erfolgreicher fachlicher Bearbeitung oder eines erfolgreichen Provider-Turns. Eine fehlgeschlagene oder unklare Zustellung wird ausdrücklich so gemeldet. Zwischen Snapshot und Dispatch können externe T3-Aktionen stattfinden; der bestehende Dispatch-Vertrag bietet keine atomare Bedingung auf Projekt oder offene Rückfragen.

## Offline-Nachweis und Auslieferung

`tests/test_followups.py` prüft laufende, completed und settled Threads, den Zieltitel „Nachrichten an abgeschlossene T3-Threads senden“, natürliche deiktische Aussagen, „Mach das“, getrennt genannte Ziele und Inhalte, gezielte Rückfragen, identische Titel in verschiedenen Projekten, Auswahl per ID, offene Rückfragen, unbekannte und unzulässige Ziele, Identitätswechsel, alte Gesprächsinhalte, Vorschlagsbindung, veraltete Sprachrevisionen, doppelte Zustellung, Neustart, Speicherfehler und unklare Netzwerk-/Readback-Ergebnisse. Die übrigen Tests prüfen weiterhin die bestehende Rückfrage- und Auftragsbestätigung.

Damit wäre die Beispielnachricht an „Gesprächskoordination-Threads automatisch setteln“ nach Auslieferung korrekt zustellbar, sofern dieser Titel dann eindeutig vorhanden, verfügbar und ohne offene Rückfrage ist. Das ist ein synthetischer Offline-Nachweis mit dem exakten Titel, keine Prüfung des aktuellen produktiven Threads. Es wurde keine Produktivnachricht versendet und keine laufende Installation aktualisiert.

## Aktivierung am 11. September 2026

Auf erneuten ausdrücklichen Nutzerauftrag wurde die Erweiterung mit dem inzwischen aktivierten Settled-Verhalten zusammengeführt und in die lokale Feature-Linie `feat/issue-1-telegram-service` übernommen. Der kombinierte Stand `59d9a27` besteht `make check` mit 189 Tests. Das Release `40f729dcec7af961` enthält exakt dessen Python-Quellen und die bisherigen nativen Laufzeitdateien.

Der LaunchAgent wurde nach frischer Prüfung auf ein freies Gesprächsfenster vollständig beendet und mit dem neuen Release gestartet. Der Vorgängerprozess 90121 war vor dem Start beendet. Der neue Prozess 3647 meldete am 11. September 2026 um 21:15:22 UTC `running=true`, 19 Projekte, aktivierte Auftragserstellung, keinen aktiven Anruf und keine Backend-/Gesprächsverlaufsfehler. Das ist ein Nachweis der aktivierten Installation, kein neuer Hörtest oder Produktivversand einer Folgenachricht. Die vorherige LaunchAgent-Konfiguration und Release-Metadaten bleiben privat für einen möglichen Rückwechsel erhalten.
