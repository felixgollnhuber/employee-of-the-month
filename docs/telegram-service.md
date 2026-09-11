# Telegram-Folgedialog und eingehende Anrufe

Aktueller Dauerbetrieb: [alle T3-Projekte, neue Feature-Aufträge und Modellwahl](all-projects-service.md). Der unten beschriebene Einzelprojektmodus bleibt für ausdrücklich eingegrenzte Starts verfügbar.

Der explizit gestartete `serve-t3`-Dienst bearbeitet genau ein T3-Projekt und das konfigurierte persönliche Telegram-Konto. Er vereint ausgehende Anrufe, Telegram-Textdialoge und eingehende Audioanrufe in einem TDLib-Client. GPT-Live 1, Cedar und die vorhandene T3-Delegation bleiben der Sprachpfad.

## Verhalten

- Eine offene T3-Rückfrage erhält eine dauerhaft gespeicherte Vorgangs-ID. Ausgehender Kontakt frühestens drei Minuten nach Erstellung der Frage, höchstens einmal automatisch pro Rückfrage.
- Bleibt die Frage nach einem Kontaktversuch offen, folgt eine kurze Telegram-Nachricht mit Anlass, Frage und Vorgangs-ID. Bereits in T3 erledigte Fragen erhalten keine erste Folgenachricht.
- Telegram-Antworten und nachgeladene Nachrichtenänderungen werden anhand des persönlichen Absenders und privaten Chats geprüft. Antworten auf eine Nachricht haben deren feste Vorgangszuordnung. Eine ausdrücklich genannte Vorgangs-ID darf dieser Zuordnung nicht widersprechen. Bei mehreren möglichen Vorgängen fragt der Dienst nach.
- Mehrteilige Dialoge übernehmen Erläuterungen aus T3. Eine eindeutige schriftliche Entscheidung braucht keine zusätzliche Bestätigungsrunde. Eine Rückfrage gilt weiterhin nicht als fachliche Entscheidung. Explizit zugeordnete spätere Korrekturen gehen als neue Folgeeingabe an denselben Thread, niemals nochmals an eine erledigte Ask-ID.
- Die Abschlussnachricht folgt erst nach bestätigter T3-Rückgabe. Ist deren Ausgang unklar, bleibt die Übertragung gesperrt, bis der Zustand direkt in T3 geprüft wurde. Es gibt keinen automatischen Wiederholungsversuch.
- Ein Rückruf kann einen offenen Vorgang fortsetzen. Bei mehreren Vorgängen klärt das Gespräch zunächst die Auswahl. Ein neuer eingehender Anruf kann den aktuellen Stand, offene Fragen und Probleme der Aufgaben im konfigurierten Projekt abfragen. Fremde Konten erhalten keine Projektdaten und keine Medieninstanz.
- Auflegen beendet den Anruf, nicht die Arbeitsfrage. „Jetzt nicht“ und erkannte Vertagungen pausieren den Vorgang. Es gibt **keine zeitgesteuerte Erinnerung und keinen automatischen Wiederanruf**. Eine neue Nachricht oder ein Rückruf setzt den Dialog fort.
- Eine Telegram-Antwort während des Anrufaufbaus beendet diesen Aufbau. T3-Dialogarbeit läuft serialisiert außerhalb der Telegram-Empfangsschleife; Medien- und Auflegeereignisse bleiben verarbeitbar.

## Persistenz und Fehlerfälle

`conversations.json` liegt ausschließlich im privaten Profil, mit Modus 0600 im Verzeichnis 0700. Es enthält Vorgangs-, Thread-, Ask- und Kontakt-IDs, begrenzten ausgewählten Textkontext, Dialogzustand, bereits verarbeitete Nachrichteninhalte als Hash und Versandzuordnungen. Keine Audioaufnahmen oder API-Schlüssel werden darin gespeichert. Der Dienst begrenzt die Datei auf 8 MiB; bei Erreichen ist eine bewusste Archivierung im gestoppten Zustand erforderlich.

Vor jedem Kontaktversuch, Versand und jeder T3-Mutation wird der Zustand atomar gespeichert und synchronisiert. Doppelte Updates, Reconnects und Neustarts erzeugen keine automatische Wiederholung. Der Versandstatus unterscheidet reserviert, ausstehend, gesendet und fehlgeschlagen. „Reserviert“ nach einem Absturz kann einen bereits ausgeführten Versand bedeuten. Deshalb wird nicht blind erneut gesendet. Das ist eine Vermeidung doppelter Versuche, keine Behauptung garantierter Zustellung bei jedem Absturzzeitpunkt.

