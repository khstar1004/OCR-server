from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.services.auth_store import AUTH_COOKIE_NAME, current_user_from_request, get_auth_store, require_admin_user
from app.services.runtime_config import get_runtime_config_store, runtime_config_value

_REPO_ROOT = Path(__file__).resolve().parents[1]
_PLAYGROUND_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "index.html"
_PLAYGROUND_DOCS_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "docs.html"
_PLAYGROUND_API_GUIDE_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "api_guide.html"
_PLAYGROUND_API_REFERENCE_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "api_reference.html"
_PLAYGROUND_AUTH_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "auth.html"
_PLAYGROUND_ADMIN_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "admin.html"
_PLAYGROUND_STATIC_ROOT = (_REPO_ROOT / "static" / "playground").resolve()
_PLAYGROUND_GUIDE_MARKDOWN = _REPO_ROOT / "docs" / "ocr_playground_api_guide.md"


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Army-OCR UI",
        version="0.1.0",
        root_path=settings.normalized_root_path,
    )

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "Army-OCR-playground"}

    @app.get("/playground", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/playground/", response_class=HTMLResponse, include_in_schema=False)
    def get_playground(request: Request) -> HTMLResponse:
        html = _PLAYGROUND_TEMPLATE.read_text(encoding="utf-8")
        html = html.replace("__PLAYGROUND_BASE__", _external_playground_base(request))
        links = _resource_links(request)
        html = html.replace("__DOCS_URL__", links["docs"]["url"])
        html = html.replace("__API_GUIDE_URL__", links["api_guide"]["url"])
        html = html.replace("__API_REFERENCE_URL__", links["api_reference"]["url"])
        html = html.replace("__OPENAPI_URL__", links["openapi"]["url"])
        html = html.replace("__API_CAPABILITIES_URL__", links["api_capabilities"]["url"])
        html = html.replace("__OCR_HEALTH_URL__", links["ocr_health"]["url"])
        html = html.replace("__ADMIN_URL__", links["admin"]["url"])
        html = html.replace(
            'href="admin" target="_blank" rel="noopener" data-resource-link="admin"',
            f'href="{links["admin"]["url"]}" target="_blank" rel="noopener" data-resource-link="admin"',
        )
        return HTMLResponse(html)

    @app.get("/playground/login", response_class=HTMLResponse, include_in_schema=False)
    def get_playground_login(request: Request) -> HTMLResponse:
        user = current_user_from_request(request)
        if user and user.get("role") == "admin":
            return RedirectResponse(url=_resource_links(request)["admin"]["url"], status_code=303)
        return _render_docs_template(request, _PLAYGROUND_AUTH_TEMPLATE.read_text(encoding="utf-8"))

    @app.get("/playground/admin", response_class=HTMLResponse, include_in_schema=False)
    def get_playground_admin(request: Request) -> HTMLResponse:
        user = current_user_from_request(request)
        if not user or user.get("role") != "admin":
            return RedirectResponse(url=f"{_resource_prefixes(request)['playground']}/login", status_code=303)
        return _render_docs_template(request, _PLAYGROUND_ADMIN_TEMPLATE.read_text(encoding="utf-8"))

    @app.get("/playground/docs", response_class=HTMLResponse, include_in_schema=False)
    def get_playground_docs(request: Request) -> HTMLResponse:
        return _render_docs_template(request, _PLAYGROUND_DOCS_TEMPLATE.read_text(encoding="utf-8"))

    @app.get("/playground/api-guide", response_class=HTMLResponse, include_in_schema=False)
    def get_playground_api_guide(request: Request) -> HTMLResponse:
        return _render_docs_template(request, _PLAYGROUND_API_GUIDE_TEMPLATE.read_text(encoding="utf-8"))

    @app.get("/playground/api-reference", response_class=HTMLResponse, include_in_schema=False)
    def get_playground_api_reference(request: Request) -> HTMLResponse:
        return _render_docs_template(request, _PLAYGROUND_API_REFERENCE_TEMPLATE.read_text(encoding="utf-8"))

    @app.get("/playground/api-guide.md", include_in_schema=False)
    def get_playground_api_guide_markdown() -> FileResponse:
        if not _PLAYGROUND_GUIDE_MARKDOWN.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="api guide not found")
        return FileResponse(_PLAYGROUND_GUIDE_MARKDOWN, media_type="text/markdown; charset=utf-8")

    @app.get("/playground/assets/{asset_path:path}", include_in_schema=False)
    def get_playground_asset(asset_path: str) -> FileResponse:
        path = (_PLAYGROUND_STATIC_ROOT / asset_path).resolve()
        try:
            path.relative_to(_PLAYGROUND_STATIC_ROOT)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found") from None
        if not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
        return FileResponse(path)

    @app.get("/playground/api/health")
    async def get_playground_health(request: Request) -> dict[str, Any]:
        upstream = await _fetch_upstream_json(request, "/playground/api/health")
        ready = bool(upstream and upstream.get("ocr_service_ready"))
        return {
            "status": "ok" if ready else "starting",
            "service": "Army-OCR-playground",
            "ocr_service_ready": ready,
            "upstream": upstream or {},
        }

    @app.get("/playground/api/capabilities")
    async def get_playground_capabilities(request: Request) -> dict[str, Any]:
        upstream = await _fetch_upstream_json(request, "/playground/api/capabilities")
        if upstream:
            upstream["links"] = _resource_links(request)
            return upstream
        return {
            "service": "Army-OCR",
            "playground": True,
            "input_formats": ["pdf", "png", "jpg", "jpeg", "webp"],
            "output_formats": ["json", "markdown", "html", "chunks", "zip"],
            "marker_modes": ["fast", "balanced", "accurate"],
            "features": {
                "page_range": True,
                "max_pages": True,
                "file_url": True,
                "tables": True,
                "layout_block_labels": [],
            },
            "links": _resource_links(request),
        }

    @app.get("/playground/api/resources")
    async def get_playground_resources(request: Request) -> dict[str, Any]:
        return {
            "status": "ok",
            "links": _resource_links(request),
            "health": await get_playground_health(request),
            "capabilities": await get_playground_capabilities(request),
        }

    @app.get("/playground/api/auth/me")
    def get_auth_me(request: Request) -> dict[str, Any]:
        user = current_user_from_request(request)
        return {
            "authenticated": user is not None,
            "user": user,
            "admin": bool(user and user.get("role") == "admin"),
        }

    @app.post("/playground/api/auth/signup")
    async def signup_account(request: Request) -> dict[str, Any]:
        payload = _json_object(await request.body())
        try:
            user = get_auth_store(get_settings()).request_account(
                username=str(payload.get("username") or ""),
                password=str(payload.get("password") or ""),
                display_name=str(payload.get("display_name") or ""),
                email=str(payload.get("email") or ""),
                reason=str(payload.get("reason") or ""),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        return {"success": True, "user": user}

    @app.post("/playground/api/auth/login")
    async def login_account(request: Request) -> JSONResponse:
        payload = _json_object(await request.body())
        store = get_auth_store(get_settings())
        try:
            user = store.authenticate(str(payload.get("username") or ""), str(payload.get("password") or ""))
            session = store.create_session(str(user["id"]))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from None
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from None
        response = JSONResponse({"success": True, "user": session.user, "expires_at": session.expires_at})
        response.set_cookie(
            AUTH_COOKIE_NAME,
            session.session_id,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
            max_age=max(int(getattr(get_settings(), "playground_session_days", 7)), 1) * 24 * 60 * 60,
            path="/",
        )
        return response

    @app.post("/playground/api/auth/logout")
    def logout_account(request: Request) -> JSONResponse:
        get_auth_store(get_settings()).delete_session(request.cookies.get(AUTH_COOKIE_NAME))
        response = JSONResponse({"success": True})
        response.delete_cookie(AUTH_COOKIE_NAME, path="/")
        return response

    @app.get("/playground/api/admin/users")
    def list_admin_users(request: Request) -> dict[str, Any]:
        require_admin_user(request)
        store = get_auth_store(get_settings())
        return {"success": True, "users": store.list_users(), "summary": store.snapshot()}

    @app.get("/playground/api/admin/overview")
    async def admin_overview(request: Request) -> dict[str, Any]:
        admin = require_admin_user(request)
        settings = get_settings()
        store = get_auth_store(settings)
        local_settings = get_runtime_config_store(settings).snapshot()
        upstream_settings = await _fetch_upstream_json(request, "/playground/api/admin/runtime-settings")
        settings_payload = _merge_runtime_settings(upstream_settings, local_settings) if upstream_settings else local_settings
        return {
            "success": True,
            "user": admin,
            "auth": store.snapshot(),
            "runtime": _runtime_overview(settings_payload),
            "settings": settings_payload,
            "health": await get_playground_health(request),
            "capabilities": await get_playground_capabilities(request),
        }

    @app.post("/playground/api/admin/users/{user_id}/approve")
    def approve_admin_user(request: Request, user_id: str) -> dict[str, Any]:
        admin = require_admin_user(request)
        try:
            user = get_auth_store(get_settings()).approve_user(user_id, approved_by=str(admin.get("username") or "admin"))
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
        return {"success": True, "user": user}

    @app.post("/playground/api/admin/users/{user_id}/reject")
    def reject_admin_user(request: Request, user_id: str) -> dict[str, Any]:
        admin = require_admin_user(request)
        try:
            user = get_auth_store(get_settings()).reject_user(user_id, rejected_by=str(admin.get("username") or "admin"))
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        return {"success": True, "user": user}

    @app.post("/playground/api/admin/users/{user_id}/suspend")
    def suspend_admin_user(request: Request, user_id: str) -> dict[str, Any]:
        admin = require_admin_user(request)
        try:
            user = get_auth_store(get_settings()).suspend_user(user_id, suspended_by=str(admin.get("username") or "admin"))
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
        return {"success": True, "user": user}

    @app.post("/playground/api/admin/users/{user_id}/activate")
    def activate_admin_user(request: Request, user_id: str) -> dict[str, Any]:
        admin = require_admin_user(request)
        try:
            user = get_auth_store(get_settings()).activate_user(user_id, activated_by=str(admin.get("username") or "admin"))
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
        return {"success": True, "user": user}

    @app.get("/playground/api/admin/runtime-settings")
    async def proxy_admin_runtime_settings(request: Request) -> dict[str, Any]:
        require_admin_user(request)
        local = get_runtime_config_store(get_settings()).snapshot()
        upstream = await _fetch_upstream_json(request, "/playground/api/admin/runtime-settings")
        if upstream:
            return _merge_runtime_settings(upstream, local)
        local["warning"] = "OCR API 설정 endpoint에 연결하지 못해 playground proxy 로컬 설정만 표시합니다."
        return local

    @app.put("/playground/api/admin/runtime-settings")
    async def proxy_update_admin_runtime_settings(request: Request) -> dict[str, Any]:
        require_admin_user(request)
        body = await request.body()
        payload = _json_object(body)
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="values object is required")

        upstream: dict[str, Any] = {}
        warning = ""
        try:
            response = await _upstream_request(request, "PUT", "/playground/api/admin/runtime-settings", content=body)
            if 200 <= response.status_code < 300:
                upstream_payload = response.json()
                upstream = upstream_payload if isinstance(upstream_payload, dict) else {}
            else:
                warning = f"OCR API 설정 저장은 HTTP {response.status_code}로 실패했고 proxy 로컬 설정만 저장했습니다."
        except Exception as exc:  # noqa: BLE001
            warning = f"OCR API 설정 endpoint에 연결하지 못해 proxy 로컬 설정만 저장했습니다: {exc}"

        try:
            local = get_runtime_config_store(get_settings()).save(values)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

        result = _merge_runtime_settings(upstream, local) if upstream else local
        if warning:
            result["warning"] = warning
        return result

    @app.get("/playground/api/runtime-settings")
    async def proxy_runtime_settings(request: Request) -> dict[str, Any]:
        require_admin_user(request)
        local = get_runtime_config_store(get_settings()).snapshot()
        upstream = await _fetch_upstream_json(request, "/playground/api/runtime-settings")
        if upstream:
            return _merge_runtime_settings(upstream, local)
        local["warning"] = "OCR API 설정 endpoint에 연결하지 못해 playground proxy 로컬 설정만 표시합니다."
        return local

    @app.put("/playground/api/runtime-settings")
    async def proxy_update_runtime_settings(request: Request) -> dict[str, Any]:
        require_admin_user(request)
        body = await request.body()
        payload = _json_object(body)
        values = payload.get("values") if isinstance(payload, dict) else None
        if not isinstance(values, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="values object is required")

        upstream: dict[str, Any] = {}
        warning = ""
        try:
            response = await _upstream_request(request, "PUT", "/playground/api/runtime-settings", content=body)
            if 200 <= response.status_code < 300:
                upstream_payload = response.json()
                upstream = upstream_payload if isinstance(upstream_payload, dict) else {}
            else:
                warning = f"OCR API 설정 저장은 HTTP {response.status_code}로 실패했고 proxy 로컬 설정만 저장했습니다."
        except Exception as exc:  # noqa: BLE001
            warning = f"OCR API 설정 endpoint에 연결하지 못해 proxy 로컬 설정만 저장했습니다: {exc}"

        try:
            local = get_runtime_config_store(get_settings()).save(values)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None

        result = _merge_runtime_settings(upstream, local) if upstream else local
        if warning:
            result["warning"] = warning
        return result

    @app.get("/playground/api/history")
    async def proxy_history(request: Request) -> Response:
        return await _proxy_to_upstream(request, "/playground/api/history")

    @app.post("/playground/api/convert")
    async def proxy_convert(request: Request) -> Response:
        return await _proxy_to_upstream(request, "/playground/api/convert")

    @app.post("/playground/api/convert/start")
    async def proxy_convert_start(request: Request) -> Response:
        return await _proxy_to_upstream(request, "/playground/api/convert/start")

    @app.get("/playground/api/convert/{request_id}")
    async def proxy_convert_result(request: Request, request_id: str) -> Response:
        return await _proxy_to_upstream(request, f"/playground/api/convert/{request_id}")

    @app.put("/playground/api/convert/{request_id}/blocks/{page_index}/{block_index}")
    async def proxy_update_convert_block(
        request: Request,
        request_id: str,
        page_index: int,
        block_index: int,
    ) -> Response:
        return await _proxy_to_upstream(
            request,
            f"/playground/api/convert/{request_id}/blocks/{page_index}/{block_index}",
        )

    @app.get("/playground/api/images/{request_id}/{asset_name}")
    async def proxy_image(request: Request, request_id: str, asset_name: str) -> Response:
        return await _proxy_to_upstream(request, f"/playground/api/images/{request_id}/{asset_name}")

    @app.get("/playground/api/download/{request_id}")
    async def proxy_download(request: Request, request_id: str) -> Response:
        return await _proxy_to_upstream(request, f"/playground/api/download/{request_id}")

    return app


