# Direkter Gesprächspfad ohne Koordinator-Thread

Stand: 17. September 2026. Auf ausdrücklichen Nutzerwunsch wurde die mittlere Agent-Schicht aus dem Gesprächspfad genommen, weil Antworten am Telefon zu lange dauerten. Dieser Stand ist offline getestet. Er ist noch nicht im laufenden Dienst aktiviert und noch nicht in einem echten Telefonat geprüft.

## Vorher und nachher

Vorher liefen bei jeder inhaltlichen Aussage drei Agents nacheinander: GPT-Live, ein eigener T3-Koordinator-Thread als JSON-Klassifikator und gegebenenfalls der Arbeits-Thread. Pro Anruf kamen bis zu vier Agents zusammen, weil Statusfragen einen zweiten Koordinator-Thread verwendeten. Der Koordinator war ein vollwertiger Coding-Agent mit `thread.turn.start`, Polling alle 250 ms und Kaltstart beim ersten Turn.

```text
Vorher:  Felix <-> GPT-Live -> Brücke -> Koordinator-Thread (Agent) -> Brücke -> Arbeits-Thread
Nachher: Felix <-> GPT-Live -> Brücke mit einem Strukturierungs-Aufruf  ->        Arbeits-Thread
```

Nachher gibt es pro Anruf zwei Agents: GPT-Live und den Arbeits-Thread.

## Was sich geändert hat

- **GPT-Live führt das Gespräch.** Die frühere Regel, jede inhaltliche Aussage zu delegieren, ist ersetzt. GPT-Live erklärt Rückfragen aus dem mitgegebenen Kontext, liest Entscheidungen selbst vor und holt das Ja ein. Delegiert wird nur noch, wenn in T3 etwas passieren oder frisch gelesen werden soll. Das entspricht der offiziellen [Prompting-Empfehlung](https://developers.openai.com/api/docs/guides/live-prompting).
- **Status liegt beim Anrufbeginn in der Sitzung.** Während es klingelt, lädt die Brücke einen kompakten Stand mit offenen Rückfragen und den neuesten Aufgaben und hängt ihn an die Instruktionen an, bevor `session.start` gesendet wird. Kommt der Stand zu spät, wird er verworfen, weil ein späteres `session.instructions.append` laufende Sprache unterbrechen kann. Statusfragen laufen dann wie bisher über das Backend.
- **Ein zustandsloser Strukturierungs-Aufruf ersetzt den Koordinator-Thread.** `telegram_bridge/structurer.py` sendet genau eine Anfrage an die Responses-API: JSON-Modus, `reasoning.effort=none`, `store=false`, kein Thread, kein Polling. Er erhält denselben Prompt und liefert denselben JSON-Vertrag wie zuvor der Koordinator.
- **Alle Prüfungen bleiben unverändert in Python:** Bestätigungszitat in der letzten Nutzeraussage, Frage-IDs, Revision bei neuen Aussagen, stabile Command-IDs, Readback vor Erfolgsmeldungen. Ein Offline-Test belegt, dass eine Antwort ohne passendes Zitat auch über den neuen Weg nicht übertragen wird.
- **Der Koordinator-Thread bleibt als langsamer Rückfallweg.** Schlägt der Strukturierungs-Aufruf fehl, meldet das Log `structurer_fallback` mit dem Fehlercode, und der bisherige Weg übernimmt. Settle-Logik und Ausschluss interner Threads gelten dafür weiter.
- **Status-Snapshots werden innerhalb eines Anrufs bis zu 15 Sekunden wiederverwendet.** Das gilt nur für Statusauskünfte. Antworten, Rückgaben und T3-Befehle lesen weiterhin frisch.
- **Zeitmessung:** Jede Backend-Antwort meldet `backend_reply_seconds` im Dienstlog. Das ist die Stille, die Felix pro Delegation hört. Es wird nur die Dauer protokolliert, kein Text.

## Konfiguration und Kosten

Der Aufruf verwendet den vorhandenen OpenAI-Projekt-Key aus `live.json`. Das Modell ist `gpt-5.6-luna` und lässt sich mit `"structuring_model": "..."` in `live.json` ändern. `"structuring_model": null` schaltet den direkten Weg ab; dann läuft wieder ausschließlich der Koordinator-Thread.

Das ist eine bewusste Änderung der früheren Festlegung, keine zusätzlichen API-Modelle zu konfigurieren. Laut [Preisliste](https://developers.openai.com/api/docs/pricing) kostet das Modell 0,20 US-Dollar je Million Eingabe-Tokens und 1,20 US-Dollar je Million Ausgabe-Tokens. Bei wenigen Aufrufen pro Anruf mit jeweils einigen tausend Tokens sind das Bruchteile eines Cents pro Gespräch. Die Prompts enthalten Transkript und begrenzten Aufgabenkontext wie zuvor beim Koordinator; gängige Schlüsselformate sind bereits ausgeblendet.

## Geprüfte Alternative: TypeSafe Jev

[TypeSafe Jev](https://docs.typesafe.ai/introduction) liefert Auswahl, Score und Ja/Nein mit Wahrscheinlichkeiten. Es erzeugt keinen Freitext, den wir für weitergegebene Rückfragen und Auftragsvorschläge brauchen. Deutsch ist nicht dokumentiert, doppelte Verneinungen sind als Schwäche genannt, und Transkripte gingen an einen weiteren Anbieter. Deshalb nicht eingesetzt. Die Strukturierung liegt hinter einer einzigen Funktion und ließe sich später für den reinen Klassifikationsteil austauschen.

## Nachweise und offene Punkte

- `make check` besteht mit 236 Offline-Tests. Neu sind Tests für den Strukturierungs-Aufruf, den Rückfallweg, das Zitat-Gate über den neuen Weg, Kontext vor und nach `session.start`, das Vorabladen beim Anrufstart samt Fehlerfall, die Snapshot-Wiederverwendung, die neuen Gesprächsregeln und die Zeitmessung.
- Request-Form und Modellname folgen der offiziellen Dokumentation vom 17. September 2026. Ein echter API-Aufruf wurde nicht ausgeführt, weil Netzaktionen und Anrufe eigenständige bewusste Laufzeitaktionen sind.
- Offen: echter Aufruf des Strukturierers, Hörtest mit den neuen Gesprächsregeln, gemessene `backend_reply_seconds` aus einem echten Anruf und die Aktivierung als neues Release. Die zuvor genannten Zeiten von 12 bis 45 Sekunden und die erwarteten 1 bis 3 Sekunden sind Schätzungen aus dem Code, keine Messungen.
