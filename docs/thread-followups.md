# Ausdrückliche Folgenachrichten an bestehende Threads

Die Telefonbrücke kann eine ausdrücklich adressierte Nachricht als normalen T3-Nutzerturn an einen bestehenden Thread senden. Das funktioniert unabhängig von einem früheren Telefonvorgang und auch bei laufenden, completed oder settled Threads.

## Formulierung

Beispiele für Sprache und Telegram-Text:

- `Sende an Thread „Gesprächskoordination-Threads automatisch setteln“: Bitte prüfe auch den Fehlerfall.`
- `Sende an Thread „Gesprächskoordination-Threads automatisch setteln“ mit der Nachricht Bitte prüfe auch den Fehlerfall.`
- `Sag dem Thread „vollständiger Titel“, dass bitte auch der Fehlerfall geprüft werden soll.`
- Bei gleichen Titeln: `Sende an Thread thread-id: deine Nachricht.`

Ziel und Nachrichteninhalt müssen in derselben aktuellen Nutzeraussage stehen. Titel werden vollständig und ohne Unterscheidung der Groß-/Kleinschreibung verglichen. Teiltreffer, Ähnlichkeitsranking, frühere Gesprächsinhalte oder der zuletzt aktive Vorgang wählen niemals das Ziel. Mehrere passende Threads führen zu einer Rückfrage mit Projekt und Thread-ID. Felix wiederholt danach den vollständigen Sendewunsch mit dieser ID. Unvollständige Sendewünsche führen zu einer Bitte um eindeutige Formulierung. Freie Paraphrasen außerhalb der dokumentierten Formen werden nicht als Sendeberechtigung interpretiert.

## Getrennte Dialogwege

Explizit adressierte Folgenachrichten werden vor einer gebundenen Rückfrage oder einem neuen Auftragsvorschlag ausgewertet. Eine offene Rückfrage im Zielthread blockiert diesen Versandweg und verweist auf den zugehörigen Rückfragevorgang. Sie wird weder automatisch beantwortet noch durch einen normalen Turn ersetzt. Normale Rückfrageantworten laufen weiterhin durch die bestehende Ask-/Bestätigungslogik.

Eine Folgenachricht erstellt keinen Arbeits-Thread und keinen Auftrag. Ein dazwischenliegender anderer Gesprächswunsch löst die aktuelle Bindung an einen Auftragsvorschlag. Ein späteres bloßes Ja kann diesen alten Vorschlag nicht starten. Ein gespeicherter Vorschlag muss erneut vorgelegt und frisch bestätigt werden.

## Zustellung und Wiederholungen

Die Anwendung liest Shell und Ziel-Snapshot über die vorhandene T3-Schnittstelle und prüft Thread-ID, Projekt, Titel, Verfügbarkeit und offene Rückfragen. Archivierte, gelöschte, interne Koordinationsthreads und Projekte außerhalb des Dienstumfangs sind ausgeschlossen. Sie speichert dann den exakten `thread.turn.start`-Befehl einschließlich stabiler Command-/Message-ID vor dem Dispatch dauerhaft. Laufzeit- und Interaktionsmodus bleiben die des Zielthreads.

Die lokal geprüfte T3-Implementierung in `apps/server/src/orchestration/decider.ts`, Fall `thread.turn.start`, hebt durch die neue Aktivität einen vorhandenen Settlement-Override auf. Dafür ist kein separater `thread.unsettle`-Aufruf nötig. Die Bridge greift nicht auf die T3-Datenbank oder eine zweite Provider-Sitzung zu.

Ein wiederholter Callback desselben Sprachsegments oder dieselbe Telegram-Nachricht sendet nicht erneut. Ein geändertes bereits verarbeitetes Sprachsegment darf denselben Versandplatz nicht neu belegen. Nach Neustart bleibt die Reservierung erhalten. Bei unklarer Zustellung erfolgt nur ein Readback derselben Message-ID, kein automatischer erneuter Versand. Auch ein neuer Sendewunsch mit identischem Ziel und Inhalt umgeht eine unbestätigte Reservierung nicht. Nach bestätigtem Empfang ist eine neue, ausdrücklich wiederholte Nutzeraussage eine neue Nachricht.

Nur der Readback von Message-ID, Rolle und identischem Text im richtigen Thread/Projekt bestätigt den Empfang. Das ist keine Bestätigung erfolgreicher fachlicher Bearbeitung oder eines erfolgreichen Provider-Turns. Eine fehlgeschlagene oder unklare Zustellung wird ausdrücklich so gemeldet. Zwischen Snapshot und Dispatch können externe T3-Aktionen stattfinden; der bestehende Dispatch-Vertrag bietet keine atomare Bedingung auf Projekt oder offene Rückfragen.

## Offline-Nachweis und Auslieferung

`tests/test_followups.py` prüft laufende, completed und settled Threads, den oben genannten Zieltitel, offene Rückfragen, identische Titel in verschiedenen Projekten, unbekannte und unzulässige Ziele, Identitätswechsel, alte Gesprächsinhalte, Vorschlagsbindung, veraltete Sprachrevisionen, doppelte Zustellung, Neustart, Speicherfehler und unklare Netzwerk-/Readback-Ergebnisse. Die übrigen Tests prüfen weiterhin die bestehende Rückfrage- und Auftragsbestätigung.

Damit wäre die Beispielnachricht an „Gesprächskoordination-Threads automatisch setteln“ nach Auslieferung korrekt zustellbar, sofern dieser Titel dann eindeutig vorhanden, verfügbar und ohne offene Rückfrage ist. Das ist ein synthetischer Offline-Nachweis mit dem exakten Titel, keine Prüfung des aktuellen produktiven Threads. Es wurde keine Produktivnachricht versendet und keine laufende Installation aktualisiert.
