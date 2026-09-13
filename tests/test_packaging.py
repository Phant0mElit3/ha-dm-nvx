"""Verify the distribution, translations and install instructions stay coherent."""

import json
from zipfile import ZipFile

import yaml

from scripts.build_release import ROOT, build_release


def test_archive_contains_only_installable_files():
    with ZipFile(build_release()) as archive:
        names = archive.namelist()
        assert "custom_components/crestron_nvx/manifest.json" in names
        assert "custom_components/crestron_nvx/brand/icon.png" in names
        assert "custom_components/crestron_nvx/translations/en.json" in names
        assert "LICENSE" in names
        assert not any("__pycache__" in name or ".env" in name for name in names)


def test_translations_match_and_all_steps_have_labels():
    component = ROOT / "custom_components/crestron_nvx"
    strings = json.loads((component / "strings.json").read_text())
    assert strings == json.loads((component / "translations/en.json").read_text())
    assert set(strings["config"]["step"]) == {"user", "reconfigure", "reauth_confirm"}


def test_examples_use_event_entity_state_triggers():
    examples = yaml.safe_load((ROOT / "configuration_example.yaml").read_text())
    for automation in examples["automation"][:2]:
        assert automation["trigger"][0]["platform"] == "state"
        assert automation["trigger"][0]["entity_id"].startswith("event.")


def test_all_local_documentation_links_exist():
    import re

    for document in ROOT.glob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            if "://" not in target and not target.startswith("#"):
                assert (document.parent / target.split("#")[0]).exists(), (document, target)
