# Dauerhafter Sprachagent für alle T3-Projekte

Seit dem 11. September 2026 ist der gemeinsame Dienst auf diesem Mac aktiviert. Er überwacht alle Projekte der verbundenen T3-Instanz, einschließlich PeakShare. Neu angelegte Projekte und Threads werden beim nächsten Durchlauf automatisch berücksichtigt. Archivierte Threads und interne Gesprächskoordinatoren werden nicht automatisch angerufen. Nach dem vollständigen Ende eines Telefongesprächs werden dessen Gesprächskoordinatoren automatisch als settled markiert; siehe den Abschnitt Settled-Status in [telegram-service.md](telegram-service.md).

## Anrufe, Dialoge und Status

Es bleibt genau ein TDLib-Client für das konfigurierte persönliche Telegram-Konto aktiv. Der Schutz durch die festgelegte Nutzer-ID gilt auch für neue Aufträge. Rückfragen erhalten weiterhin eine feste Zuordnung zu Projekt, Thread und Ask-ID. Die erste automatische Kontaktaufnahme erfolgt nach drei Minuten; zwischen zwei ausgehenden Versuchen liegen mindestens drei Minuten. Pro Rückfrage wird nur einmal automatisch gewählt. „Jetzt nicht“ pausiert den Vorgang bis zu einer Nachricht oder einem Rückruf.

Eingehende Anrufe können offene Vorgänge fortsetzen oder nach Aufgabenstatus fragen. Bei Statusfragen helfen Projektname und Thread-Titel: Der Dienst durchsucht die Titel aller aktiven Threads und lädt einen begrenzten, ausdrücklich gekennzeichneten Ausschnitt der passenden beziehungsweise aktuellen Aufgaben. Er überträgt nicht ungefragt komplette Projektverläufe.

## Neue Feature-Aufträge per Sprache

Beispiel: „Baue in PeakShare eine Suche nach Energiegemeinschaften.“ Der Agent klärt fehlenden Kontext und schlägt vor:

- Zielprojekt, Thread-Titel und konkreten Auftrag.
- Provider-Instanz beziehungsweise Account, Modell und Reasoning-Effort.
- Begründung anhand der Aufgabenkomplexität und der verfügbaren Limitfenster.

Nach einer neuen ausdrücklichen Bestätigung wie „Ja, starte“ wird ein neuer T3-Thread mit genau dieser Auswahl angelegt und sein erster Arbeitsschritt gestartet. Der Auftrag läuft danach unabhängig vom Telefonat weiter. Der Thread erhält den Implementierungsauftrag und muss die jeweiligen AGENTS.md und Projektregeln beachten. Bestehende Branches und fremde aktive Worktrees werden nicht vom zuletzt verwendeten Thread übernommen.

Der Zustand enthält feste Thread-, Nachrichten- und Command-IDs. Nach einem unklaren Ergebnis wird zuerst derselbe Thread zurückgelesen. Ein bereits versuchter Turn-Start wird nicht blind wiederholt. Eine erfolgreiche Rückmeldung erfordert die passende Nutzernachricht sowie einen gestarteten oder bereits abgeschlossenen Turn in T3. Eine zusätzliche Telegram-Nachricht bestätigt den Start.

## Modell- und Account-Auswahl

Der Modellkatalog und die erlaubten Optionen kommen aus der laufenden T3-Instanz. Die Brücke akzeptiert keine erfundenen Modelle, Provider-IDs oder Reasoning-Stufen. Für Codex fragt sie `account/read` und `account/rateLimits/read` getrennt unter dem jeweiligen effektiven Home-Verzeichnis ab; ein konfiguriertes Shadow-Home hat Vorrang. Es werden keine Anmeldungen getauscht, Schlüssel kopiert oder Limit-Resets verbraucht. Andere aktivierte Provider verwenden die von T3 gemeldeten Usage-Fenster.