def _render_docs_template(request: Request, html: str) -> HTMLResponse:
    links = _resource_links(request)
    html = html.replace("__PLAYGROUND_BASE__", _external_playground_root_base(request))
    html = html.replace("__PLAYGROUND_URL__", links["playground"]["url"])
    html = html.replace("__DOCS_URL__", links["docs"]["url"])
    html = html.replace("__API_GUIDE_URL__", links["api_guide"]["url"])
    html = html.replace("__API_REFERENCE_URL__", links["api_reference"]["url"])
    html = html.replace("__API_GUIDE_MARKDOWN_URL__", links["api_guide_markdown"]["url"])
    html = html.replace("__OPENAPI_URL__", links["openapi"]["url"])
    html = html.replace("__API_CAPABILITIES_URL__", links["api_capabilities"]["url"])
    html = html.replace("__OCR_HEALTH_URL__", links["ocr_health"]["url"])
    html = html.replace("__ADMIN_URL__", links["admin"]["url"])
    return HTMLResponse(html)


async def _fetch_upstream_json(request: Request, path: str, *, timeout_sec: float | None = 5.0) -> dict[str, Any]:
    try:
        response = await _upstream_request(request, "GET", path, content=b"", timeout_sec=timeout_sec)
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _proxy_to_upstream(request: Request, path: str) -> Response:
    response = await _upstream_request(request, request.method, path, content=await request.body())
    headers = _response_headers(response)
    return Response(
        content=response.content,
        status_code=response.status_code,
        media_type=response.headers.get("content-type"),
        headers=headers,
    )


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8") if body else "{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON object is required")
    return payload


def _merge_runtime_settings(upstream: dict[str, Any], local: dict[str, Any]) -> dict[str, Any]:
    if not upstream:
        return local
    merged = dict(upstream)
    merged["proxy_path"] = local.get("path")
    if upstream.get("path"):
        merged["upstream_path"] = upstream.get("path")
    local_overrides = local.get("overrides") if isinstance(local.get("overrides"), dict) else {}
    if not local_overrides:
        return merged
    values = dict(merged.get("values") if isinstance(merged.get("values"), dict) else {})
    specs = list(merged.get("specs") if isinstance(merged.get("specs"), list) else [])
    for key, value in local_overrides.items():
        values[key] = value
        for spec in specs:
            if isinstance(spec, dict) and spec.get("key") == key:
                spec["value"] = value
                spec["has_override"] = True
                break
    merged["values"] = values
    merged["specs"] = specs
    return merged


def _runtime_overview(payload: dict[str, Any]) -> dict[str, Any]:
    specs = payload.get("specs") if isinstance(payload.get("specs"), list) else []
    overrides = payload.get("overrides") if isinstance(payload.get("overrides"), dict) else {}
    restart_specs = [
        spec
        for spec in specs
        if isinstance(spec, dict) and spec.get("restart_required") and spec.get("has_override")
    ]
    groups: dict[str, int] = {}
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        group = str(spec.get("group") or "other")
        groups[group] = groups.get(group, 0) + 1
    return {
        "path": payload.get("path"),
        "updated_at": payload.get("updated_at"),
        "override_count": len(overrides),
        "setting_count": len(specs),
        "restart_required_override_count": len(restart_specs),
        "restart_required_keys": [str(spec.get("key") or "") for spec in restart_specs],
        "groups": groups,
    }


async def _upstream_request(
    request: Request,
    method: str,
    path: str,
    *,
    content: bytes,
    timeout_sec: float | None = None,
) -> httpx.Response:
    url = f"{_upstream_base_url()}{path}"
    if request.url.query:
        url = f"{url}?{request.url.query}"
    headers = _upstream_headers(request)
    timeout = httpx.Timeout(timeout_sec) if timeout_sec is not None else httpx.Timeout(None)
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.request(method, url, content=content, headers=headers)


def _upstream_base_url() -> str:
    settings = get_settings()
    value = (
        runtime_config_value("playground_upstream_base_url", os.getenv("PLAYGROUND_UPSTREAM_BASE_URL") or "", settings)
        or os.getenv("PLAYGROUND_UPSTREAM_BASE_URL")
        or os.getenv("OCR_SERVICE_URL")
        or str(settings.ocr_service_url or "")
        or "http://a-cong-ocr-service:5000"
    )
    return value.rstrip("/")


def _upstream_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in ("accept", "content-type", "authorization", "cookie"):
        value = request.headers.get(name)
        if value:
            headers[name] = value
    prefix = request.headers.get("x-forwarded-prefix") or request.scope.get("root_path")
    if prefix:
        headers["x-forwarded-prefix"] = str(prefix)
    return headers


def _response_headers(response: httpx.Response) -> dict[str, str]:
    headers: dict[str, str] = {}
    for name in ("content-disposition", "cache-control"):
        value = response.headers.get(name)
        if value:
            headers[name] = value
    return headers


def _external_playground_base(request: Request) -> str:
    prefix = str(
        request.headers.get("x-forwarded-prefix")
        or request.scope.get("root_path")
        or ""
    ).strip()
    if prefix:
        normalized = "/" + prefix.strip("/")
        if normalized.endswith("-playground"):
            return f"{normalized}/"
        return f"{normalized}/playground/"
    path = request.url.path
    if not path.endswith("/"):
        path = f"{path}/"
    return path


def _external_playground_root_base(request: Request) -> str:
    prefix = str(
        request.headers.get("x-forwarded-prefix")
        or request.scope.get("root_path")
        or ""
    ).strip()
    if prefix:
        normalized = "/" + prefix.strip("/")
        if normalized.endswith("-playground"):
            return f"{normalized}/"
        return f"{normalized}/playground/"
    return "/playground/"


def _resource_links(request: Request) -> dict[str, dict[str, str]]:
    prefixes = _resource_prefixes(request)
    return {
        "playground": {"label": "OCR 실행", "url": prefixes["playground"], "kind": "html"},
        "docs": {"label": "문서", "url": f"{prefixes['playground']}/docs", "kind": "html"},
        "api_reference": {
            "label": "API 목록",
            "url": f"{prefixes['playground']}/api-reference",
            "kind": "html",
        },
        "api_capabilities": {
            "label": "기능 확인",
            "url": f"{prefixes['api']}/api/v1/capabilities",
            "kind": "json",
        },
        "openapi": {"label": "OpenAPI 원본", "url": f"{prefixes['api']}/openapi.json", "kind": "json"},
        "api_guide": {"label": "API 사용법", "url": f"{prefixes['playground']}/api-guide", "kind": "html"},
        "api_guide_markdown": {
            "label": "OCR 사용법 Markdown",
            "url": f"{prefixes['playground']}/api-guide.md",
            "kind": "markdown",
        },
        "ocr_health": {"label": "OCR 상태", "url": f"{prefixes['api']}/health", "kind": "json"},
        "admin": {"label": "관리자", "url": f"{prefixes['playground']}/admin", "kind": "html"},
    }


def _resource_prefixes(request: Request) -> dict[str, str]:
    prefix = str(request.headers.get("x-forwarded-prefix") or "").strip()
    root_path = str(request.scope.get("root_path") or "").strip()
    normalized = "/" + prefix.strip("/") if prefix else ""
    if not normalized and root_path:
        normalized = "/" + root_path.strip("/")
    if not normalized:
        return {"api": "", "app": "", "playground": "/playground"}

    if normalized.endswith("-playground"):
        base = normalized[: -len("-playground")]
    elif normalized.endswith("-api"):
        base = normalized[: -len("-api")]
    else:
        base = normalized

    if not base:
        base = normalized
    return {
        "api": f"{base}-api" if not base.endswith("-api") else base,
        "app": base,
        "playground": f"{base}-playground" if not base.endswith("-playground") else base,
    }


app = create_app()
