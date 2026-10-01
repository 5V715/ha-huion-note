# Handwritten commands · Handschriftliche Befehle

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2F5V715%2Fha-huion-note%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fhuion_note%2Ftranscribe_page.yaml)

[English](#english) · [Deutsch](#deutsch)

---

## English

Every page the Huion Note X10 integration saves is read by an AI Task (Claude via the
Anthropic integration). You get the handwriting as text, and lines that start with
**`#` and a keyword** are carried out in Home Assistant. **No `#`, no command:** a line
without it is always just text, whatever it says.

### Commands

| Command (EN) | Command (DE) | What happens | Setting in the blueprint |
|---|---|---|---|
| `#todo` | `#aufgabe` | Item on your to-do list | *#todo / #aufgabe → to-do list* |
| `#shop` | `#einkauf` | Item(s) on your shopping list — one per item | *#shop / #einkauf → shopping list* |
| `#remind <when>` | `#erinnerung <wann>` | To-do with a due date/time | *#remind / #erinnerung → to-do list with due times* |
| `#event <when>` | `#termin <wann>` | Calendar event | *#event / #termin → calendar* |
| `#send` | `#nachricht` | Push message to your phone | *Phone (notify action)* |

Commands work in either language, in any capitalisation. Leave a setting empty to
switch that command off. Controlling devices by handwriting is deliberately not
supported — a misread word must never switch something.

### Confirmation on your phone

Nothing is added just because it was read. Every command **except `#send`** first
arrives as a push message on your phone:

> **Huion Note — run this?**
> Shopping list: milk
> [ ✓ Yes ]  [ ✗ No ]

Only **✓ Yes** carries it out. **✗ No** drops it; no answer within the
*confirmation timeout* (default 60 min) drops it too and removes the question from
your phone. `#send` needs no confirmation — the message itself is the push. The
questions come one after another, in page order.

### Examples

```text
#todo call the landlord
#shop milk, eggs and coffee
#remind fri 18:00 take out the bins
#remind tomorrow pay the rent
#event 12.10. 14:00-15:30 dentist
#event next monday 19:00 dinner with Anna
#send I'll be 10 minutes late

todo: buy a present        ← no #, stays a normal note
```

| Line | Result |
|---|---|
| `#todo call the landlord` | To-do list: "call the landlord" |
| `#shop milk, eggs and coffee` | Shopping list: "milk", "eggs", "coffee" |
| `#remind fri 18:00 take out the bins` | To-do "take out the bins", due next Friday 18:00 |
| `#remind tomorrow pay the rent` | To-do "pay the rent", due tomorrow (all day) |
| `#event 12.10. 14:00-15:30 dentist` | Calendar: "dentist", 12 Oct 14:00–15:30 |
| `#event next monday 19:00 dinner with Anna` | Calendar: 19:00 for the default length (60 min) |
| `#send I'll be 10 minutes late` | Push message with that text |
| `todo: buy a present` | Nothing — no `#` |

### Rules

- The `#` is **mandatory** and must be at the **start of a line**, directly before the
  keyword: `#todo`, not `# todo` or `todo`. A `#` word in the middle of a sentence
  does nothing.
- **When** comes right after the keyword, then the text: `#remind fri 18:00 …`,
  `#event 12.10. 14:00 …`. You may put a colon or dash before the text if it reads
  better (`#remind fri 18:00: …`). Times like `tomorrow`, `next week` work too. Dates like `12.10.` are day.month; times are 24 h. Relative dates are
  counted from when the page is read and always point to the future.
- A date without a time gives an all-day reminder or event.
- Crossed-out command lines are ignored, and so are lines Claude can't read with
  confidence.
- **Writing more on a page:** the page is synced again as a new version. To-dos,
  shopping items, reminders and events that already exist are **not added twice**
  (same text, or an event with the same title at that time) and you are **not asked
  again**. Declined or unanswered commands are asked again. `#send` lines **are sent
  again** — put messages on a fresh page.

### Setup

1. Add the **Anthropic** integration (API key) — it creates a *Claude AI Task*
   entity. For handwriting, open its settings, untick *recommended settings* and
   choose **Claude Opus 5.5** (`claude-opus-5-5`).
2. Install the **Home Assistant companion app** on your phone — it receives the
   confirmation questions and `#send` messages (actionable notifications).
3. Optional: create lists and a calendar, e.g. with **Local To-do** (supports due
   times) and **Local Calendar**.
4. Import the blueprint (button above), create an automation from it and choose the
   AI Task, your language, your phone (`notify.mobile_app_…`), where each command
   goes, the confirmation timeout and the language of the questions.

After each page you get a notification with the transcript and a list of what was
done (✓ added · 📅 event · ✉ sent · ✗ declined or not confirmed · ↺ already there ·
⏭ skipped, not configured). The
`huion_note_page_transcribed` event carries `transcript`, `commands` and `done` for
your own automations.

### Own output folder

The AI Task can only read pages inside a Home Assistant **media folder**. The
integration's default (`/media/huion_notes`) is one. If you set another output folder
in the integration's options, e.g. `/notes`, register it in `configuration.yaml` and
restart Home Assistant:

```yaml
homeassistant:
  media_dirs:
    local: /media      # keep the default — this list replaces it
    notes: /notes
```

The integration then finds the right media folder for each page by itself; there is
nothing to set in the blueprint. If a page is outside every media folder, the
automation stops with a warning in the log.

---

## Deutsch

Jede Seite, die die Huion-Note-X10-Integration speichert, liest ein AI Task (Claude
über die Anthropic-Integration). Du bekommst die Handschrift als Text, und Zeilen,
die mit **`#` und einem Stichwort** beginnen, werden in Home Assistant ausgeführt.
**Ohne `#` kein Befehl:** Eine Zeile ohne `#` bleibt immer normaler Text, egal was
darin steht.

### Befehle

| Befehl (DE) | Befehl (EN) | Was passiert | Einstellung im Blueprint |
|---|---|---|---|
| `#aufgabe` | `#todo` | Eintrag in deiner To-do-Liste | *#todo / #aufgabe → to-do list* |
| `#einkauf` | `#shop` | Eintrag/Einträge auf der Einkaufsliste — einer pro Artikel | *#shop / #einkauf → shopping list* |
| `#erinnerung <wann>` | `#remind <when>` | Aufgabe mit Fälligkeit (Datum/Uhrzeit) | *#remind / #erinnerung → to-do list with due times* |
| `#termin <wann>` | `#event <when>` | Kalendertermin | *#event / #termin → calendar* |
| `#nachricht` | `#send` | Push-Nachricht aufs Handy | *Phone (notify action)* |

Befehle funktionieren in beiden Sprachen und in beliebiger Groß-/Kleinschreibung.
Lass eine Einstellung leer, um den Befehl abzuschalten. Geräte per Handschrift zu
steuern ist absichtlich nicht möglich — ein falsch gelesenes Wort darf nie etwas
schalten.

### Bestätigung auf dem Handy

Nichts wird angelegt, nur weil es gelesen wurde. Jeder Befehl **außer
`#nachricht`** kommt zuerst als Push-Nachricht aufs Handy:

> **Huion Note — ausführen?**
> Einkaufsliste: Milch
> [ ✓ Ja ]  [ ✗ Nein ]

Erst **✓ Ja** führt ihn aus. **✗ Nein** verwirft ihn; ohne Antwort innerhalb der
*Bestätigungsfrist* (Standard 60 Min.) wird er ebenfalls verworfen und die Frage vom
Handy entfernt. `#nachricht` braucht keine Bestätigung — die Nachricht ist selbst
der Push. Die Fragen kommen nacheinander, in der Reihenfolge auf der Seite. Für
deutsche Fragen im Blueprint *Language of the confirmation messages* auf
**Deutsch** stellen.

### Beispiele

```text
#aufgabe Vermieter anrufen
#einkauf Milch, Eier und Kaffee
#erinnerung Fr 18:00 Müll rausbringen
#erinnerung morgen Miete überweisen
#termin 12.10. 14:00-15:30 Zahnarzt
#termin nächsten Montag 19:00 Essen mit Anna
#nachricht Ich bin 10 Minuten später da

Einkauf: Geschenk besorgen  ← kein #, bleibt eine normale Notiz
```

| Zeile | Ergebnis |
|---|---|
| `#aufgabe Vermieter anrufen` | To-do-Liste: „Vermieter anrufen" |
| `#einkauf Milch, Eier und Kaffee` | Einkaufsliste: „Milch", „Eier", „Kaffee" |
| `#erinnerung Fr 18:00 Müll rausbringen` | Aufgabe „Müll rausbringen", fällig nächsten Freitag 18:00 |
| `#erinnerung morgen Miete überweisen` | Aufgabe „Miete überweisen", fällig morgen (ganztägig) |
| `#termin 12.10. 14:00-15:30 Zahnarzt` | Kalender: „Zahnarzt", 12.10. 14:00–15:30 |
| `#termin nächsten Montag 19:00 Essen mit Anna` | Kalender: 19:00 mit Standarddauer (60 Min.) |
| `#nachricht Ich bin 10 Minuten später da` | Push-Nachricht mit diesem Text |
| `Einkauf: Geschenk besorgen` | Nichts — kein `#` |

### Regeln

- Das `#` ist **Pflicht** und steht **am Zeilenanfang** direkt vor dem Stichwort:
  `#einkauf`, nicht `# einkauf` oder `Einkauf`. Ein `#`-Wort mitten im Satz bewirkt
  nichts.
- **Wann** steht direkt nach dem Stichwort, danach der Text: `#erinnerung Fr 18:00 …`,
  `#termin 12.10. 14:00 …`. Ein Doppelpunkt oder Strich vor dem Text ist erlaubt,
  wenn es besser lesbar ist (`#erinnerung Fr 18:00: …`). Auch `morgen`,
  `nächste Woche` funktionieren. `12.10.` ist Tag.Monat, Uhrzeiten im 24-h-Format.
  Relative Angaben zählen ab dem Zeitpunkt, an dem die Seite gelesen wird, und liegen
  immer in der Zukunft.
- Ein Datum ohne Uhrzeit ergibt eine ganztägige Erinnerung bzw. einen ganztägigen
  Termin.
- Durchgestrichene Befehlszeilen werden ignoriert, ebenso Zeilen, die Claude nicht
  sicher lesen kann.
- **Weiterschreiben auf einer Seite:** Die Seite wird als neue Version erneut
  synchronisiert. Aufgaben, Einkäufe, Erinnerungen und Termine, die es schon gibt,
  werden **nicht doppelt angelegt** (gleicher Text bzw. ein Termin mit gleichem
  Titel zur selben Zeit), und es wird **nicht erneut gefragt**. Abgelehnte oder
  unbeantwortete Befehle werden erneut gefragt. `#nachricht`-Zeilen werden **erneut
  gesendet** — Nachrichten am besten auf eine neue Seite schreiben.

### Einrichtung

1. Die **Anthropic**-Integration hinzufügen (API-Schlüssel) — sie legt eine
   *Claude AI Task*-Entität an. Für Handschrift in deren Einstellungen *empfohlene
   Einstellungen* abwählen und **Claude Opus 5.5** (`claude-opus-5-5`) wählen.
2. Die **Home-Assistant-Companion-App** aufs Handy installieren — sie bekommt die
   Bestätigungsfragen und `#nachricht`-Nachrichten.
3. Optional: Listen und Kalender anlegen, z. B. mit **Local To-do** (unterstützt
   Fälligkeiten mit Uhrzeit) und **Local Calendar**.
4. Blueprint importieren (Button oben), daraus eine Automation erstellen und AI Task,
   Sprache, Handy (`notify.mobile_app_…`), die Ziele der einzelnen Befehle, die
   Bestätigungsfrist und die Sprache der Fragen auswählen.

Nach jeder Seite gibt es eine Benachrichtigung mit dem Text und einer Liste, was
passiert ist (✓ hinzugefügt · 📅 Termin · ✉ gesendet · ✗ abgelehnt oder nicht
bestätigt · ↺ schon vorhanden · ⏭ übersprungen, nicht eingerichtet). Das Event `huion_note_page_transcribed` enthält
`transcript`, `commands` und `done` für eigene Automationen.

### Eigener Ausgabeordner

Das AI Task kann nur Seiten in einem **Medienordner** von Home Assistant lesen. Der
Standard der Integration (`/media/huion_notes`) ist einer. Wenn du in den Optionen der
Integration einen anderen Ausgabeordner einstellst, z. B. `/notes`, trag ihn in
`configuration.yaml` ein und starte Home Assistant neu:

```yaml
homeassistant:
  media_dirs:
    local: /media      # Standard behalten — diese Liste ersetzt ihn
    notes: /notes
```

Die Integration findet dann für jede Seite selbst den passenden Medienordner; im
Blueprint ist nichts einzustellen. Liegt eine Seite in keinem Medienordner, bricht die
Automation mit einer Warnung im Log ab.
