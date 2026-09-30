# Handwritten commands · Handschriftliche Befehle

[![Import blueprint](https://my.home-assistant.io/badges/blueprint_import.svg)](https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2F5V715%2Fha-huion-note%2Fblob%2Fmain%2Fblueprints%2Fautomation%2Fhuion_note%2Ftranscribe_page.yaml)

[English](#english) · [Deutsch](#deutsch)

---

## English

Every page the Huion Note X10 integration saves is read by an AI Task (Claude via the
Anthropic integration). You get the handwriting as text, and lines that start with a
**keyword and a colon** are carried out in Home Assistant.

### Commands

| Keyword (EN) | Keyword (DE) | What happens | Setting in the blueprint |
|---|---|---|---|
| `TODO:` | `AUFGABE:` | Item on your to-do list | *TODO: / AUFGABE: → to-do list* |
| `SHOP:` | `EINKAUF:` | Item(s) on your shopping list — one per item | *SHOP: / EINKAUF: → shopping list* |
| `REMIND <when>:` | `ERINNERUNG <wann>:` | To-do with a due date/time | *REMIND: / ERINNERUNG: → to-do list with due times* |
| `EVENT <when>:` | `TERMIN <wann>:` | Calendar event | *EVENT: / TERMIN: → calendar* |
| `SEND:` | `NACHRICHT:` | Push message to your phone | *SEND: / NACHRICHT: → notify action* |

Keywords work in either language, in any capitalisation. Leave a setting empty to
switch that command off. Controlling devices by handwriting is deliberately not
supported — a misread word must never switch something.

### Examples

```text
TODO: call the landlord
SHOP: milk, eggs and coffee
REMIND fri 18:00: take out the bins
REMIND tomorrow: pay the rent
EVENT 12.10. 14:00-15:30: dentist
EVENT next monday 19:00: dinner with Anna
SEND: I'll be 10 minutes late
```

| Line | Result |
|---|---|
| `TODO: call the landlord` | To-do list: "call the landlord" |
| `SHOP: milk, eggs and coffee` | Shopping list: "milk", "eggs", "coffee" |
| `REMIND fri 18:00: take out the bins` | To-do "take out the bins", due next Friday 18:00 |
| `REMIND tomorrow: pay the rent` | To-do "pay the rent", due tomorrow (all day) |
| `EVENT 12.10. 14:00-15:30: dentist` | Calendar: "dentist", 12 Oct 14:00–15:30 |
| `EVENT next monday 19:00: dinner with Anna` | Calendar: 19:00 for the default length (60 min) |
| `SEND: I'll be 10 minutes late` | Push message with that text |

### Rules

- The keyword must be at the **start of a line**, followed by a **colon**. "shop"
  in the middle of a sentence does nothing.
- **When** goes between keyword and colon: `fri 18:00`, `tomorrow`, `12.10. 14:00`,
  `next week`. Dates like `12.10.` are day.month; times are 24 h. Relative dates are
  counted from when the page is read and always point to the future.
- A date without a time gives an all-day reminder or event.
- Crossed-out command lines are ignored, and so are lines Claude can't read with
  confidence.
- **Writing more on a page:** the page is synced again as a new version. To-dos,
  shopping items, reminders and events that already exist are **not added twice**
  (same text, or an event with the same title at that time). `SEND:` lines **are sent again** — put
  messages on a fresh page.

### Setup

1. Add the **Anthropic** integration (API key) — it creates a *Claude AI Task*
   entity. For handwriting, open its settings, untick *recommended settings* and
   choose **Claude Opus 5.5** (`claude-opus-5-5`).
2. Optional: create lists and a calendar, e.g. with **Local To-do** (supports due
   times) and **Local Calendar**; install the companion app for push messages.
3. Import the blueprint (button above), create an automation from it and choose the
   AI Task, your language and where each command goes.

After each page you get a notification with the transcript and a list of what was
done (✓ added · 📅 event · ✉ sent · ↺ already there · ⏭ skipped, not configured). The
`huion_note_page_transcribed` event carries `transcript`, `commands` and `done` for
your own automations.

---

## Deutsch

Jede Seite, die die Huion-Note-X10-Integration speichert, liest ein AI Task (Claude
über die Anthropic-Integration). Du bekommst die Handschrift als Text, und Zeilen,
die mit einem **Stichwort und Doppelpunkt** beginnen, werden in Home Assistant
ausgeführt.

### Befehle

| Stichwort (DE) | Stichwort (EN) | Was passiert | Einstellung im Blueprint |
|---|---|---|---|
| `AUFGABE:` | `TODO:` | Eintrag in deiner To-do-Liste | *TODO: / AUFGABE: → to-do list* |
| `EINKAUF:` | `SHOP:` | Eintrag/Einträge auf der Einkaufsliste — einer pro Artikel | *SHOP: / EINKAUF: → shopping list* |
| `ERINNERUNG <wann>:` | `REMIND <when>:` | Aufgabe mit Fälligkeit (Datum/Uhrzeit) | *REMIND: / ERINNERUNG: → to-do list with due times* |
| `TERMIN <wann>:` | `EVENT <when>:` | Kalendertermin | *EVENT: / TERMIN: → calendar* |
| `NACHRICHT:` | `SEND:` | Push-Nachricht aufs Handy | *SEND: / NACHRICHT: → notify action* |

Stichwörter funktionieren in beiden Sprachen und in beliebiger Groß-/Kleinschreibung.
Lass eine Einstellung leer, um den Befehl abzuschalten. Geräte per Handschrift zu
steuern ist absichtlich nicht möglich — ein falsch gelesenes Wort darf nie etwas
schalten.

### Beispiele

```text
AUFGABE: Vermieter anrufen
EINKAUF: Milch, Eier und Kaffee
ERINNERUNG Fr 18:00: Müll rausbringen
ERINNERUNG morgen: Miete überweisen
TERMIN 12.10. 14:00-15:30: Zahnarzt
TERMIN nächsten Montag 19:00: Essen mit Anna
NACHRICHT: Ich bin 10 Minuten später da
```

| Zeile | Ergebnis |
|---|---|
| `AUFGABE: Vermieter anrufen` | To-do-Liste: „Vermieter anrufen" |
| `EINKAUF: Milch, Eier und Kaffee` | Einkaufsliste: „Milch", „Eier", „Kaffee" |
| `ERINNERUNG Fr 18:00: Müll rausbringen` | Aufgabe „Müll rausbringen", fällig nächsten Freitag 18:00 |
| `ERINNERUNG morgen: Miete überweisen` | Aufgabe „Miete überweisen", fällig morgen (ganztägig) |
| `TERMIN 12.10. 14:00-15:30: Zahnarzt` | Kalender: „Zahnarzt", 12.10. 14:00–15:30 |
| `TERMIN nächsten Montag 19:00: Essen mit Anna` | Kalender: 19:00 mit Standarddauer (60 Min.) |
| `NACHRICHT: Ich bin 10 Minuten später da` | Push-Nachricht mit diesem Text |

### Regeln

- Das Stichwort muss **am Zeilenanfang** stehen, gefolgt von einem **Doppelpunkt**.
  „Einkauf" mitten im Satz bewirkt nichts.
- **Wann** steht zwischen Stichwort und Doppelpunkt: `Fr 18:00`, `morgen`,
  `12.10. 14:00`, `nächste Woche`. `12.10.` ist Tag.Monat, Uhrzeiten im 24-h-Format.
  Relative Angaben zählen ab dem Zeitpunkt, an dem die Seite gelesen wird, und liegen
  immer in der Zukunft.
- Ein Datum ohne Uhrzeit ergibt eine ganztägige Erinnerung bzw. einen ganztägigen
  Termin.
- Durchgestrichene Befehlszeilen werden ignoriert, ebenso Zeilen, die Claude nicht
  sicher lesen kann.
- **Weiterschreiben auf einer Seite:** Die Seite wird als neue Version erneut
  synchronisiert. Aufgaben, Einkäufe, Erinnerungen und Termine, die es schon gibt,
  werden **nicht doppelt angelegt** (gleicher Text bzw. ein Termin mit gleichem
  Titel zur selben Zeit). `NACHRICHT:`-Zeilen werden **erneut gesendet** — Nachrichten am besten auf
  eine neue Seite schreiben.

### Einrichtung

1. Die **Anthropic**-Integration hinzufügen (API-Schlüssel) — sie legt eine
   *Claude AI Task*-Entität an. Für Handschrift in deren Einstellungen *empfohlene
   Einstellungen* abwählen und **Claude Opus 5.5** (`claude-opus-5-5`) wählen.
2. Optional: Listen und Kalender anlegen, z. B. mit **Local To-do** (unterstützt
   Fälligkeiten mit Uhrzeit) und **Local Calendar**; für Push-Nachrichten die
   Companion-App installieren.
3. Blueprint importieren (Button oben), daraus eine Automation erstellen und AI Task,
   Sprache und die Ziele der einzelnen Befehle auswählen.

Nach jeder Seite gibt es eine Benachrichtigung mit dem Text und einer Liste, was
passiert ist (✓ hinzugefügt · 📅 Termin · ✉ gesendet · ↺ schon vorhanden · ⏭
übersprungen, nicht eingerichtet). Das Event `huion_note_page_transcribed` enthält
`transcript`, `commands` und `done` für eigene Automationen.