Der Dienst übernimmt zu noch offenen Fragen vorhandene Einträge aus `watch-attempts.json` und wählt diese nicht erneut. Der alte Wächter hat keinen vollständigen dauerhaften Gesprächszustand geschrieben; bereits erledigte Ask-IDs mit nur mündlich offen gebliebener fachlicher Entscheidung können daraus nicht rekonstruiert werden.

Ein Verlust der Anmeldung, ein unbestätigtes Gesprächsende oder eine unbestätigte Medienbereinigung stoppt den Dienst. Nach Prüfung ist ein expliziter Neustart möglich. Eine unklare T3-Mutation wird nicht durch Wiederholung einer Telegram-Nachricht erneut ausgeführt. Nachrichten aus der Zeit vor der erstmaligen Dienstaktivierung werden ignoriert.

## Settled-Status der Koordinations-Threads

Jedes Telefongespräch erzeugt in T3 höchstens zwei Arten technischer Threads mit dem Titel „Telefonbrücke - Gesprächskoordination“: den Status-Koordinator des Gesprächs und je behandeltem Vorgang den Rückfrage-Koordinator. Sobald das Telefongespräch vollständig beendet ist, markiert der Dienst diese Threads über den vorhandenen T3-Befehl `thread.settle` als settled. Sie verschwinden damit aus der aktiven T3-Übersicht, bleiben aber unarchiviert und für Rückrufe wiederverwendbar. Fachliche Ziel- und Arbeits-Threads werden nie automatisch verändert: Der Befehl geht nur an Thread-IDs, die die Brücke selbst angelegt hat, und vor jedem Befehl wird zusätzlich der Thread-Titel geprüft. Ein Thread ohne diesen Titel wird ohne Wiederholung verworfen.

| Gesprächsende | Erkennung | Settled-Verhalten |
| --- | --- | --- |
| Regulär beendet: Auflegen, gesprochene Auflegebitte, Zeitlimit, Dienstende | Sitzungsphase `ended` | Status- und Vorgangs-Koordinator werden dauerhaft vorgemerkt und beim nächsten Leerlauf-Durchlauf gesettelt. |
| Unterbrochen: Nichtabheben, Ablehnung, Netzverlust, Telegram-Antwort während des Aufbaus | Sitzungsphase `ended` mit Grund, zum Beispiel `startup_timeout` oder `telegram_text_received` | Wie regulär beendet. Der Grund wird mit der Vormerkung gespeichert. |
| Fehlerhaft beendet: Medienfehler, Aufbau- oder Annahmefehler | Sitzungsphase `failed` | Wie regulär beendet. |
| Dienstende während eines Gesprächs | `close()` beendet den Anruf | Vormerkung erfolgt nach dem Stopp des Arbeitsthreads; der nächste Dienststart führt sie aus. |
| Absturz oder Neustart während eines Gesprächs | Wiederanlauf: Vorgang mit offenem Kontaktversuch, Gesprächsjournal mit Zustand `interrupted` | Beim ersten Durchlauf nach dem Start vorgemerkt, Grund `service_restart`. |
| Unbestätigtes Ende oder unbestätigte Medienbereinigung | `end_unconfirmed` stoppt den Dienst | Die Vormerkung wird noch abgesetzt, kann aber beim erzwungenen Stopp verloren gehen. Der nächste Start holt sie über Vorgang und Gesprächsjournal nach. |

Die Vormerkungen liegen als `settle_queue` in `conversations.json`, je Koordinator genau ein Eintrag. Ein späterer Anruf mit demselben Koordinator ersetzt den Eintrag; ein bereits gesetteltes Ziel wird ohne neuen Befehl übersprungen. Der Befehl selbst läuft ausschließlich im Leerlauf-Durchlauf des serialisierten Arbeitsthreads, also nie während eines laufenden Gesprächs und nie parallel zu Telegram-Nachrichten. Vor dem Befehl prüft die Brücke dieselben Bedingungen wie der T3-Server: archivierte oder gelöschte Threads gelten als erledigt; laufende Sitzungen oder Turns, offene Genehmigungen oder Rückfragen führen zu einem Wiederholungsversuch nach 30 Sekunden. Jeder Versuch verwendet eine neue Command-ID, weil T3 eine einmal abgelehnte ID dauerhaft ablehnt. Nach 30 Minuten ohne Erfolg wird der Eintrag mit dem Ereignis `coordinator_settle_abandoned` aufgegeben; das Gespräch selbst ist davon nie betroffen. Nach dem Befehl wird der Status über den Thread-Snapshot bestätigt.

