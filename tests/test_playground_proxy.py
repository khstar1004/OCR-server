from __future__ import annotations

import importlib
import json
import sys

import httpx
import pytest
from fastapi.testclient import TestClient


def _reset_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/playground/api/auth/login",
        json={"username": "admin", "password": "roqkfrhk1!"},
    )
    assert response.status_code == 200


def test_playground_proxy_links_follow_root_path_when_forwarded_prefix_is_missing(monkeypatch) -> None:
    _reset_app_modules()
    monkeypatch.setenv("PLAYGROUND_UPSTREAM_BASE_URL", "http://a-cong-ocr-service:5000")

    proxy = importlib.import_module("app.playground_proxy")

    async def fake_fetch_upstream_json(request, path: str):
        if path.endswith("/health"):
            return {"ocr_service_ready": True}
        return {"service": "a-cong-ocr", "links": {}}

    monkeypatch.setattr(proxy, "_fetch_upstream_json", fake_fetch_upstream_json)

    with TestClient(proxy.create_app(), root_path="/a-cong-ocr-playground") as client:
        page = client.get("/playground")
        assert page.status_code == 200
        assert '<base href="/a-cong-ocr-playground/">' in page.text
        assert 'href="/a-cong-ocr-playground/docs"' in page.text
        assert 'href="/a-cong-ocr-api/openapi.json"' in page.text
        assert 'href="/a-cong-ocr-api/api/v1/capabilities"' in page.text
        assert 'href="/a-cong-ocr-playground/admin"' in page.text
        assert 'data-pane="runtimeSettingsPane"' not in page.text

        resources = client.get("/playground/api/resources")
        assert resources.status_code == 200
        links = resources.json()["links"]
        assert links["docs"]["url"] == "/a-cong-ocr-playground/docs"
        assert links["openapi"]["url"] == "/a-cong-ocr-api/openapi.json"
        assert links["api_capabilities"]["url"] == "/a-cong-ocr-api/api/v1/capabilities"
        assert links["admin"]["url"] == "/a-cong-ocr-playground/admin"


