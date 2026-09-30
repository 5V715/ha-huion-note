# Huion Linux driver — BLE "live tablet" protocol

Reverse-engineered from `HuionTablet_LinuxDriver_v15.0.0.175.x86_64.deb` (static analysis
only, `objdump` + DWARF line info — both `huionCore` and `libs/libTabletSession.so` ship
**with debug info, not stripped**). Complements [`offline-note-protocol.md`](offline-note-protocol.md),
which covers the offline page dump; this driver does **not** implement offline sync at all
(no page/offline/`0x86`/`0x87` code anywhere in the package). It implements the *live*
pen-as-tablet path over the same GATT service.

Status legend: ✅ read directly from the binary · 🟡 inferred · ❌ untested on an X10.

---

## 1. Where the code lives

| File | What |
|------|------|
| `usr/lib/huiontablet/libs/libTabletSession.so` | `HnBluetooth` (BLE, via BlueZ D-Bus / `sd-bus`, source `TabletSession/HnBluetooth.cpp`, `lib/blz/blzlib.c`), `HnUsb`, `HnHid` |
| `usr/lib/huiontablet/huionCore` | Daemon: `HnTabletThread`, `HnDoerPen`, report parsers (`TabletBase/TabletParse.c`) |
| `usr/lib/huiontablet/huiontablet` | Qt settings UI — no protocol code |

The X10's internal model **`HUION_T218`** appears in `huionCore` in a model allow-list
(`T209, T216, T218, T21h, T22b, T23a, T23b`) — see §6. So the driver knows this device.

## 2. Discovery / connect ✅

- The driver does **not** scan-and-pair. `HnBluetooth::openBLE()` runs
  `hcitool con` and only considers devices **already connected** in BlueZ.
- For each: `blz_connect(mac)` → require service `0000ffe0-…` → read **`2a29`
  Manufacturer Name**. `"HUION"` ⇒ Huion device; anything else is recorded as a
  Gaomon OEM product (`addGaoManProduct`).
- A thread (`blz_event_loop_thread_function`) then sets up the characteristics
  (the log string calls them "Nordic UART characteristics"):

| Char | Driver var | Use |
|------|-----------|-----|
| `FFE2` | `wch` | command **write** — `AcquireWrite` fd, i.e. write-without-response |
| `FFE2` | `rch` | command **responses** — notify/indicate → `notify_command_response_handler_fun` |
| `FFE1` | `rchReport` | **pen reports** — notify → `notify_report_handler_fun` |

Same roles as the offline-sync map (FFE2 = command + indications, FFE1 = data).

**No VERIFY_CONNECT / VERIFY_RESULT (`0x81/0x82`) handshake is sent.** 🟡 Either live
mode on this firmware doesn't need it, or the driver only works with tablets that don't
gate on it. ❌ Unverified on the X10.

## 3. Commands (FFE2 write) ✅

`HnBluetooth::getStringDesc(idx)` — the USB "string descriptor" query of wired Huion
tablets, tunnelled over BLE:

```
cd <idx> 00 00 00 00 00 00        (8 bytes; note: no "08" length byte, no "ed" trailer)
```

(The offline app sends `cd op 08 … ed`; the firmware evidently keys on byte 1. 🟡)
The driver waits up to **3000 ms** for a response (polling a flag every 500 ms), and
for `0xc9`/`0xd1` re-sends once if the response buffer is still empty.

Response = the next FFE2 notification. The driver strips the `cd <idx>` prefix
(payload = bytes `[2:]`) — **except `0xd1`, which keeps the whole frame**.

| idx | Dec | Used for | Response parse |
|-----|-----|----------|----------------|
| `0xc8` | 200 | tablet params (`initTabletInfo`, `onCmd`) | §4 |
| `0xc9` | 201 | model/firmware string | UTF-16LE, up to 0x23 chars. `sFullModel` = first 17 chars, `sModel` = first 10 (e.g. `HUION_T218`) — matches the known `HUION_T218_230819` VERSION |
| `0xca` | 202 | vendor check | UTF-16LE (0x22 chars) must equal `"HUION Animation Technology Co.,ltd"` **and** byte-sum of the first 0x44 bytes must be `0xbef` |
| `0xcd` | 205 | "exit driver mode" on close — **skipped for T218** and the other allow-listed models |
| `0xd1` | 209 | battery | full frame `cd d1 <max> <cur> …` → `percent = cur*100/max` (0 if `max==0`) |
| `0xe8` | 232 | macro-key status/values (not relevant to the X10) | — |

