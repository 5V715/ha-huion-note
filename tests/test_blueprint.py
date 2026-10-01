"""The transcription/commands blueprint, run by Home Assistant's automation engine
with stand-in AI Task, to-do, calendar and notify services (no API calls)."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
from datetime import datetime

import pytest

import voluptuous as vol

from homeassistant.components.calendar import CREATE_EVENT_SCHEMA
from homeassistant.components.todo import TODO_ITEM_FIELD_SCHEMA
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_capture_events

BLUEPRINT = os.path.join(os.path.dirname(__file__), "..", "blueprints", "automation",
                         "huion_note", "transcribe_page.yaml")
PNG = "/media/huion_notes/20260930T063000Z-page3-3f9a1c2e.png"
LISTS = {"todo.tasks": "TODO", "todo.shopping": "SHOP", "todo.reminders": "REMIND"}


class Fakes:
    def __init__(self, hass: HomeAssistant):
        self.ai: list[ServiceCall] = []
        self.added: list[ServiceCall] = []
        self.events: list[ServiceCall] = []
        self.messages: list[ServiceCall] = []
        self.answer: str | None = "ok"  # how the "phone" answers confirmations
        self.reply = {"transcript": "", "commands": []}
        self.existing_items: dict[str, list[str]] = {e: [] for e in LISTS}
        self.existing_events: list[str] = []

        def reg(domain, name, fn, response=False):
            hass.services.async_register(
                domain, name, fn,
                supports_response=SupportsResponse.ONLY if response else SupportsResponse.NONE)

        async def generate_data(call):
            self.ai.append(call)
            # like Claude under the flat schema: the command list comes as JSON text
            cmds = self.reply["commands"]
            return {"conversation_id": "x", "data": {
                "transcript": self.reply["transcript"],
                "commands": cmds if isinstance(cmds, str) else json.dumps(cmds)}}

        async def get_items(call):
            return {e: {"items": [{"summary": s, "status": "needs_action"}
                                  for s in self.existing_items[e]]}
                    for e in call.data["entity_id"]}

        async def get_events(call):
            return {e: {"events": [{"summary": s} for s in self.existing_events]}
                    for e in call.data["entity_id"]}

        reg("ai_task", "generate_data", generate_data, response=True)
        reg("todo", "get_items", get_items, response=True)
        reg("todo", "add_item", self.added.append)
        reg("calendar", "get_events", get_events, response=True)
        reg("calendar", "create_event", self.events.append)
        async def phone(call):
            self.messages.append(call)
            buttons = call.data.get("data", {}).get("actions")
            if buttons and self.answer:
                action = buttons[0 if self.answer == "ok" else 1]["action"]

                async def tap():
                    await asyncio.sleep(0.05)  # after the automation starts waiting
                    hass.bus.async_fire("mobile_app_notification_action", {"action": action})
                hass.async_create_background_task(tap(), "tap")

        reg("notify", "mobile_app_phone", phone)

    @property
    def asks(self):
        return [c for c in self.messages if c.data.get("data", {}).get("actions")]

    @property
    def sent(self):
        return [c.data["message"] for c in self.messages
                if not c.data.get("data") and c.data["message"] != "clear_notification"]

    @property
    def cleared(self):
        return [c for c in self.messages if c.data["message"] == "clear_notification"]


@pytest.fixture
def fakes(hass: HomeAssistant) -> Fakes:
    return Fakes(hass)


ALL_TARGETS = {
    "todo_list": "todo.tasks", "shopping_list": "todo.shopping",
    "reminder_list": "todo.reminders", "calendar": "calendar.home",
    "message_action": "notify.mobile_app_phone",
}


async def _setup(hass: HomeAssistant, **inputs) -> None:
    dest = hass.config.path("blueprints", "automation", "huion_note")
    os.makedirs(dest, exist_ok=True)
    shutil.copy(BLUEPRINT, dest)
    assert await async_setup_component(hass, "persistent_notification", {})
    assert await async_setup_component(hass, "automation", {"automation": {
        "alias": "transcribe",
        "use_blueprint": {
            "path": "huion_note/transcribe_page.yaml",
            "input": {"ai_task_entity": "ai_task.claude_ai_task", **inputs},
        },
    }})
    await hass.async_block_till_done()


_OLD = object()


async def _page(hass, png=PNG, strokes=5, media_id=None):
    """Fire huion_note_page_saved like the integration does: with the media link
    for the page (default: derived from PNG's /media path); media_id=_OLD leaves it
    out, as integration versions before it was added did."""
    done = async_capture_events(hass, "huion_note_page_transcribed")
    data = {"page": 3, "strokes": strokes, "complete": True, "png": png,
            "svg": png[:-3] + "svg", "json": png[:-3] + "json"}
    if media_id is None:
        media_id = ("media-source://media_source/local/" + png[len("/media/"):]
                    if png.startswith("/media/") else "")
    if media_id is not _OLD:
        data["media_content_id"] = media_id
    hass.bus.async_fire("huion_note_page_saved", data)
    await hass.async_block_till_done(wait_background_tasks=True)
    return done


async def test_page_is_transcribed(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "Buy milk\n- eggs", "commands": []}
    await _setup(hass, language="German", actions=[
        {"event": "my_transcript", "event_data": {"text": "{{ transcript }}"}}])
    mine = async_capture_events(hass, "my_transcript")
    done = await _page(hass)

    data = fakes.ai[0].data
    assert data["entity_id"] == "ai_task.claude_ai_task"
    assert data["attachments"] == [{
        "media_content_id":
            "media-source://media_source/local/huion_notes/20260930T063000Z-page3-3f9a1c2e.png",
        "media_content_type": "image/png",
    }]
    assert "written in German" in data["instructions"]
    assert '"#todo" or "#aufgabe"' in data["instructions"]
    assert "a line without it is NEVER a command" in data["instructions"]
    assert set(data["structure"]) == {"transcript", "commands"}
    # only plain text fields: nested objects break Anthropic structured outputs
    # ("For 'object' type, 'additionalProperties' must be explicitly set to false")
    assert all(set(f["selector"]) == {"text"} for f in data["structure"].values())
    assert done[0].data["transcript"] == "Buy milk\n- eggs"
    assert mine[0].data["text"] == "Buy milk\n- eggs"
    notes = hass.data["persistent_notification"]
    assert any(n["message"] == "Buy milk\n- eggs" for n in notes.values())


async def test_every_command_runs(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "…", "commands": [
        {"type": "todo", "text": "Vermieter anrufen"},
        {"type": "shopping", "text": "Milch"},
        {"type": "shopping", "text": "Eier"},
        {"type": "reminder", "text": "Müll rausbringen", "start": "2026-10-02 18:00"},
        {"type": "reminder", "text": "Pay rent", "start": "2026-10-03"},
        {"type": "event", "text": "Zahnarzt", "start": "2026-10-12 14:00", "end": "2026-10-12 15:30"},
        {"type": "event", "text": "Dinner", "start": "2026-10-13 19:00"},
        {"type": "event", "text": "Holiday", "start": "2026-10-20"},
        {"type": "message", "text": "Bin gleich da"},
    ]}
    await _setup(hass, **ALL_TARGETS, event_duration=45)
    done = await _page(hass)

    added = [(c.data["entity_id"][0], c.data["item"]) for c in fakes.added]
    assert added == [("todo.tasks", "Vermieter anrufen"), ("todo.shopping", "Milch"),
                     ("todo.shopping", "Eier"), ("todo.reminders", "Müll rausbringen"),
                     ("todo.reminders", "Pay rent")]
    due = datetime.fromisoformat(fakes.added[3].data["due_datetime"])
    assert (due.year, due.month, due.day, due.hour, due.minute) == (2026, 10, 2, 18, 0)
    assert due.tzinfo is not None  # local time, not UTC
    assert str(fakes.added[4].data["due_date"]) == "2026-10-03"

    ev = {c.data["summary"]: c.data for c in fakes.events}

    def minutes(e):
        start, end = (datetime.fromisoformat(e[k]) for k in ("start_date_time", "end_date_time"))
        assert start.tzinfo is not None and start.hour in (14, 19)
        return (end - start).seconds // 60
    assert minutes(ev["Zahnarzt"]) == 90
    assert minutes(ev["Dinner"]) == 45
    assert str(ev["Holiday"]["start_date"]) == "2026-10-20"
    assert str(ev["Holiday"]["end_date"]) == "2026-10-21"

    # every command but the message was confirmed on the phone first
    assert len(fakes.asks) == 8
    assert fakes.asks[0].data["message"] == "To-do: Vermieter anrufen"
    assert fakes.sent == ["Bin gleich da"]

    # the stand-ins skip validation: check the payloads against the real schemas
    item_schema = vol.Schema({vol.Required("item"): cv.string, **TODO_ITEM_FIELD_SCHEMA})
    for call in fakes.added:
        item_schema({k: v for k, v in call.data.items() if k != "entity_id"})
    for call in fakes.events:
        CREATE_EVENT_SCHEMA(dict(call.data))
    assert len(done[0].data["done"]) == 9
    assert sum(line.startswith(("✓", "📅")) for line in done[0].data["done"]) == 8


async def test_resynced_page_does_not_add_twice(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "…", "commands": [
        {"type": "todo", "text": "Call landlord"},
        {"type": "event", "text": "Dentist", "start": "2026-10-12 14:00"},
    ]}
    fakes.existing_items["todo.tasks"] = ["call landlord"]
    fakes.existing_events = ["Dentist"]
    await _setup(hass, **ALL_TARGETS)
    done = await _page(hass)
    assert not fakes.added and not fakes.events
    assert not fakes.asks  # nothing to confirm
    assert all(line.startswith("↺") for line in done[0].data["done"])


COMMANDS = [{"type": "todo", "text": "Einkaufen"},
            {"type": "event", "text": "Zahnarzt", "start": "2026-10-12 14:00"}]


async def test_declined_commands_do_nothing(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "…", "commands": COMMANDS}
    fakes.answer = "no"
    await _setup(hass, **ALL_TARGETS, notification_language="de")
    done = await _page(hass)
    assert len(fakes.asks) == 2 and not fakes.added and not fakes.events
    assert fakes.asks[0].data["title"] == "Huion Note — ausführen?"
    assert fakes.asks[0].data["message"] == "Aufgabe: Einkaufen"
    assert all(line.endswith("(abgelehnt)") for line in done[0].data["done"])


async def test_unanswered_commands_expire(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "…", "commands": COMMANDS}
    fakes.answer = None
    await _setup(hass, **ALL_TARGETS, confirm_timeout=0)
    done = await _page(hass)
    assert not fakes.added and not fakes.events
    assert len(fakes.cleared) == 2  # the unanswered questions are removed
    assert all(line.endswith("(not confirmed)") for line in done[0].data["done"])


async def test_commands_without_a_target_are_skipped(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "…", "commands": [
        {"type": "todo", "text": "x"}, {"type": "event", "text": "y", "start": "2026-10-12"},
        {"type": "message", "text": "z"}]}
    await _setup(hass)  # no phone: nothing can be confirmed or sent
    done = await _page(hass)
    assert not fakes.added and not fakes.events and not fakes.messages
    assert all(line.endswith("(no phone set)") for line in done[0].data["done"])


async def test_commands_without_a_list_are_skipped(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "…", "commands": [{"type": "todo", "text": "x"}]}
    await _setup(hass, message_action="notify.mobile_app_phone")
    done = await _page(hass)
    assert not fakes.asks and not fakes.added
    assert done[0].data["done"] == ["⏭ To-do: x (no target set)"]


async def test_pages_outside_the_media_folder_are_skipped(hass: HomeAssistant, fakes) -> None:
    await _setup(hass)
    await _page(hass, png="/config/notes/p.png")
    assert not fakes.ai


async def test_the_media_link_comes_from_the_integration(hass: HomeAssistant, fakes) -> None:
    """Pages in the device's own output folder, e.g. /notes registered as media
    folder "notes": the blueprint uses the link the integration resolved."""
    fakes.reply = {"transcript": "Hi", "commands": []}
    await _setup(hass)
    await _page(hass, png="/notes/20261001T102031Z-page8-5e364b12.png",
                media_id="media-source://media_source/notes/20261001T102031Z-page8-5e364b12.png")
    assert fakes.ai[0].data["attachments"][0]["media_content_id"] == \
        "media-source://media_source/notes/20261001T102031Z-page8-5e364b12.png"


async def test_older_integrations_without_the_link_use_media(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "Hi", "commands": []}
    await _setup(hass)
    await _page(hass, media_id=_OLD)
    assert fakes.ai[0].data["attachments"][0]["media_content_id"] == \
        "media-source://media_source/local/huion_notes/20260930T063000Z-page3-3f9a1c2e.png"


async def test_pages_without_strokes_are_skipped(hass: HomeAssistant, fakes) -> None:
    await _setup(hass)
    await _page(hass, strokes=0)
    assert not fakes.ai


async def test_command_json_in_a_code_fence_is_read(hass: HomeAssistant, fakes) -> None:
    fakes.reply = {"transcript": "…", "commands":
                   '```json\n[{"type": "todo", "text": "Einkaufen"}]\n```'}
    await _setup(hass, **ALL_TARGETS)
    await _page(hass)
    assert [c.data["item"] for c in fakes.added] == ["Einkaufen"]


@pytest.mark.parametrize("bad", ["not json", '{"type": "todo"}', ""])
async def test_unreadable_command_json_still_shows_the_page(hass: HomeAssistant, fakes, bad) -> None:
    fakes.reply = {"transcript": "Hello", "commands": bad}
    await _setup(hass, **ALL_TARGETS)
    done = await _page(hass)
    assert not fakes.asks and not fakes.added
    assert done[0].data["transcript"] == "Hello" and done[0].data["commands"] == []