def test_playground_proxy_runtime_settings_saves_locally_and_forwards(tmp_path, monkeypatch) -> None:
    _reset_app_modules()
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "runtime-settings.json"))
    monkeypatch.setenv("AUTH_STORE_PATH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("PLAYGROUND_UPSTREAM_BASE_URL", "http://old-upstream")

    proxy = importlib.import_module("app.playground_proxy")
    calls = []

    async def fake_upstream_request(request, method: str, path: str, *, content: bytes):
        calls.append((method, path, content))
        return httpx.Response(
            200,
            json={
                "path": "/upstream/settings.json",
                "values": {"playground_default_max_pages": 9},
                "overrides": {"playground_default_max_pages": 9},
                "specs": [
                    {
                        "key": "playground_default_max_pages",
                        "value": 9,
                        "has_override": True,
                    }
                ],
            },
        )

    monkeypatch.setattr(proxy, "_upstream_request", fake_upstream_request)

    with TestClient(proxy.create_app()) as client:
        assert client.get("/playground/api/runtime-settings").status_code == 401
        _login_admin(client)
        response = client.put(
            "/playground/api/admin/runtime-settings",
            json={
                "values": {
                    "playground_default_max_pages": 7,
                    "playground_upstream_base_url": "http://new-upstream",
                }
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["values"]["playground_default_max_pages"] == 7
        assert payload["values"]["playground_upstream_base_url"] == "http://new-upstream"
        assert payload["proxy_path"].endswith("runtime-settings.json")
        assert calls[0][0] == "PUT"
        assert calls[0][1] == "/playground/api/admin/runtime-settings"

        resources = client.get("/playground/api/resources")
        assert resources.status_code == 200
        assert resources.json()["links"]["admin"]["url"] == "/playground/admin"


def test_playground_proxy_account_approval_flow(tmp_path, monkeypatch) -> None:
    _reset_app_modules()
    monkeypatch.setenv("AUTH_STORE_PATH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("PLAYGROUND_UPSTREAM_BASE_URL", "http://a-cong-ocr-service:5000")

    proxy = importlib.import_module("app.playground_proxy")

    with TestClient(proxy.create_app()) as client:
        assert client.get("/playground/admin", follow_redirects=False).status_code == 303
        signup = client.post(
            "/playground/api/auth/signup",
            json={"username": "worker1", "password": "strongpass1", "display_name": "작업자"},
        )
        assert signup.status_code == 200
        user_id = signup.json()["user"]["id"]
        assert client.post(
            "/playground/api/auth/login",
            json={"username": "worker1", "password": "strongpass1"},
        ).status_code == 403

        _login_admin(client)
        assert client.get("/playground/admin").status_code == 200
        overview = client.get("/playground/api/admin/overview")
        assert overview.status_code == 200
        assert overview.json()["auth"]["pending_count"] == 1
        users = client.get("/playground/api/admin/users")
        assert any(user["username"] == "worker1" for user in users.json()["users"])
        assert client.post(f"/playground/api/admin/users/{user_id}/approve").status_code == 200
        assert client.post(f"/playground/api/admin/users/{user_id}/suspend").json()["user"]["status"] == "suspended"
        assert client.post(f"/playground/api/admin/users/{user_id}/activate").json()["user"]["status"] == "active"


def test_bootstrap_admin_password_syncs_existing_store(tmp_path, monkeypatch) -> None:
    _reset_app_modules()
    monkeypatch.setenv("AUTH_STORE_PATH", str(tmp_path / "auth.json"))
    monkeypatch.setenv("PLAYGROUND_ADMIN_PASSWORD", "oldpass123!")

    config = importlib.import_module("app.core.config")
    auth_store = importlib.import_module("app.services.auth_store")
    first_store = auth_store.AuthStore(config.Settings())
    assert first_store.authenticate("admin", "oldpass123!")["role"] == "admin"

    monkeypatch.setenv("PLAYGROUND_ADMIN_PASSWORD", "roqkfrhk1!")
    second_store = auth_store.AuthStore(config.Settings())
    assert second_store.authenticate("admin", "roqkfrhk1!")["role"] == "admin"
    with pytest.raises(ValueError):
        second_store.authenticate("admin", "oldpass123!")


def test_playground_proxy_forwards_history(monkeypatch) -> None:
    _reset_app_modules()
    monkeypatch.setenv("PLAYGROUND_UPSTREAM_BASE_URL", "http://a-cong-ocr-service:5000")

    proxy = importlib.import_module("app.playground_proxy")
    calls = []

    async def fake_upstream_request(request, method: str, path: str, *, content: bytes):
        calls.append((method, path, request.url.query, content))
        return httpx.Response(
            200,
            json={
                "success": True,
                "items": [
                    {
                        "request_id": "abc123",
                        "status": "complete",
                        "file_name": "page.png",
                    }
                ],
            },
        )

    monkeypatch.setattr(proxy, "_upstream_request", fake_upstream_request)

    with TestClient(proxy.create_app()) as client:
        response = client.get("/playground/api/history?limit=5")

        assert response.status_code == 200
        assert response.json()["items"][0]["request_id"] == "abc123"
        assert calls == [("GET", "/playground/api/history", "limit=5", b"")]


def test_playground_proxy_forwards_block_edits(monkeypatch) -> None:
    _reset_app_modules()
    monkeypatch.setenv("PLAYGROUND_UPSTREAM_BASE_URL", "http://a-cong-ocr-service:5000")

    proxy = importlib.import_module("app.playground_proxy")
    calls = []

    async def fake_upstream_request(request, method: str, path: str, *, content: bytes):
        calls.append((method, path, request.url.query, content))
        return httpx.Response(200, json={"success": True, "request_id": "abc123", "pages": []})

    monkeypatch.setattr(proxy, "_upstream_request", fake_upstream_request)

    with TestClient(proxy.create_app()) as client:
        response = client.put(
            "/playground/api/convert/abc123/blocks/29/6",
            json={"label": "text", "text": "수정본"},
        )

        assert response.status_code == 200
        assert response.json()["success"] is True
        assert len(calls) == 1
        method, path, query, content = calls[0]
        assert method == "PUT"
        assert path == "/playground/api/convert/abc123/blocks/29/6"
        assert query == ""
        assert json.loads(content.decode("utf-8")) == {"label": "text", "text": "수정본"}
