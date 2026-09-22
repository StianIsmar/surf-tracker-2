from __future__ import annotations

from pathlib import Path

import pytest
import review_server

from youtube_upload import (
    CLIENT_CONFIG_KEY,
    TOKEN_KEY,
    YouTubePublisher,
    validate_client_config,
)


class MemoryStore:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def desktop_config() -> dict[str, object]:
    return {
        "installed": {
            "client_id": "client.apps.googleusercontent.com",
            "client_secret": "secret",
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }


def test_oauth_config_accepts_only_google_desktop_credentials() -> None:
    assert "installed" in validate_client_config(desktop_config())
    with pytest.raises(ValueError):
        validate_client_config({"web": desktop_config()["installed"]})
    malicious = desktop_config()
    malicious["installed"]["token_uri"] = "https://example.com/token"
    with pytest.raises(ValueError):
        validate_client_config(malicious)


def test_import_stores_credentials_outside_project_and_clears_old_token() -> None:
    store = MemoryStore()
    store.set(TOKEN_KEY, "old token")
    publisher = YouTubePublisher(store)

    publisher.import_client_config(desktop_config())

    assert store.get(CLIENT_CONFIG_KEY) is not None
    assert store.get(TOKEN_KEY) is None
    assert publisher.public_status()["configured"] is True
    assert "client" not in publisher.public_status()


def test_upload_forces_unlisted_privacy_and_reports_progress(tmp_path: Path) -> None:
    video_path = tmp_path / "final.mp4"
    video_path.write_bytes(b"video")
    captured: dict[str, object] = {}
    progress = []

    class UploadStatus:
        def progress(self) -> float:
            return 0.5

    class Request:
        calls = 0

        def next_chunk(self):
            self.calls += 1
            return (UploadStatus(), None) if self.calls == 1 else (None, {"id": "abc123"})

    class Videos:
        def insert(self, **kwargs):
            captured.update(kwargs)
            return Request()

    class Service:
        def videos(self):
            return Videos()

    result = YouTubePublisher(MemoryStore()).upload(
        video_path, "Morning session", "Clean waves", progress.append, Service()
    )

    assert captured["body"]["status"]["privacyStatus"] == "unlisted"
    assert captured["body"]["status"]["selfDeclaredMadeForKids"] is False
    assert progress == [0.5, 1.0]
    assert result == {"video_id": "abc123", "video_url": "https://youtu.be/abc123"}


@pytest.mark.parametrize("fails", [False, True])
def test_publish_removes_temporary_video_only_after_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    session_path = tmp_path / "session.json"
    session_path.write_text(
        '{"source_path":"%s","results_path":"results.json"}' % source_path,
        encoding="utf-8",
    )
    temporary_video = tmp_path / "publish.mp4"

    class Publisher:
        def upload(self, path, title, description, progress_callback):
            assert path == temporary_video
            if fails:
                raise RuntimeError("network unavailable")
            progress_callback(1.0)
            return {"video_id": "video1", "video_url": "https://youtu.be/video1"}

    def fake_export(source, output):
        assert source == source_path
        output.write_bytes(b"compiled")

    monkeypatch.setattr(review_server, "ACTIVE_SESSION_PATH", session_path)
    monkeypatch.setattr(review_server, "YOUTUBE_TEMP_PATH", temporary_video)
    monkeypatch.setattr(review_server, "YOUTUBE_PUBLISHER", Publisher())
    monkeypatch.setattr(review_server, "export_model_compilation", fake_export)
    review_server.PUBLISH_STATUS.clear()
    review_server.PUBLISH_STATUS.update(state="idle", progress=0.0)

    review_server.run_youtube_publish("Session", "")

    assert temporary_video.exists() is fails
    expected_state = "failed" if fails else "published"
    assert review_server.PUBLISH_STATUS["state"] == expected_state


def test_active_publish_blocks_temporary_video_invalidation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary_video = tmp_path / "publish.mp4"
    temporary_video.write_bytes(b"uploading")
    monkeypatch.setattr(review_server, "YOUTUBE_TEMP_PATH", temporary_video)
    review_server.PUBLISH_STATUS.clear()
    review_server.PUBLISH_STATUS.update(state="uploading", progress=0.4)

    with pytest.raises(ValueError, match="publishing to finish"):
        review_server.reset_publish_artifacts()

    assert temporary_video.read_bytes() == b"uploading"
    assert review_server.PUBLISH_STATUS["state"] == "uploading"
    review_server.PUBLISH_STATUS.update(state="idle", progress=0.0)
