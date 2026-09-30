"""The transcription blueprint, run by Home Assistant's automation engine with a
stand-in `ai_task.generate_data` (no API calls)."""
from __future__ import annotations

import os
import shutil

import pytest

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_capture_events

BLUEPRINT = os.path.join(os.path.dirname(__file__), "..", "blueprints", "automation",
                         "huion_note", "transcribe_page.yaml")
PNG = "/media/huion_notes/20260930T063000Z-page3-3f9a1c2e.png"


@pytest.fixture
async def ai_calls(hass: HomeAssistant):
    calls: list[ServiceCall] = []

    async def generate_data(call: ServiceCall):
        calls.append(call)
        return {"conversation_id": "x", "data": "Buy milk\n- eggs"}

    hass.services.async_register("ai_task", "generate_data", generate_data,
                                 supports_response=SupportsResponse.ONLY)
    return calls


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


def _saved(png=PNG, strokes=5):
    return {"page": 3, "strokes": strokes, "complete": True, "png": png,
            "svg": png[:-3] + "svg", "json": png[:-3] + "json"}


async def test_page_is_transcribed(hass: HomeAssistant, ai_calls) -> None:
    await _setup(hass, language="German", actions=[
        {"event": "my_transcript", "event_data": {"text": "{{ transcript }}"}}])
    done = async_capture_events(hass, "huion_note_page_transcribed")
    mine = async_capture_events(hass, "my_transcript")

    hass.bus.async_fire("huion_note_page_saved", _saved())
    await hass.async_block_till_done()

    assert len(ai_calls) == 1
    data = ai_calls[0].data
    assert data["entity_id"] == "ai_task.claude_ai_task"
    assert data["attachments"] == [{
        "media_content_id":
            "media-source://media_source/local/huion_notes/20260930T063000Z-page3-3f9a1c2e.png",
        "media_content_type": "image/png",
    }]
    assert "in German" in data["instructions"]
    assert done[0].data["transcript"] == "Buy milk\n- eggs"
    assert mine[0].data["text"] == "Buy milk\n- eggs"
    notes = hass.data["persistent_notification"]
    assert any(n["message"] == "Buy milk\n- eggs" for n in notes.values())


async def test_pages_outside_the_media_folder_are_skipped(hass: HomeAssistant, ai_calls) -> None:
    await _setup(hass)
    hass.bus.async_fire("huion_note_page_saved", _saved(png="/config/notes/p.png"))
    await hass.async_block_till_done()
    assert not ai_calls


async def test_pages_without_strokes_are_skipped(hass: HomeAssistant, ai_calls) -> None:
    await _setup(hass)
    hass.bus.async_fire("huion_note_page_saved", _saved(strokes=0))
    await hass.async_block_till_done()
    assert not ai_calls
