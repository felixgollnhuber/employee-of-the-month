# Telegram-Folgedialog und eingehende Anrufe

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

`--max-calls 0` deaktiviert automatische ausgehende Anrufe. Nachrichten und eingehende Anrufe bleiben aktiv. Laufzeit maximal 24 Stunden, Anrufdauer maximal 180 Sekunden, höchstens 20 automatische Anrufe pro Dienststart. Bereits versuchte Vorgänge bleiben auch über diese Starts hinweg gesperrt.

Mit `--library PFAD` und `--media-runtime PFAD` können vorhandene TDLib-Abhängigkeiten und eine separat gebaute neue Medienlaufzeit verwendet werden. Auf diesem Mac liegt der isoliert geprüfte neue Medienbuild unter `.build/issue-1-native/media_runtime`; Python-Umgebung und TDLib können aus `../codex-phone-bridge-telegram/.build/` stammen. Der aktuelle Python-Quellstand muss dabei aus diesem Checkout geladen werden.

## Abnahme

Automatisch und ohne Laufzeitaktionen geprüft: 127 Python-Tests einschließlich der neuen Dialog- und Diensteigenschaften, elf native Descriptor-/IPC-Prüfungen für beide Anrufrichtungen sowie PCM-Callbacks ohne Gerätezugriff.

Noch offen ist ein bewusst gestarteter echter Abnahmelauf: nicht abheben, ablehnen, vor der Entscheidung auflegen, Netzunterbrechung, Antwort während des Aufbaus, Text-Rückfrage mit Erläuterung, Textkorrektur, Rückruf, neuer Statusanruf und fremdes Konto. Dabei tatsächlichen Telegram-Versand, hörbare eingehende Sprache und passende T3-Rückgabe kontrollieren. Die frühere Nutzerbestätigung ausgehender Gespräche belegt diese neuen Abläufe nicht.
