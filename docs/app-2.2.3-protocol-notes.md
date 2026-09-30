# Huion Note app 2.2.3 — protocol delta

Source: `Huion Note _ Easy note-taking_2.2.3_APKPure.xapk` (`com.huion.hinotes`, versionCode 33,
targetSdk 36), base APK decompiled with jadx 1.5.6. The protocol classes are still **not
obfuscated** (`util/bluetooth/HiBluetoothManager`, `BluetoothUtil`, `OrderCode`,
`been/BluePoint`, `been/BluetoothPackageData`); the UI/presenter layer is (`a.*`).

Extends [`offline-note-protocol.md`](offline-note-protocol.md) and
[`linux-driver-ble-protocol.md`](linux-driver-ble-protocol.md). Legend: ✅ read in source ·
🟡 inferred · ❌ needs hardware.

---

## 1. Connection sequence ✅

1. Device must be **bonded** first (`createBond`; Android only — the app tries to suppress
   the pairing prompt).
2. `connectGatt` → `discoverServices` → enable notify on **FFE1** and **FFE2** →
   `requestMtu(141)`.
3. **2.1 s later** send `VERIFY_CONNECT` `cd 81 08 00 00 00 00 ed` (timeout 10 s, one retry).
4. Challenge → `VERIFY_RESULT` (unchanged, §6 of the offline doc). Status `0` fail · `1` ok ·
   `2` PIN.
5. After OK: `MAX_DATA 0x95` → (its reply triggers) `DEVICE_NAME 0x91`,
   `SET_MANY_PACKET_DISTANCE 0x96 (01 03)`, `ROM 0x8f`, `ELECTRICITY 0x8e`.
6. Also after status 1 the app sends `GET_PWD 0x93` (see §6).

**Command pacing:** every `sendMsg` sleeps **350 ms** after the write (single-thread
executor). Only `GET_PAGE_PACKAGE 0x88` and the post-`NEXT_PAGE` `ROM` go out without
the delay — and the app still sends 0x88 **one at a time**, waiting ≤1 s for each reply.

**Disconnect:** the app sends `DISCONNECT` `cd 94 08 00 00 00 00 ed`, waits 300 ms, then
closes. The device can also send `cd 94 … 01` to say "stop auto-connecting to me".

## 2. Which pages to sync — `ROM 0x8f` ✅ (replaces the CURRENT_PAGE scan)

`requestCurrentLogicPage` (0x85) is **no longer called anywhere** in 2.2.3. The sync range
comes from `ROM` (`cd 8f 08 00 00 00 00 ed`):

```
reply: cd 8f .. free   total   last_lo last_hi  flag
            [3]    [4]     [5]     [6]      [7]
stored = total - free                    # pages with data
last   = u16(last_lo,last_hi) - (1 if flag == 0 else 0)
sync pages  (last + 1 - stored) … last   # stored pages, contiguous indices
```

`free`/`total` are also the "device memory" UI numbers; the app warns at `free ≤ 10`.
The device also pushes `ROM` unsolicited after `NEXT_PAGE 0x8a` (page created on the pad).

🟡 This explains the "empty pages 0–6, content at 7–11" behaviour: the official app
**deletes every page after syncing it** (§3), so indices keep growing and older slots stay
empty. The window `last+1-stored … last` targets exactly the live pages.

## 3. Official sync loop ✅ (`a.C88008`, `a.C0362o0oOo`, `a.oo0o888`, `a.C0532ooo08`)

For each page in the window:
1. `REQUEST_OFFLINE_DATA` `cd 86 08 lo hi 00 00 ed` — timeout 2 s for the count reply,
   **1 s idle** between `0x87` packets. On timeout with partial data it proceeds with what
   it has; with no data, retries the page **up to 3×**.
2. Packets are validated (§4), sorted, de-duplicated by seq.
3. Gaps: `GET_PAGE_PACKAGE 0x88` per missing seq, sequentially, **up to 5 retries each**;
   reply accepted only if its index (bytes 3..4) equals the one requested.
4. Import page → **`DELETE_PAGE 0x8b` (retried once)** → next page.
5. After the last page: `CLEAR_CACHE 0x8c`, then `ROM` again to refresh memory numbers.

## 4. Packet validation & decoding ✅

**Checksum (new vs. our implementation):** for `0x87`/`0x88` packets
(`BluetoothPackageData`):

```
complete = (sum(pkt[:-1]) & 0xff) == pkt[-1]      # packets < 7 bytes are never complete
```

Incomplete packets are **dropped** and re-fetched via 0x88.

**Points** (`BluetoothUtil.decodePackagePoint`): `N = (len - 6) // POINT_SIZE`, records start
at byte 5. `POINT_SIZE = 8` only if `DEVICE_NAME` is `"Huion Tablet_X30"`, else **6**.
(For 126-byte packets `(len-6)//6 == (len-5)//6 == 20` — same result as today.)

**Pressure/status split depends on `MAX_PRESS`** (`BluePoint`):

| MAX_PRESS | status bits of `rec[5]` | pressure high bits |
|-----------|-------------------------|--------------------|
| ≤ 8192 (X10: 8191) | `rec[5] >> 5` (3 bits) | `rec[5] & 0x1f` |
| 8193 … 16384 | `rec[5] >> 6` (2 bits) | `rec[5] & 0x3f` |
| 16385 … 32768 | `rec[5] >> 7` (1 bit) | `rec[5] & 0x7f` |

8-byte records add `tilt_x = (int8) rec[6]`, `tilt_y = (int8) rec[7]` (degrees; `fe fe` =
no tilt data).

