# Direkter Gesprächspfad ohne Koordinator-Thread

Stand: 17. September 2026. Auf ausdrücklichen Nutzerwunsch wurde die mittlere Agent-Schicht aus dem Gesprächspfad genommen, weil Antworten am Telefon zu lange dauerten. Der Umbau wurde am selben Tag als Release `dc224d086f9fc59c` aktiviert und in zwei echten Telefonaten geprüft; siehe Nachweise. Die daraus abgeleiteten Korrekturen für Begrüßung, Auflegen, Warteton und Statuskontext sind offline getestet und noch nicht aktiviert.

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

## Korrekturen nach den ersten beiden Telefonaten

### Vom Nutzer gemeldet

- **Keine Begrüßung von selbst.** Kein Rückschritt: In allen gespeicherten Gesprächen, auch vor dem Umbau, sprach zuerst der Nutzer. Eine Anweisung im Prompt löst keine erste Antwort aus. Laut [offizieller Anleitung](https://developers.openai.com/api/docs/guides/live-conversations) braucht es nach `session.started` ein `session.instructions.append` mit `delegation_id: null`. Die Brücke sendet es jetzt genau einmal.
- **Auflegen ohne hörbaren Abschied.** Zwischen erkannter Bitte und Auflegen lagen fest 1,5 Sekunden. Das reicht nicht für Erzeugung, Echtzeit-Ausgabe und Telegram-Puffer. Jetzt wartet die Brücke, bis die Verabschiedung des Modells vollständig an den Telefonweg übergeben ist, plus 0,6 Sekunden. Bleibt das Modell stumm, wird es nach 2 Sekunden einmal zur Verabschiedung aufgefordert. Beginnt die Verabschiedung erst während der letzten Wartezeit, wird sie trotzdem ausgespielt. Spätestens nach 10 Sekunden wird aufgelegt.
- **Auflegebitte nicht erkannt.** Die Spracherkennung lieferte `Mhm. Das- dann kannst du jetzt auflegen. Danke`. Das strenge Muster verlangt, dass die gesamte Äußerung passt. Neu gibt es zusätzlich einen lockeren Hinweis auf eine Auflegebitte. Er beendet ein Gespräch nie allein, sondern nur, wenn sich das Modell direkt danach selbst verabschiedet. Als Verabschiedung zählt nur ein Abschiedswort am Ende der Antwort, keine Frage und keine gespiegelte Begrüßung zu Gesprächsbeginn. Verneinungen, Fragen, Einschübe wie bevor oder vorher und Aussagen über Nachrichten oder Texte sind ausgeschlossen.
- **Wunsch nach einem Warteton.** Während das Backend eine Delegation beantwortet und weder Modell noch Anrufer sprechen, spielt die Brücke etwa alle 1,6 Sekunden zwei leise ansteigende Töne von zusammen 230 ms. Nie über oder direkt nach Sprache, nie während des Auflegens. Der Ton zählt nicht als API-Ausgabe. Abschaltbar mit `"wait_tone": false` in `live.json`.

### Eigene Befunde aus Log und Review

- **Rücknahme einer Auflegebitte.** Das längere Wartefenster machte sichtbar, dass eine Rücknahme wie „Halt, eine Frage habe ich doch noch“ ignoriert wurde. Eine neue Aussage mit mindestens vier Wörtern oder einem Wort wie halt, warte oder moment hebt die Bitte jetzt auf. Kurze Höflichkeiten und ein erwidertes Tschau heben sie nicht auf.
- **Transkript nach dem Audio.** Trifft das Transkript des Anrufers erst nach dem Audio der Verabschiedung ein, zählte diese nicht als begonnen. Maßgeblich ist jetzt auch die laufende Echtzeit-Ausgabe der Modellsprache.
- **Statuskontext bei eingehenden Anrufen.** Das Log zeigte `voice_context_prefetch_unused`: Ein eingehender Anruf ist schneller verbunden als jedes Vorabladen. Der Dienst hält den kompakten Stand jetzt im Leerlauf höchstens 60 Sekunden alt bereit und setzt ihn vor dem Annehmen ein. Ist er älter als 3 Minuten, wird wie bisher nachgeladen. Ausgehende Anrufe laden weiterhin frisch, damit ihre eigene Rückfrage enthalten ist. Nach jedem Anruf verfällt der bereitgehaltene Stand. Ein Fehler dabei beendet weder den Suchlauf noch einen Anruf.
- **Provider-Katalog außerhalb gesprochener Wechsel.** Die Statusfrage brauchte 11,91 Sekunden, weil der Statuspfad bei jeder Frage den Provider-Katalog auffrischte und dafür `codex app-server`-Prozesse startete. Die Promptdaten verwenden den Katalog jetzt bis zu 10 Minuten weiter; er wird beim Anrufbeginn vorgewärmt. Vorschlag, Validierung und Start prüfen weiterhin frisch.
- **Feinere Diagnose:** `structuring_seconds` mit `path` `direct` oder `coordinator` trennt die Modellzeit von der gesamten `backend_reply_seconds`. `caller_speech_during_wait` meldet, wenn während einer Wartezeit Anrufersprache erkannt wurde. Das wäre auch das Anzeichen dafür, dass der Warteton als Echo in die Spracherkennung gelangt und dadurch ausstehende Antworten verworfen würden.

## Nachweise und offene Punkte

- PR #5 wurde gemergt (`009987c`) und als festes Release `dc224d086f9fc59c` aktiviert. Der Vorgängerdienst wurde ohne aktives Gespräch geordnet beendet; der neue Prozess meldete 19 Projekte und keinen Backend-Fehler. Rückwechsel-Kopien liegen privat als `rollback-be23bd82528ad0e4.plist`.
- Echter Strukturierungs-Aufruf mit synthetischem Text: gültiges JSON, das die Zitat-Prüfung besteht. Gemessen 3,85 und 2,37 Sekunden vom Arbeitscheckout, 1,60 Sekunden für einen Minimalprompt mit dem Dienst-Interpreter. Das ist langsamer als die zuvor geschätzte eine Sekunde.
- Ausgehender Probeanruf zur Demo-Aufgabe: Das Modell erklärte den Bericht aus dem Kontext und las die Entscheidung selbst vor. Nach dem Ja folgte eine einzige Delegation mit `backend_reply_seconds` 5,78 einschließlich T3-Rückgabe und Warten auf die Reaktion der Aufgabe. `t3_answer_resolved` wurde gemeldet. Es gab keinen `structurer_fallback` und keinen Koordinator-Thread.
- Eingehender Probeanruf mit Statusfrage: eine Delegation mit 11,91 Sekunden, Statuskontext nicht rechtzeitig. Ursachen und Korrekturen stehen im vorigen Abschnitt.
- `make check` besteht mit 261 Offline-Tests. Die Tests für Abschied und Füllwörter reproduzierten die beiden gemeldeten Fehler vor der Korrektur: 12 von 15 Audioblöcken gespielt beziehungsweise kein Auflegen. Ein unabhängiger Review fand danach weitere Schwächen in der Auflegelogik und beim bereitgehaltenen Stand; jede wurde zuerst mit einem scheiternden Test belegt.
- Offen: Aktivierung der Korrekturen, Hörtest für Begrüßung, Abschied und Warteton sowie gemessene Zeiten für Statusfragen nach der Korrektur. Ob der Warteton über den Lautsprecher des Telefons zurück in die Spracherkennung gelangt, ist ungeprüft; `caller_speech_during_wait` im Dienstlog zeigt es. Nicht behoben: Bereits eingereihtes Audio einer längeren Antwort wird beim Dazwischensprechen nicht verworfen.
