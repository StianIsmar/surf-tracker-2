from __future__ import annotations

import json
import secrets
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import keyring
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

YOUTUBE_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
REDIRECT_URI = "http://127.0.0.1:8765/api/youtube/callback"
KEYCHAIN_SERVICE = "local.surftracker.app"
CLIENT_CONFIG_KEY = "youtube-oauth-client"
TOKEN_KEY = "youtube-oauth-token"


class CredentialStore(Protocol):
    def get(self, key: str) -> str | None: ...
    def set(self, key: str, value: str) -> None: ...
    def delete(self, key: str) -> None: ...


class KeychainStore:
    def get(self, key: str) -> str | None:
        return keyring.get_password(KEYCHAIN_SERVICE, key)

    def set(self, key: str, value: str) -> None:
        keyring.set_password(KEYCHAIN_SERVICE, key, value)

    def delete(self, key: str) -> None:
        try:
            keyring.delete_password(KEYCHAIN_SERVICE, key)
        except keyring.errors.PasswordDeleteError:
            pass


def validate_client_config(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != {"installed"}:
        raise ValueError("Choose a Google Desktop OAuth JSON file")
    installed = payload["installed"]
    if not isinstance(installed, dict):
        raise ValueError("Invalid Google OAuth configuration")
    required = ("client_id", "client_secret", "auth_uri", "token_uri")
    if any(not isinstance(installed.get(key), str) or not installed[key] for key in required):
        raise ValueError("Google OAuth configuration is incomplete")
    if installed["auth_uri"] != "https://accounts.google.com/o/oauth2/auth":
        raise ValueError("Unexpected Google authorization endpoint")
    if installed["token_uri"] != "https://oauth2.googleapis.com/token":
        raise ValueError("Unexpected Google token endpoint")
    return {"installed": {key: installed[key] for key in required}}


class YouTubePublisher:
    def __init__(self, store: CredentialStore | None = None) -> None:
        self.store = store or KeychainStore()
        self._oauth_state: str | None = None
        self._flow: Flow | None = None
        self._lock = threading.Lock()
        self._status: dict[str, object] = {"state": "idle", "progress": 0.0}

    def import_client_config(self, payload: object) -> None:
        config = validate_client_config(payload)
        self.store.set(CLIENT_CONFIG_KEY, json.dumps(config))
        self.store.delete(TOKEN_KEY)

    def configured(self) -> bool:
        return self.store.get(CLIENT_CONFIG_KEY) is not None

    def connected(self) -> bool:
        return self.store.get(TOKEN_KEY) is not None

    def authorization_url(self) -> str:
        raw_config = self.store.get(CLIENT_CONFIG_KEY)
        if raw_config is None:
            raise ValueError("Import Google OAuth credentials first")
        state = secrets.token_urlsafe(32)
        flow = Flow.from_client_config(
            json.loads(raw_config), scopes=[YOUTUBE_SCOPE], state=state
        )
        flow.redirect_uri = REDIRECT_URI
        url, returned_state = flow.authorization_url(
            access_type="offline", prompt="consent", include_granted_scopes="true"
        )
        self._oauth_state = returned_state
        self._flow = flow
        return url

    def complete_authorization(self, code: str, state: str) -> None:
        if not self._flow or not self._oauth_state or not secrets.compare_digest(state, self._oauth_state):
            raise ValueError("YouTube authorization expired or was invalid")
        self._flow.fetch_token(code=code)
        credentials = self._flow.credentials
        if not credentials.refresh_token:
            raise ValueError("Google did not provide a refresh token")
        self.store.set(TOKEN_KEY, credentials.to_json())
        self._flow = None
        self._oauth_state = None

    def disconnect(self) -> None:
        self.store.delete(TOKEN_KEY)

    def credentials(self) -> Credentials:
        raw_token = self.store.get(TOKEN_KEY)
        if raw_token is None:
            raise ValueError("Connect a YouTube account first")
        credentials = Credentials.from_authorized_user_info(json.loads(raw_token), [YOUTUBE_SCOPE])
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.store.set(TOKEN_KEY, credentials.to_json())
        if not credentials.valid:
            raise ValueError("Reconnect your YouTube account")
        return credentials

    def public_status(self) -> dict[str, object]:
        with self._lock:
            status = dict(self._status)
        return {
            "configured": self.configured(),
            "connected": self.connected(),
            **status,
        }

    def upload(
        self,
        video_path: Path,
        title: str,
        description: str = "",
        progress_callback: Callable[[float], None] | None = None,
        service: object | None = None,
    ) -> dict[str, str]:
        title = title.strip()
        description = description.strip()
        if not video_path.is_file():
            raise ValueError("Create the final video before publishing")
        if not title or len(title) > 100 or len(description) > 5000:
            raise ValueError("Enter a title up to 100 characters and a description up to 5000")
        youtube = service or build(
            "youtube", "v3", credentials=self.credentials(), cache_discovery=False
        )
        body = {
            "snippet": {"title": title, "description": description},
            "status": {"privacyStatus": "unlisted", "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(str(video_path), chunksize=8 * 1024 * 1024, resumable=True)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            try:
                upload_status, response = request.next_chunk()
            except HttpError as error:
                if error.resp.status in (401, 403):
                    raise ValueError("YouTube authorization or quota prevented the upload") from error
                raise RuntimeError("YouTube upload failed") from error
            if upload_status is not None and progress_callback is not None:
                progress_callback(float(upload_status.progress()))
        if progress_callback is not None:
            progress_callback(1.0)
        video_id = response.get("id")
        if not video_id:
            raise RuntimeError("YouTube did not return a video ID")
        return {"video_id": video_id, "video_url": f"https://youtu.be/{video_id}"}
