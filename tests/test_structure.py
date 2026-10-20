"""Validate distributable HACS repository metadata and language files."""

import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hacs_manifest_brand_files():
    base = ROOT / "custom_components" / "db_fahrplan"
    manifest = json.loads((base / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == "db_fahrplan"
    assert manifest["config_flow"] is True
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["codeowners"]
    assert all(manifest[k] for k in ["version", "documentation", "issue_tracker", "name"])
    assert hacs["homeassistant"] == "2026.3.0"
    data = (base / "brand/icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", data[16:24]) == (256, 256)
    assert len(list((ROOT / "custom_components").iterdir())) == 1


def test_translation_files_have_equivalent_steps():
    base = ROOT / "custom_components" / "db_fahrplan"
    en = json.loads((base / "strings.json").read_text())
    de = json.loads((base / "translations/de.json").read_text())
    assert en.keys() == de.keys()
    for key in ["user", "reconfigure"]:
        assert key in en["config_subentries"]["route"]["step"]
        assert key in de["config_subentries"]["route"]["step"]
    for locale in (en, de):
        route = locale["config_subentries"]["route"]
        assert "choose_stations" not in route["step"]
        assert "not_enough_saved_stations" in route["abort"]
        for step in ("user", "reconfigure"):
            fields = route["step"][step]["data"]
            assert {"from_eva", "to_eva"} <= fields.keys()
            assert "from_query" not in fields and "to_query" not in fields
    assert en["entity"]["sensor"].keys() == de["entity"]["sensor"].keys()


def test_button_platform_json_sensor_and_translations():
    base = ROOT / "custom_components" / "db_fahrplan"
    init = (base / "__init__.py").read_text()
    assert "Platform.BUTTON" in init
    button = (base / "button.py").read_text()
    assert "async_manual_refresh" in button
    sensor = (base / "sensor.py").read_text()
    assert '"json_route"' in sensor
    assert '"average_delay"' in sensor
    assert "DepartureFieldSensor" in sensor
    assert "history_summary" in sensor
    assert "DepartureDelayHistory" in (base / "coordinator.py").read_text()
    assert "data[CONF_FROM_EVA]" in (base / "coordinator.py").read_text()
    assert "data[CONF_TO_EVA]" in (base / "coordinator.py").read_text()
    for name in ("strings.json", "translations/de.json", "translations/en.json"):
        translated = json.loads((base / name).read_text())
        assert "refresh" in translated["entity"]["button"]
        for key in ("departure_time", "departure_date", "line", "terminal", "platform", "delay_minutes", "average_delay", "json_route"):
            assert key in translated["entity"]["sensor"]
        assert "set_display_name" in translated["options"]["step"]["init"]["menu_options"]
        assert "history_days" in translated["config_subentries"]["route"]["step"]["user"]["data"]