LED / dial / sleep / touch-screen option commands also go through this path with
runtime-chosen indices (not relevant here).

## 4. `0xc8` tablet parameters ✅ (offsets into the stripped payload)

```
max_x    = u24 LE  [0..2]
max_y    = u24 LE  [3..5]
max_p    = u16 LE  [6..7]
lpi      = u16 LE  [8..9]
pen_btns = [10]    hkeys = [11]    skeys = [12]
flags    = [14]    is_monitor = flags & 0x0a, is_passive = flags & 0x0c
rate     = [5] * 4        (driver reads byte 5 — overlaps max_y hi; likely a quirk)
```

🟡 Expect the X10 to report the same limits as `MAX_DATA 0x95` (28200 × 37400, 8191).

## 5. Live pen report (FFE1 notify) ✅

`notify_report_handler_fun` accepts **only 14-byte notifications** (others are dropped).
It overwrites byte 1 with `0x08` and passes `notif[1:]` on — i.e. it turns the BLE
packet into the standard Huion USB HID pen report (ID 8):

```
BLE notif:  n0  n1  n2      n3 n4  n5 n6  n7 n8   n9     n10    n11   n12   n13
report:         08  status  x_lo   y_lo   press   x_hi   y_hi   tiltx tilty pen_idx
                [0] [1]     [2..3] [4..5] [6..7]  [8]    [9]    [10]  [11]  [12]
```

- `x = r[8]<<16 | r[3]<<8 | r[2]` · `y = r[9]<<16 | r[5]<<8 | r[4]`
- `pressure = u16 LE r[6..7]`
- `tilt_x = (int8) r[10]` · `tilt_y = -(int8) r[11]`
- `pen_index = r[12]` (multi-pen models only; must be ≤ 3)
- report valid only if `r[0] == 0x08` (always true after the rewrite)
- `status = r[1]`: `≤ 0x90`, `0xa0`, `0xa1` → pen (in-range/tip/barrel bits in the low
  nibble, standard Huion); `0xe0…0xf1` → express keys / dial / ring / slider / touch.

`n0` (the byte before the rewritten one) is ignored by the driver. 🟡 Likely the `cd`
frame marker, with `n1` an opcode (plausibly `0x8d ONLINE_DATA`, which the offline doc
lists as "online-mode").

## 6. T218-specific behaviour ✅

`T209, T216, T218, T21h, T22b, T23a, T23b` are treated specially in two places:

- `HnTabletThread::close()` — does **not** send `0xcd` (exit driver mode) to these.
- `HnDoerPen::handle()` — a change of `pen_index` does **not** trigger the UI "pen
  changed" notification for these.

## 7. What this means for the integration

> Update: the app 2.2.3 notes show the X10's live mode is `ONLINE_DATA 0x8d` (11/13-byte
> packets), not this 14-byte desktop-tablet report — see
> [`app-2.2.3-protocol-notes.md`](app-2.2.3-protocol-notes.md) §5.

- Nothing here changes the offline-sync implementation; it's consistent with it
  (same service/chars, same `cd` framing, `0xc9` VERSION already known).
- New, potentially useful: **`cd d1 …` battery** (`cur*100/max`), **`cd c8 …`
  tablet limits**, and a **live pen stream** on FFE1 in 14-byte packets.
- ❌ Open: whether the X10 emits 14-byte live reports without the `0x81/0x82`
  handshake, and whether it needs `MODE 0x84` to switch into online mode. Needs one
  probe on hardware (connect → subscribe FFE1/FFE2 → send `cd c9…`, `cd c8…`, `cd d1…`
  → write on the pad and log FFE1 packet lengths).
