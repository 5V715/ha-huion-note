# Huion Note X10 — Home Assistant integration

[![License](https://img.shields.io/badge/License-MIT-blue)](./LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5)](https://hacs.xyz/)
[![BLE](https://img.shields.io/badge/BLE-5.0_GATT-0082FC?logo=bluetooth&logoColor=white)](https://www.bluez.org/)

Pull the pages you write on a Huion Note X10 into Home Assistant — **no Huion app, no
cloud.** Whenever the notebook comes into range of a Home Assistant Bluetooth adapter or
[ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html), its stored
pages are downloaded, decoded and saved to your media folder as PNG + SVG + JSON
(ordered points + pressure — ready for handwriting recognition). No button to press.

The offline-sync protocol was reverse-engineered from Huion's desktop drivers (Ghidra),
Android BLE captures and the APK — see [`docs/offline-note-protocol.md`](docs/offline-note-protocol.md).

> Codebase maintained with [Claude Code](https://claude.ai/code). Not an official Huion
> product.

---

## Installation

- **HACS:** *HACS → ⋮ → Custom repositories* → add this repo's URL as an
  *Integration* → install **Huion Note X10** → restart Home Assistant.
- **Manual:** copy `custom_components/huion_note/` into `<config>/custom_components/`
  and restart.

Wake the notebook. Home Assistant discovers it by its advertised name (`Huion Note-X10`)
and offers to set it up (or add it via *Settings → Devices & services → Add integration →
Huion Note X10*).

**How it syncs**

1. Home Assistant's `bluetooth` integration reports an advertisement from the notebook.
2. If no sync ran within the cooldown (default 5 min, remembered across restarts;
   60 s after a failed attempt), it connects and pairs (the notebook drops
   connections from unpaired devices; if it has forgotten the pairing — e.g. after
   pairing with the Huion app on a phone — the old pairing is removed and redone),
   runs the keyless handshake (optional PIN), reads battery + page count and
   downloads every non-empty page, re-fetching dropped or corrupt packets
   (checksum). If the notebook wants a PIN, or rejects the one you set,
   automatic syncs pause and a repair issue appears under *Settings → Repairs*;
   saving the PIN in the options resumes them.
3. Each page is written as `<UTC time>-page<N>-<id>.{svg,json,png}` (e.g.
   `20260929T063000Z-page1-3f9a1c2e.png`) to `<media>/huion_notes/` (so it shows up
   under *Media → My media*), unless you set another folder. Existing files are never
   overwritten. The JSON holds the strokes plus every raw point (dots and pen-up
   points included). Pages are identified by a hash of all their points, so pages
   still stored on the notebook are **not** saved again on the next sync; if their
   files have gone missing they are saved again. A page you kept writing on is saved
   as a new version.
4. Optionally (**off** by default) the notebook's copies are deleted, highest index
   first. A page is only deleted if it downloaded completely, contains strokes, and
   its SVG, JSON and PNG are confirmed on disk. The current (last) page is never
   deleted, because you may still be writing on it.

**Options** (*Configure* on the integration): delete pages after sync, cooldown minutes,
output folder, device PIN.

**Entities**

| Entity | What |
|--------|------|
| `sensor.<name>_sync_status` | `idle` / `syncing` / `error` (`last_error` attribute) |
| `sensor.<name>_last_sync` | timestamp of the last successful sync (`new_pages`, `latest_page` attributes) |
| `sensor.<name>_pages_saved` | pages saved so far |
| `sensor.<name>_battery` | notebook battery %, read during each sync |
| `image.<name>_latest_page` | the most recently saved page |
| `button.<name>_sync_now` | sync immediately (the notebook must be awake and in range) |

**Events** — for automations (OCR, notify, copy to Nextcloud, …):

- `huion_note_page_saved` — `{address, page, strokes, complete, png, svg, json}` per new page
- `huion_note_sync_finished` — `{address, pages_on_tablet, new_pages, deleted, files}`

```yaml
automation:
  - alias: Notify on new handwritten pages
    triggers:
      - trigger: event
        event_type: huion_note_sync_finished
    conditions: "{{ trigger.event.data.new_pages > 0 }}"
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "{{ trigger.event.data.new_pages }} new page(s) from the notebook"
```

**Caveats**

- **Bluetooth link.** The firmware's duplicate MTU-request bug (see
  [below](#requirements-patch-bluez)) makes *unpatched* BlueZ drop the connection
  after a few seconds. Home Assistant OS ships stock BlueZ, so either use an
  [ESPHome Bluetooth proxy](https://esphome.io/components/bluetooth_proxy.html)
  near the notebook (recommended), or run HA on a host with the patched BlueZ.
- **The notebook must advertise.** Home Assistant only sees it while it is awake and
  advertising. A notebook that is paired/connected to another device (e.g. a phone
  running the Huion app) may not advertise; disconnect it there.
- The page protocol, decoder and dedupe/delete logic are covered by tests
  (`pytest`, see `requirements_test.txt`), and the protocol was validated on hardware
  by this project's earlier Linux CLI and Android app (see git history) — but the
  bleak transport inside Home Assistant has not yet been run against a real
  notebook. Reports welcome.

---

## Requirements: patch BlueZ

Only needed if Home Assistant talks to the notebook through a **local BlueZ adapter**
(not through an ESPHome Bluetooth proxy). The X10 firmware sends duplicate
`Exchange MTU Request` packets after every BLE connection-parameter update
(~every 5–8 s). Unpatched BlueZ 5.x treats this as a protocol violation and
disconnects — killing the page sync. A 2-line
patch to `src/shared/att.c` drops the duplicate instead:

```diff
--- a/src/shared/att.c
+++ b/src/shared/att.c
@@ -1082,9 +1082,8 @@
 		if (chan->in_req) {
 			DBG(att, "(chan %p) Received request while "
-					"another is pending: 0x%02x",
+					"another is pending: 0x%02x "
+					"(dropping duplicate)",
 					chan, opcode);
-			io_shutdown(chan->io);
-			bt_att_unref(chan->att);
-			return false;
+			return true;
 		}
```

The patch ships at [`patches/fix-duplicate-mtu-request.patch`](patches/fix-duplicate-mtu-request.patch).

**NixOS:** apply it via a `hardware.bluetooth.package` overlay.

<details>
<summary><strong>Debian / Ubuntu</strong></summary>

```bash
sudo apt build-dep bluez && sudo apt install devscripts
apt source bluez && cd bluez-*/
patch -p1 < /path/to/patches/fix-duplicate-mtu-request.patch
debuild -us -uc -b
cd .. && sudo dpkg -i bluez_*.deb && sudo apt-mark hold bluez
sudo systemctl restart bluetooth
```

</details>

<details>
<summary><strong>Arch Linux</strong></summary>

```bash
asp update bluez && asp checkout bluez && cd bluez/trunk/
cp /path/to/patches/fix-duplicate-mtu-request.patch .
# Add to PKGBUILD prepare(): patch -p1 < "$srcdir/../fix-duplicate-mtu-request.patch"
makepkg -si
```

</details>

<details>
<summary><strong>Fedora</strong></summary>

```bash
sudo dnf install rpm-build dnf-utils && sudo dnf builddep bluez
dnf download --source bluez && rpm -i bluez-*.src.rpm
cp /path/to/patches/fix-duplicate-mtu-request.patch ~/rpmbuild/SOURCES/
# Edit ~/rpmbuild/SPECS/bluez.spec — add PatchN and %patchN lines
rpmbuild -bb ~/rpmbuild/SPECS/bluez.spec
sudo rpm -Uvh ~/rpmbuild/RPMS/x86_64/bluez-*.rpm && sudo systemctl restart bluetooth
```

</details>

<details>
<summary><strong>From source (any distro)</strong></summary>

```bash
wget https://www.kernel.org/pub/linux/bluetooth/bluez-5.84.tar.xz
tar xf bluez-5.84.tar.xz && cd bluez-5.84/
patch -p1 < /path/to/patches/fix-duplicate-mtu-request.patch
./configure --prefix=/usr --sysconfdir=/etc --localstatedir=/var --enable-library --enable-tools
make -j$(nproc) && sudo make install && sudo systemctl restart bluetooth
```

</details>

---

## How it works

Connect → keyless challenge/response (`((a+b)<<2)%255`, optional 6-digit PIN) → read
device limits, battery and page count → per page: `REQUEST_OFFLINE_DATA` → device replies
a packet count, streams `0x87` point packets (gaps re-fetched via `0x88`) → decode 6-byte
points → strokes → render. Keepalive (`0x80`) every 5 s.

Background: [`docs/offline-notes-overview.md`](docs/offline-notes-overview.md),
reverse-engineering log: [`docs/notes/journey.md`](docs/notes/journey.md).

## Device info

| Field | Value |
|-------|-------|
| Product | Huion Note X10 (internal `HUION_T218`) |
| Pen | PW320 Scribo (dual nibs: ballpoint + plastic) |
| BLE | 5.0 GATT, VID `0x256C`, PID `0x8251` |
| Vendor GATT | `0000FFE0` (FFE1 = data notify, FFE2 = command write/indicate) |
| Resolution / pressure | 28200 × 37400, 8192 levels (13-bit) |

## Repo layout

```text
custom_components/huion_note/          — the integration (install this)
custom_components/huion_note/protocol/ — pure protocol core: framing, auth, codec, session, render
tests/                                 — pytest suite (pytest-homeassistant-custom-component)
patches/                               — BlueZ att.c patch for the firmware MTU bug
docs/                                  — protocol map, specs, overview, RE log (docs/notes/)
```

Run the tests: `pip install -r requirements_test.txt && pytest`.

## License

[MIT](LICENSE). Huion and Note X10 are trademarks of Huion; this project is not affiliated
with or endorsed by Huion.
