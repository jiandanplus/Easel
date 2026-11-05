import os
import json
import pytest


def provider(**patch):
    from platform_service.media_config import MediaProvider
    return MediaProvider(version="fixture-v1", base_url="http://127.0.0.1:9999/v1", secret="private-fixture", models={"image-fixture": "upstream-image"}, **patch)


def test_unconfigured_media_is_closed_and_has_no_storage_side_effect(tmp_path):
    from platform_service.media_config import MediaSettings
    config = MediaSettings.from_env(None, env={})
    assert config.mode == "disabled" and config.bindings == {} and config.storage_root is None
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("patch", [
    {"base_url": "http://vendor.example/v1"}, {"base_url": "https://user:password@vendor.example/v1"},
    {"base_url": "https://vendor.example/v1?token=secret"}, {"secret": "bad\nkey"},
    {"capabilities": ("audio.speech",)}, {"capabilities": ("image.variation",)},
])
def test_media_configuration_rejects_unsafe_or_unverified_protocol(tmp_path, patch):
    from platform_service.media_config import MediaProvider, MediaSettings
    values = dict(version="v1", base_url="http://127.0.0.1:9999/v1", secret="fixture", models={"public": "upstream"})
    with pytest.raises(ValueError):
        MediaSettings(mode="test", providers={"fixture": MediaProvider(**{**values, **patch})}, storage_root=tmp_path / "private")


def test_binding_freezes_secret_and_protocol_without_revealing_them(tmp_path):
    from platform_service.media_config import MediaProvider, MediaSettings
    first = MediaSettings(mode="test", providers={"fixture": provider()}, storage_root=tmp_path / "media")
    binding = first.bindings["image-fixture"]
    assert binding["adapter"] == "openai_images_v1" and binding["protocol_version"] == "1"
    assert len(binding["binding_hash"]) == 64 and "private-fixture" not in repr(first)
    other = MediaSettings(mode="test", providers={"fixture": MediaProvider("fixture-v1", "http://127.0.0.1:9999/v1", "rotated", {"image-fixture": "upstream-image"})}, storage_root=tmp_path / "media")
    assert first.bindings != other.bindings
    assert not (tmp_path / "media").exists()


def test_private_media_configuration_file_requires_owner_only_permissions(tmp_path):
    from platform_service.media_config import MediaSettings
    path = tmp_path / "media.json"
    payload = {"mode": "test", "storage_root": str(tmp_path / "blobs"), "providers": {"fixture": {"version": "v1", "base_url": "http://127.0.0.1:9999/v1", "secret": "private-fixture", "models": {"public": "upstream"}}}}
    path.write_text(json.dumps(payload)); path.chmod(0o600)
    assert MediaSettings.from_env(None, env={"CENTRAL_MEDIA_CONFIG_FILE": str(path)}).mode == "test"
    path.chmod(0o644)
    with pytest.raises(ValueError):
        MediaSettings.from_env(None, env={"CENTRAL_MEDIA_CONFIG_FILE": str(path)})


@pytest.mark.parametrize("patch", [
    {"base_url": "https://vendor.example/v1"},
    {"base_url": "https://127.0.0.1:9999/v1"},
    {"download_origins": ("https://cdn.vendor.example",)},
])
def test_test_funding_never_targets_public_or_live_https_provider(tmp_path, patch):
    from platform_service.media_config import MediaSettings, MediaProvider
    values = dict(version="v1", base_url="http://127.0.0.1:9999/v1", secret="fixture", models={"public": "upstream"})
    with pytest.raises(ValueError):
        MediaSettings(mode="test", storage_root=tmp_path / "blobs", providers={"fixture": MediaProvider(**(values | patch))})
