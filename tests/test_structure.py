"""Validate distributable HACS repository metadata and language files."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_hacs_manifest_brand_files():
    base = ROOT / "custom_components" / "db_fahrplan"
    manifest = json.loads((base / "manifest.json").read_text())
    hacs = json.loads((ROOT / "hacs.json").read_text())
    assert manifest["domain"] == "db_fahrplan"
    assert manifest["config_flow"] is True
    assert manifest["codeowners"]
    assert all(manifest[k] for k in ["version", "documentation", "issue_tracker", "name"])
    assert hacs["homeassistant"] == "2026.3.0"
    data = (base / "brand/icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(list((ROOT / "custom_components").iterdir())) == 1


def test_translation_files_have_equivalent_steps():
    base = ROOT / "custom_components" / "db_fahrplan"
    en = json.loads((base / "strings.json").read_text())
    de = json.loads((base / "translations/de.json").read_text())
    assert en.keys() == de.keys()
    for key in ["user", "reconfigure", "choose_stations"]:
        assert key in en["config_subentries"]["route"]["step"]
        assert key in de["config_subentries"]["route"]["step"]
    assert en["entity"]["sensor"].keys() == de["entity"]["sensor"].keys()
