"""Models added from the console must be usable by the server and the desktop."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def desktop_contract():
    spec = importlib.util.spec_from_file_location("desktop_media_contracts", ROOT / "easel/platform/media_contracts.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("capability", ["image.generate", "image.edit", "video.generate", "music.generate", "audio.speech", "audio.transcribe"])
def test_console_template_passes_both_contracts(capability):
    from platform_service.media.contracts import media_resources_ready, validate_definition
    from platform_service.model_templates import MEDIA, build_rate

    template = MEDIA[capability]
    definition = validate_definition({
        "id": "console-model", "version": "console-1", "name": "模型", "capability": capability,
        "feature": template["feature"], "products": ["easel"], "test_only": False, "provider_id": "gateway",
        "provider_version": "1", "adapter": "fixture", "protocol_version": "1", "verification": "live",
        "verification_ref": "console-managed:test",
        "rate": build_rate(capability, "console-1", {template["unit"]: "2"}),
        "limits": {**template["limits"], "max_units": {template["unit"]: template["max_units"]}},
        "parameters": template["parameters"]})
    assert media_resources_ready(definition)
    public = desktop_contract().public_definition({**definition, "readiness": "live"})
    assert public["id"] == "console-model"