Wenn eine spätere Textantwort oder ein Rückruf denselben Vorgangs-Koordinator weiterverwendet, startet ein neuer Turn. T3 hebt den Settled-Status bei dieser Aktivität serverseitig wieder auf; nach dem Ende dieses Gesprächs wird er erneut gesetzt. Die Einzelbefehle `call` und `watch-t3` setteln ihren Koordinator direkt nach dem Anruf mit höchstens fünf Versuchen ohne dauerhafte Warteschlange.

Verbleibende Einschränkungen:

- Nur Telefongespräche lösen den Settled-Status aus. Nach einer reinen Telegram-Textantwort bleibt der Vorgangs-Koordinator aktiv, und der Text-Status-Koordinator (`status_coordinator`) wird nie automatisch gesettelt.
- Ein Koordinator mit offener Genehmigungsanfrage kann nicht gesettelt werden und wird nach 30 Minuten aufgegeben. Ein beim Gesprächsende noch laufender Koordinator-Turn wird nicht unterbrochen, sondern nur später erneut geprüft.
- Bei einem Absturz ohne journalisierte Koordinator-ID gibt es nichts nachzuholen; ein solcher Thread bleibt aktiv.
- Der Befehl wurde gegen die Contracts und den Decider der lokalen T3-Quelle (`t3code`, Revision `a04198127`) und offline gegen Test-Doubles geprüft. Ein Settle-Durchlauf gegen die laufende T3-Instanz wurde nicht ausgeführt.

## Bewusster Laufzeitstart

Der Befehl versendet echte Nachrichten, kann ausgehende Anrufe starten und nimmt eingehende Anrufe des konfigurierten Kontos mit kostenpflichtigem GPT-Live-Audio an. Er gehört nicht zu `make check`.

Vor der Übernahme den in `project-pilot.json` dokumentierten eigenen Wächter anhand seiner konkreten PID und Kommandozeile prüfen und gezielt beenden. Der Dienst hält sowohl `watch.lock` als auch `session.lock`. Er beendet vorhandene Prozesse nicht selbst und kann sich auch nicht neben einen gerade wartenden alten Wächter setzen. Die native Laufzeit muss die neue eingehende Verschlüsselungsrichtung enthalten; das alte geprüfte Binary reicht dafür nicht.

Nach einem regulären Build:

```sh
.build/live-venv/bin/python -m telegram_bridge serve-t3 \
  --project-id DEINE_T3_PROJEKT_ID \
  --allow-messages-and-calls \
  --service-seconds 3600 --seconds 120 --max-calls 1
```

`--max-calls 0` deaktiviert automatische ausgehende Anrufe. Nachrichten und eingehende Anrufe bleiben aktiv. Laufzeit maximal 24 Stunden, Anrufdauer maximal 1200 Sekunden (20 Minuten), höchstens 20 automatische Anrufe pro Dienststart. Bereits versuchte Vorgänge bleiben auch über diese Starts hinweg gesperrt.

Mit `--library PFAD` und `--media-runtime PFAD` können vorhandene TDLib-Abhängigkeiten und eine separat gebaute neue Medienlaufzeit verwendet werden. Auf diesem Mac liegt der isoliert geprüfte neue Medienbuild unter `.build/issue-1-native/media_runtime`; Python-Umgebung und TDLib können aus `../codex-phone-bridge-telegram/.build/` stammen. Der aktuelle Python-Quellstand muss dabei aus diesem Checkout geladen werden.

## Abnahme

Automatisch und ohne Laufzeitaktionen geprüft: 127 Python-Tests einschließlich der neuen Dialog- und Diensteigenschaften, elf native Descriptor-/IPC-Prüfungen für beide Anrufrichtungen sowie PCM-Callbacks ohne Gerätezugriff.

Im gemeinsamen Live-Test wurden die erste Folgenachricht nach offenem Kontaktversuch, eine Text-Rückfrage mit Erläuterung, der eingehende Rückruf, genau eine passende T3-Rückgabe und die gesprochene Auflegebitte sowie ein neuer Statusanruf geprüft. Der Nutzer bestätigte beide Sprachrichtungen, das Auflegen und die Statusauskunft. Details stehen in [evidence.md](evidence.md).

Noch offen sind gezielte Live-Fälle für Ablehnen, Abbruch vor der Entscheidung, Netzunterbrechung, Antwort während des Aufbaus, Textkorrektur, mehrere offene Vorgänge und ein fremdes Konto.

Beim geordneten Ende des ersten Live-Tests blieb eine Statusantwort als `reserved` stehen. Die spätere Daemon-Erweiterung leert und bestätigt neu ausstehende Versandaufträge vor dem Beenden. Der alte unklare Auftrag wurde nicht erneut gesendet.