**Pen-up filter:** a point whose pressure is `< FILTER_PRESS` (7 %) of `MAX_PRESS` is
forced to `status = 0, press = 0`.

**Axis swap** (`isA4Device`) now applies to `"Huion Tablet_T910"` **and**
`"Huion Tablet_X30"`. X10 (`"Huion Tablet_X10"`) → no swap, A5 page (1410 × 1870).

## 5. Live ("online") drawing — `ONLINE_DATA 0x8d` ✅

**Enable / disable:** `cd 8d 08 <mode> 00 00 00 ed`

| mode | Used when | Meaning 🟡 |
|------|-----------|-----------|
| `1` | editor opened, "save on device" **off** | stream live, don't store on the pad (`isOpenOnlineWithoutSaveData`) |
| `2` | editor opened, "save on device" **on** (`IS_OPEN_ONLINE`) | stream live **and** store |
| `3` | leaving the editor / main screen | back to offline-only |

Ack: a `0x8d` frame with `[3] == 0` (rejected) or `[3] == 1` (accepted). On every
`HEART_BEAT 0x80` the app re-sends the pending mode (1 or 3) if the device hadn't acked.

**Live packet** (any other `0x8d` frame; 11 or 13 bytes, or two of them glued into 22 / 26):

```
cd 8d <len> [3] x_lo x_hi y_lo y_hi p_lo p_hi|status [tilt_x tilt_y] checksum
 0  1   2    3   4    5    6    7    8    9            10     11      last
x = u16 LE [4..5]   y = u16 LE [6..7]
press/status from [8],[9] — same MAX_PRESS-dependent split as §4
checksum = sum(all but last) & 0xff
```

For glued 22/26-byte notifications the app rewrites byte 2 of the first half and tries the
first half, falling back to the second. 🟡 Byte [3] is passed through as a tag but never
interpreted; for data packets it must not be `0` or `1` (those are acks).

This supersedes the Linux driver's 14-byte `FFE1` report (that's the desktop tablet
protocol, not what the X10 app uses). ❌ Not yet tried on hardware.

## 6. PIN handling — `GET_PWD 0x93` ⚠️ ✅

- `GET_PWD` `cd 93 08 00 00 00 00 ed` → reply bytes `[3..8]` = the device PIN **in its
  encoded form** (`ascii(digit) + "huion#"[i]`), all zeros when no PIN is set.
- When `VERIFY_RESULT` says `2` (PIN required) and the device is bound to the user's
  account, the app sends `GET_PWD` and **replays those 6 bytes verbatim as
  `VERIFY_PWD`** — no user input. I.e. the firmware discloses the PIN before
  authentication, and the PIN is recoverable: `digit[i] = chr(b[i] - "huion#"[i])`.
- `SET_PWD 0x92`: same two-frame layout as `VERIFY_PWD` (`cd 92 08 01 e0 e1 e2 ed`,
  `cd 92 08 02 e3 e4 e5 ed`, 500 ms apart); the literal PIN `"######"` sends zeros =
  **remove PIN**.
- ❌ Whether the X10 answers `GET_PWD` pre-auth needs one hardware check.

## 7. Other opcodes ✅

| Op | Frame / reply |
|----|---------------|
| `0x84 MODE` | `cd 84 08 <m> 00 00 00 ed`, `m` ∈ {1 PUB_MODE, 2 PRI_MODE}; sent with 1 after VERSION. Reply `[3]==1` ok 🟡 (purpose unclear) |
| `0x8e ELECTRICITY` | reply `[3]` = battery %. Also pushed unsolicited; app warns at ≤20 / ≤10 |
| `0x8a NEXT_PAGE` | device → app: `[3]==2` "new page created on the pad"; app re-reads ROM |
| `0x90 LOW_MEMORY_WARMING` | defined, unhandled |
| `0x91 DEVICE_NAME` | reply: name = bytes `[3 : 3 + ([2] - 3)]` (ASCII) → e.g. `Huion Tablet_X10` |
| `0xc9 VERSION` | reply string = bytes `[2:]`; last 6 chars = fw date, compared to the server's latest |
| OTA | Telink OTA on characteristic `00010203-0405-0607-0809-0a0b0c0d2b12` (firmware update — not needed) |

## 8. Integration status (branch `fix/pairing-checksum-pressure`)

| # | App 2.2.3 | Integration |
|---|-----------|-------------|
| 1 | Drops packets failing the checksum, re-fetches them | ✅ same; a page with a packet that never arrives intact is saved with the bad copy but marked incomplete (never deleted). If *no* packet on a page passes, validation is skipped for that page (guards against a differing firmware checksum) |
| 2 | Pressure/status split by `MAX_PRESS` | ✅ same |
| 3 | Page window from `ROM` | ✅ scans down from ROM's last index until `stored` pages arrived (skips holes left by selective deletes); falls back to `0 … CURRENT_PAGE` if ROM is missing or implausible |
| 4 | 350 ms between commands; 0x88 one at a time | ✅ same; stops re-requesting when a packet gets no answer at all |
| 5 | `cd 94 …` before disconnect | ✅ same, 300 ms before closing |
| 6 | `< 7 %` pressure ⇒ pen-up | ✅ for stroke splitting; raw points (JSON, page hash) unchanged |
| 7 | Pairs before connecting | ✅ `pair=True`; stale BlueZ pairing removed on connect timeout (§6 of the offline doc) |
| — | Never sends `HEART_BEAT` | Integration keeps its 5 s heartbeat: the notebook drops idle links after ~2–3 s, and host-side work (rendering a page) can pause traffic |

❌ Items 1, 3 and 7 are verified only against the app's code and fakes, not a device.