Beim Vorschlag werden kurze und lange Limitfenster einschließlich Reset-Zeit berücksichtigt. Veraltete oder fehlende Werte gelten als unbekannt, nicht als freie Kapazität. Ein laut frischer Abfrage vollständig ausgeschöpfter Account wird nicht gestartet. Vor dem Start wird erneut geprüft. Prozentwerte verschiedener Kontingente werden nicht zu einem vermeintlichen Tokenbudget addiert. Gleiche Account-Identitäten werden im Vorschlag als gemeinsames Kontingent behandelt. Standard-Service-Tier wird explizit gesetzt, sofern das Modell ihn anbietet.

Aktueller Befund auf diesem Mac: Codex-1 und Codex-2 sind zwei T3-Instanzen, melden aber dieselbe Account-Identität und dasselbe Wochenkontingent. Auch die lokal gespeicherten Account-IDs stimmen überein. Eine tatsächlich zweite unabhängige Anmeldung würde automatisch als solche eingelesen; derzeit stehen damit nicht zwei unabhängige Codex-Budgets zur Verfügung. Claude hat eigene Usage-Fenster.

Die Account-Schnittstellen und die Modellkatalog-Abfrage orientieren sich an der [offiziellen App-Server-Dokumentation](https://developers.openai.com/codex/app-server). Die Empfehlung für komplexe Aufgaben berücksichtigt die [offizielle Modellbeschreibung von GPT-6 Astra](https://developers.openai.com/api/docs/guides/latest-model); die konkrete Verfügbarkeit und Optionsauswahl wird stets gegen T3 validiert. Die Einschätzung der passenden Komplexitätsstufe bleibt eine Empfehlung, keine garantierte Kostenvorhersage.

## Betrieb

Der LaunchAgent `eu.colibrie.codex-phone-bridge` startet nach der Benutzeranmeldung und nach einem Prozessende automatisch erneut. Der automatische Wiederanlauf wurde live geprüft. T3 muss erreichbar und der Mac wach sowie online sein; bei ausgeschaltetem oder schlafendem Mac besteht keine Erreichbarkeit. Bestehende T3-Threads und deren Provider-Prozesse werden bei einem Neustart der Telefonbrücke nicht neu gestartet.

Die Laufzeit liegt als feste Kopie unter `~/Library/Application Support/CodexPhoneBridge/service/releases/`. Python-Quellen, TDLib und Medienbinary werden pro Release bereitgestellt; normale Änderungen oder Builds im Arbeitscheckout verändern den laufenden Dienst nicht. Die Python-Umgebung stammt weiterhin aus dem vorhandenen Telegram-Laufzeit-Worktree. Es gibt keinen automatischen Code-Update-Mechanismus.

`service-health.json` im privaten Telegram-Profil enthält den aktuellen PID, den Zeitpunkt der letzten Zustandsmeldung, den Projektumfang, einen gegebenenfalls aktiven Anruf und Backend-Fehler. `service-pilot.json` beschreibt die Betriebsparameter. Die bisherigen Projekt- und PeakShare-Pilotdateien verweisen auf den gemeinsamen Dienst. Geheimnisse stehen weder im LaunchAgent noch im Repository. Das Conversation-Ledger behält seine Größenbegrenzung von 8 MiB.

Ein einzelnes Gespräch ist auf ausdrücklichen Nutzerwunsch auf 1200 Sekunden (20 Minuten) begrenzt. Anrufdienst, Sprachsitzung und native Medienlaufzeit verwenden diese Obergrenze. Der Dienst selbst hat im Daemon-Modus keine geplante Endzeit und keine Gesamtzahl von Anrufen als Laufzeitgrenze. Vorhandene unklare Versandversuche werden nicht erneut gesendet. Beim geordneten Beenden werden neu wartende Nachrichten noch versendet und ihre Bestätigungen begrenzt abgewartet.

## Nachweise

- 154 Offline-Tests einschließlich zusätzlicher Fälle für mehrere Projekte, neu hinzugefügte Projekte, globalen Anrufabstand, getrennte Codex-Profile, unbekannte Limits, gültige Modelloptionen, bestätigte Thread-Anlage, Timeout-Abgleich, Wiederverwendung lokaler Call-IDs nach Neustart und Versand beim Beenden.
- Gegen echtes T3 wurde ein synthetischer Auftrag in einem eigenen Git-Testprojekt gestartet. Mit der vorgeschlagenen Provider-/Modellauswahl entstand eine Begrüßungsfunktion samt unittest; der Test wurde unabhängig erneut erfolgreich ausgeführt. Dieser Nachweis belegt den echten Thread-/Implementierungspfad, nicht die sprachliche Erkennung eines neuen Feature-Auftrags.
- Die zuvor gemeinsam geprüften echten Telegram-Dialoge, Rückrufe, Statusanrufe und Auflegebitten bleiben in [evidence.md](evidence.md) dokumentiert.

Die vollständige Auftragsberatung wurde zusätzlich mit einem synthetischen Transkript gegen den echten T3-Koordinator geprüft: Für eine kleine Funktion schlug er Codex-1, GPT-5.6-Luna und Reasoning low vor, mit konkreter Restkapazität und Begründung. Der Vorschlag erzeugte noch keinen Arbeits-Thread und wurde nach der Prüfung verworfen. Ein dabei gefundener Altprojekt-Fall mit fehlendem Arbeitsverzeichnis wurde korrigiert: Gesprächskoordinatoren verwenden nur vorhandene Git-Arbeitsverzeichnisse.

Nach einem Nutzerbericht über einen stummen Anruf wurde der Medienstart korrigiert: GPT-Live startet erst nach bestätigter Telegram-Medienverbindung. Echtzeitbegrenzung der PCM-Ausgabe und eine getrennte Empfangs-/Ausgabeverarbeitung sichern den Übergang ab. Der Fix ist aktiviert; der erneute Hörtest ist noch offen.

Der LaunchAgent verwendet `ProcessType=Interactive` und `LegacyTimers=true`. Ein lokaler Vergleich zeigte, dass die vorherige Hintergrundklassifikation die 10-ms-Taktschleife auf ungefähr ein Zehntel der erforderlichen Frequenz drosselte. Die korrigierte Konfiguration liefert im Vergleichstest 300 statt 30 Durchläufe in drei Sekunden. Details und die noch ausstehende erneute Hörbestätigung stehen in `evidence.md`.

## Kontext beim Rückruf

Ein neuer Anruf bekommt bereits zum Sprachstart Kontext aus den letzten Gesprächen. Die API-Transkripte werden während des Telefonats lokal gespeichert, auch wenn keine Backend-Delegation stattfindet. Ein eigener Schreiber aktualisiert die private Datei `call-history.json` etwa einmal pro Sekunde; beim geordneten Ende wird abschließend gespeichert. Bei einem abrupten Prozessabbruch kann der letzte noch nicht gespeicherte Augenblick fehlen.

Gespeichert werden höchstens acht Gesprächsverläufe mit jeweils maximal 24000 Textzeichen sowie Zuordnungen zu Vorgängen, Vorschlägen und Koordinatoren. Für den Sprachstart werden begrenzte Ausschnitte der letzten fünf Gespräche und die aktuellen gespeicherten Auftragszustände verwendet. Die Datei ist nur für den lokalen Benutzer lesbar. Audio wird dadurch nicht aufgenommen. Gängige API-Key-, Bearer- und Passwortformen werden im Text ausgeblendet.

Ein neuer Gesprächskoordinator kann mit diesem Kontext anknüpfen. Bestehende T3-Arbeits-Threads bleiben unabhängig vom Telefonat erhalten. Offene Ask-Vorgänge behalten ihre Zuordnung. Ein unbestätigter Feature-Vorschlag kann über seine bestehende ID wieder aufgegriffen werden; der Agent liest Projekt, Auftrag und Modellauswahl erneut vor. Erst eine neue Bestätigung im aktuellen Anruf startet den vorgesehenen Arbeits-Thread. Historische Ja-Aussagen oder eine Begrüßung lösen keinen Start aus.

Für den ersten Anruf nach dieser Erweiterung wurde ein eindeutig über die Vorschlags-ID zuordenbarer Teil des vorherigen helth-Gesprächs aus dem T3-Koordinatorverlauf wiederhergestellt. Dieser Datensatz ist als teilweise wiederhergestellter Verlauf gekennzeichnet. Der echte helth-Auftrag wurde dabei weder bestätigt noch gestartet.
