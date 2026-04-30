from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.services.datalab_compat import DatalabCompatService, normalize_marker_mode, parse_page_range
from app.services.playground_export import (
    build_playground_export_zip,
    build_playground_partial_response_payload,
    build_playground_response_payload,
    find_playground_asset,
    read_asset_bytes,
)
from app.services.runtime_config import get_runtime_config_store, runtime_config_value
from app.services.auth_store import AUTH_COOKIE_NAME, current_user_from_request, get_auth_store, require_admin_user

router = APIRouter(prefix="/playground", tags=["playground"])

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PLAYGROUND_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "index.html"
_PLAYGROUND_DOCS_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "docs.html"
_PLAYGROUND_API_GUIDE_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "api_guide.html"
_PLAYGROUND_API_REFERENCE_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "api_reference.html"
_PLAYGROUND_AUTH_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "auth.html"
_PLAYGROUND_ADMIN_TEMPLATE = _REPO_ROOT / "templates" / "playground" / "admin.html"
_PLAYGROUND_STATIC_ROOT = (_REPO_ROOT / "static" / "playground").resolve()
_PLAYGROUND_GUIDE_MARKDOWN = _REPO_ROOT / "docs" / "ocr_playground_api_guide.md"
DEFAULT_MAX_PLAYGROUND_UPLOAD_BYTES = 512 * 1024 * 1024


@router.get("", response_class=HTMLResponse, include_in_schema=False)
@router.get("/", response_class=HTMLResponse, include_in_schema=False)
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
    return HTMLResponse(html)


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
def get_playground_login(request: Request) -> HTMLResponse:
    user = current_user_from_request(request)
    if user and user.get("role") == "admin":
        return RedirectResponse(url=_resource_links(request)["admin"]["url"], status_code=303)
    return _render_docs_template(request, _PLAYGROUND_AUTH_TEMPLATE.read_text(encoding="utf-8"))


@router.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def get_playground_admin(request: Request) -> HTMLResponse:
    user = current_user_from_request(request)
    if not user or user.get("role") != "admin":
        return RedirectResponse(url=f"{_resource_prefixes(request)['playground']}/login", status_code=303)
    return _render_docs_template(request, _PLAYGROUND_ADMIN_TEMPLATE.read_text(encoding="utf-8"))


@router.get("/docs", response_class=HTMLResponse, include_in_schema=False)
def get_playground_docs(request: Request) -> HTMLResponse:
    html = _PLAYGROUND_DOCS_TEMPLATE.read_text(encoding="utf-8")
    return _render_docs_template(request, html)


@router.get("/api-guide", response_class=HTMLResponse, include_in_schema=False)
def get_playground_api_guide(request: Request) -> HTMLResponse:
    html = _PLAYGROUND_API_GUIDE_TEMPLATE.read_text(encoding="utf-8")
    return _render_docs_template(request, html)


@router.get("/api-reference", response_class=HTMLResponse, include_in_schema=False)
def get_playground_api_reference(request: Request) -> HTMLResponse:
    html = _PLAYGROUND_API_REFERENCE_TEMPLATE.read_text(encoding="utf-8")
    return _render_docs_template(request, html)


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


@router.get("/api-guide.md", include_in_schema=False)
def get_playground_api_guide_markdown() -> FileResponse:
    if not _PLAYGROUND_GUIDE_MARKDOWN.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="api guide not found")
    return FileResponse(_PLAYGROUND_GUIDE_MARKDOWN, media_type="text/markdown; charset=utf-8")


@router.get("/assets/{asset_path:path}", include_in_schema=False)
def get_playground_asset(asset_path: str) -> FileResponse:
    path = (_PLAYGROUND_STATIC_ROOT / asset_path).resolve()
    try:
        path.relative_to(_PLAYGROUND_STATIC_ROOT)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found") from None
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="asset not found")
    return FileResponse(path)


@router.get("/api/health")
def get_playground_health(request: Request) -> dict[str, Any]:
    compat = getattr(request.app.state, "datalab_compat", None)
    settings = get_settings()
    return {
        "status": "ok" if isinstance(compat, DatalabCompatService) else "starting",
        "service": "army-ocr-playground",
        "ocr_service_ready": isinstance(compat, DatalabCompatService),
        "ocr_backend": settings.ocr_backend,
        "max_concurrent_ocr_requests": max(
            int(runtime_config_value("ocr_max_concurrent_requests", settings.ocr_max_concurrent_requests, settings) or 1),
            1,
        ),
    }


@router.get("/api/capabilities")
def get_playground_capabilities(request: Request) -> dict[str, Any]:
    compat = getattr(request.app.state, "datalab_compat", None)
    versions: dict[str, Any] = {}
    if isinstance(compat, DatalabCompatService):
        versions = compat.versions()
    settings = get_settings()
    return {
        "service": "army-ocr",
        "playground": True,
        "ocr_backend": settings.ocr_backend,
        "versions": versions,
        "input_formats": ["pdf", "png", "jpg", "jpeg", "webp"],
        "output_formats": ["json", "markdown", "html", "chunks", "zip"],
        "marker_modes": ["fast", "balanced", "accurate"],
        "features": {
            "page_range": True,
            "max_pages": True,
            "file_url": True,
            "blocks": True,
            "bounding_boxes": True,
            "markdown_with_images": True,
            "html_with_images": True,
            "zip_export_with_images": True,
            "runtime_settings": True,
            "max_concurrent_ocr_requests": max(
                int(runtime_config_value("ocr_max_concurrent_requests", settings.ocr_max_concurrent_requests, settings) or 1),
                1,
            ),
            "default_max_pages": int(runtime_config_value("playground_default_max_pages", 10, settings)),
            "max_upload_mb": int(runtime_config_value("playground_max_upload_mb", 512, settings)),
        },
        "links": _resource_links(request),
    }


@router.get("/api/resources")
def get_playground_resources(request: Request) -> dict[str, Any]:
    return {
        "status": "ok",
        "links": _resource_links(request),
        "health": get_playground_health(request),
        "capabilities": get_playground_capabilities(request),
    }


@router.get("/api/auth/me")
def get_auth_me(request: Request) -> dict[str, Any]:
    user = current_user_from_request(request)
    return {
        "authenticated": user is not None,
        "user": user,
        "admin": bool(user and user.get("role") == "admin"),
    }


@router.post("/api/auth/signup")
async def signup_account(request: Request) -> dict[str, Any]:
    payload = await _read_json_object(request)
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


@router.post("/api/auth/login")
async def login_account(request: Request) -> JSONResponse:
    payload = await _read_json_object(request)
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


@router.post("/api/auth/logout")
def logout_account(request: Request) -> JSONResponse:
    get_auth_store(get_settings()).delete_session(request.cookies.get(AUTH_COOKIE_NAME))
    response = JSONResponse({"success": True})
    response.delete_cookie(AUTH_COOKIE_NAME, path="/")
    return response


@router.get("/api/admin/users")
def list_admin_users(request: Request) -> dict[str, Any]:
    require_admin_user(request)
    store = get_auth_store(get_settings())
    return {"success": True, "users": store.list_users(), "summary": store.snapshot()}


@router.post("/api/admin/users/{user_id}/approve")
def approve_admin_user(request: Request, user_id: str) -> dict[str, Any]:
    admin = require_admin_user(request)
    try:
        user = get_auth_store(get_settings()).approve_user(user_id, approved_by=str(admin.get("username") or "admin"))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
    return {"success": True, "user": user}


@router.post("/api/admin/users/{user_id}/reject")
def reject_admin_user(request: Request, user_id: str) -> dict[str, Any]:
    admin = require_admin_user(request)
    try:
        user = get_auth_store(get_settings()).reject_user(user_id, rejected_by=str(admin.get("username") or "admin"))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
    return {"success": True, "user": user}


@router.get("/api/admin/runtime-settings")
def get_admin_runtime_settings(request: Request) -> dict[str, Any]:
    require_admin_user(request)
    return get_runtime_config_store(get_settings()).snapshot()


@router.put("/api/admin/runtime-settings")
async def update_admin_runtime_settings(request: Request) -> dict[str, Any]:
    require_admin_user(request)
    payload = await _read_json_object(request)
    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="values object is required")
    try:
        return get_runtime_config_store(get_settings()).save(values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/api/history")
def get_playground_history(
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    playground_only: bool = Query(default=True),
) -> dict[str, Any]:
    compat = _get_compat(request)
    payload = compat.list_requests(limit=limit, playground_only=playground_only, request_kind="marker")
    for item in payload.get("items", []):
        if not isinstance(item, dict):
            continue
        request_id = str(item.get("request_id") or "")
        if not request_id:
            continue
        item["result_url"] = f"api/convert/{request_id}"
        item["download_url"] = f"api/download/{request_id}"
    return payload


@router.get("/api/runtime-settings")
def get_playground_runtime_settings(request: Request) -> dict[str, Any]:
    require_admin_user(request)
    return get_runtime_config_store(get_settings()).snapshot()


@router.put("/api/runtime-settings")
async def update_playground_runtime_settings(request: Request) -> dict[str, Any]:
    require_admin_user(request)
    payload = await _read_json_object(request)
    values = payload.get("values") if isinstance(payload, dict) else None
    if not isinstance(values, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="values object is required")
    try:
        return get_runtime_config_store(get_settings()).save(values)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.post("/api/convert/start")
async def start_playground_document_conversion(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    file_0: UploadFile | None = File(default=None, alias="file.0"),
    file_url: str | None = Form(default=None),
    page_range: str | None = Form(default="0-9"),
    max_pages: int | None = Form(default=10),
    mode: str = Form(default="balanced"),
    paginate: bool = Form(default=False),
    add_block_ids: bool = Form(default=True),
    include_markdown_in_chunks: bool = Form(default=True),
    skip_cache: bool = Form(default=True),
    track_changes: bool = Form(default=False),
    chart_understanding: bool = Form(default=False),
    infographic_mode: bool = Form(default=False),
    keep_page_header: bool = Form(default=False),
    keep_page_footer: bool = Form(default=False),
    extract_links: bool = Form(default=False),
    new_block_types: bool = Form(default=False),
    table_row_bboxes: bool = Form(default=False),
    disable_image_captions: bool = Form(default=False),
) -> dict[str, Any]:
    compat, request_id, process_kwargs = await _create_playground_marker_request(
        request,
        file=file,
        file_0=file_0,
        file_url=file_url,
        page_range=page_range,
        max_pages=max_pages,
        mode=mode,
        paginate=paginate,
        add_block_ids=add_block_ids,
        include_markdown_in_chunks=include_markdown_in_chunks,
        skip_cache=skip_cache,
        track_changes=track_changes,
        chart_understanding=chart_understanding,
        infographic_mode=infographic_mode,
        keep_page_header=keep_page_header,
        keep_page_footer=keep_page_footer,
        extract_links=extract_links,
        new_block_types=new_block_types,
        table_row_bboxes=table_row_bboxes,
        disable_image_captions=disable_image_captions,
    )
    _start_playground_worker(compat, request_id, process_kwargs)
    return {
        "success": True,
        "status": "processing",
        "request_id": request_id,
        "result_url": f"api/convert/{request_id}",
        "download_url": f"api/download/{request_id}",
    }


@router.post("/api/convert")
async def convert_playground_document(
    request: Request,
    file: UploadFile | None = File(default=None),
    file_0: UploadFile | None = File(default=None, alias="file.0"),
    file_url: str | None = Form(default=None),
    page_range: str | None = Form(default="0-9"),
    max_pages: int | None = Form(default=10),
    mode: str = Form(default="balanced"),
    paginate: bool = Form(default=False),
    add_block_ids: bool = Form(default=True),
    include_markdown_in_chunks: bool = Form(default=True),
    skip_cache: bool = Form(default=True),
    track_changes: bool = Form(default=False),
    chart_understanding: bool = Form(default=False),
    infographic_mode: bool = Form(default=False),
    keep_page_header: bool = Form(default=False),
    keep_page_footer: bool = Form(default=False),
    extract_links: bool = Form(default=False),
    new_block_types: bool = Form(default=False),
    table_row_bboxes: bool = Form(default=False),
    disable_image_captions: bool = Form(default=False),
) -> dict[str, Any]:
    compat, request_id, process_kwargs = await _create_playground_marker_request(
        request,
        file=file,
        file_0=file_0,
        file_url=file_url,
        page_range=page_range,
        max_pages=max_pages,
        mode=mode,
        paginate=paginate,
        add_block_ids=add_block_ids,
        include_markdown_in_chunks=include_markdown_in_chunks,
        skip_cache=skip_cache,
        track_changes=track_changes,
        chart_understanding=chart_understanding,
        infographic_mode=infographic_mode,
        keep_page_header=keep_page_header,
        keep_page_footer=keep_page_footer,
        extract_links=extract_links,
        new_block_types=new_block_types,
        table_row_bboxes=table_row_bboxes,
        disable_image_captions=disable_image_captions,
    )
    await run_in_threadpool(
        compat.process_marker_request,
        request_id,
        **process_kwargs,
    )
    response_payload = _playground_result_payload(compat, request_id)
    if not response_payload["success"]:
        response_payload["error"] = response_payload["error"] or "OCR conversion failed"
    return response_payload


@router.get("/api/convert/{request_id}")
def get_playground_conversion_result(request: Request, request_id: str) -> dict[str, Any]:
    compat = _get_compat(request)
    return _playground_result_payload(compat, request_id)


@router.get("/api/images/{request_id}/{asset_name}")
def get_playground_image(request: Request, request_id: str, asset_name: str) -> Response:
    compat = _get_compat(request)
    record, result = _get_record_and_result(compat, request_id)
    asset = find_playground_asset(record=record, result=result, asset_name=asset_name)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found")
    try:
        content, media_type = read_asset_bytes(asset)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="image not found") from None
    return Response(content=content, media_type=media_type)


@router.get("/api/download/{request_id}")
def download_playground_result(request: Request, request_id: str) -> Response:
    compat = _get_compat(request)
    record, result = _get_record_and_result(compat, request_id)
    content = build_playground_export_zip(request_id=request_id, record=record, result=result)
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="army-ocr-result-{request_id}.zip"'},
    )


def _get_compat(request: Request) -> DatalabCompatService:
    compat = getattr(request.app.state, "datalab_compat", None)
    if not isinstance(compat, DatalabCompatService):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="OCR service is not ready")
    return compat


def _get_record_and_result(compat: DatalabCompatService, request_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        record = compat.get_request_record(request_id)
        result = compat.get_request_result(request_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="request not found") from None
    if not isinstance(result, dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="result not found")
    return record, result


async def _create_playground_marker_request(
    request: Request,
    *,
    file: UploadFile | None,
    file_0: UploadFile | None,
    file_url: str | None,
    page_range: str | None,
    max_pages: int | None,
    mode: str,
    paginate: bool,
    add_block_ids: bool,
    include_markdown_in_chunks: bool,
    skip_cache: bool,
    track_changes: bool,
    chart_understanding: bool,
    infographic_mode: bool,
    keep_page_header: bool,
    keep_page_footer: bool,
    extract_links: bool,
    new_block_types: bool,
    table_row_bboxes: bool,
    disable_image_captions: bool,
) -> tuple[DatalabCompatService, str, dict[str, Any]]:
    compat = _get_compat(request)
    normalized_mode = _normalize_mode(mode)
    normalized_page_range = _normalize_page_range(page_range)
    normalized_max_pages = _normalize_max_pages(
        max_pages
        if max_pages is not None
        else int(runtime_config_value("playground_default_max_pages", 10, get_settings()))
    )
    payload, filename, input_source = await _read_playground_input(
        compat,
        file=file,
        file_alias=file_0,
        file_url=file_url,
    )
    extras = _extras_payload(
        track_changes=track_changes,
        chart_understanding=chart_understanding,
        infographic_mode=infographic_mode,
        extract_links=extract_links,
        new_block_types=new_block_types,
        table_row_bboxes=table_row_bboxes,
        disable_image_captions=disable_image_captions,
    )
    additional_config = {
        "keep_page_header_in_output": keep_page_header,
        "keep_page_footer_in_output": keep_page_footer,
        "playground": True,
    }
    request_id = compat.create_request(
        "marker",
        meta={
            "file_name": filename,
            "input_source": input_source,
            "output_format": "json,markdown,html,chunks",
            "mode": normalized_mode,
            "playground": True,
        },
    )
    process_kwargs = {
        "file_bytes": payload,
        "file_name": filename,
        "max_pages": normalized_max_pages,
        "page_range": normalized_page_range,
        "output_format": "json,markdown,html,chunks",
        "mode": normalized_mode,
        "paginate": paginate,
        "add_block_ids": add_block_ids,
        "include_markdown_in_chunks": include_markdown_in_chunks,
        "skip_cache": skip_cache,
        "extras": ",".join(extras),
        "additional_config": json.dumps(additional_config, ensure_ascii=False),
    }
    return compat, request_id, process_kwargs


def _playground_result_payload(compat: DatalabCompatService, request_id: str) -> dict[str, Any]:
    record, result = _get_record_and_result(compat, request_id)
    status_value = str(result.get("status") or record.get("status") or "processing")
    if status_value != "complete":
        result_json = result.get("json")
        if isinstance(result_json, dict) and isinstance(result_json.get("pages"), list):
            partial_payload = build_playground_partial_response_payload(
                request_id=request_id,
                record=record,
                result=result,
                image_url_prefix=f"api/images/{request_id}",
            )
            partial_payload["success"] = result.get("success")
            partial_payload["status"] = status_value
            partial_payload["error"] = result.get("error") or record.get("error")
            return partial_payload
        return {
            "success": result.get("success"),
            "status": status_value,
            "request_id": request_id,
            "page_count": result.get("page_count"),
            "processed_page_count": result.get("processed_page_count") or 0,
            "progress": result.get("progress")
            or {
                "status": status_value,
                "processed_pages": 0,
                "total_pages": int(result.get("page_count") or 0),
                "percent": 0.0,
            },
            "parse_quality_score": result.get("parse_quality_score"),
            "metadata": result.get("metadata") or record.get("meta") or {},
            "pages": [],
            "assets": [],
            "views": {
                "json": json.dumps(result, ensure_ascii=False, indent=2),
                "blocks": "",
                "html": "",
                "markdown": "",
            },
            "download_url": f"api/download/{request_id}",
            "error": result.get("error") or record.get("error"),
        }
    return build_playground_response_payload(
        request_id=request_id,
        record=record,
        result=result,
        image_url_prefix=f"api/images/{request_id}",
    )


async def _read_playground_input(
    compat: DatalabCompatService,
    *,
    file: UploadFile | None,
    file_alias: UploadFile | None,
    file_url: str | None,
) -> tuple[bytes, str, str]:
    upload = file or file_alias
    if upload is not None:
        payload = await upload.read()
        if not payload:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")
        if len(payload) > _max_playground_upload_bytes():
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file upload is too large")
        return payload, Path(upload.filename or "document.bin").name, "upload"

    cleaned_url = str(file_url or "").strip()
    if not cleaned_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file or file_url is required")
    try:
        resolved = await run_in_threadpool(compat.resolve_input_file_url, cleaned_url)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    if not resolved.content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")
    if len(resolved.content) > _max_playground_upload_bytes():
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="file upload is too large")
    return resolved.content, resolved.file_name, resolved.source


def _normalize_mode(mode: str) -> str:
    try:
        return normalize_marker_mode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


def _normalize_page_range(page_range: str | None) -> str | None:
    cleaned = str(page_range or "").strip()
    if not cleaned:
        return None
    try:
        parse_page_range(cleaned)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return cleaned


def _normalize_max_pages(max_pages: int | None) -> int | None:
    if max_pages is None:
        return None
    if max_pages <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_pages must be greater than zero")
    return max_pages


def _extras_payload(**flags: bool) -> list[str]:
    return [name for name, enabled in flags.items() if enabled]


def _start_playground_worker(compat: DatalabCompatService, request_id: str, process_kwargs: dict[str, Any]) -> None:
    worker = threading.Thread(
        target=compat.process_marker_request,
        args=(request_id,),
        kwargs=process_kwargs,
        name=f"army-ocr-playground-{request_id[:8]}",
        daemon=True,
    )
    worker.start()


def _max_playground_upload_bytes() -> int:
    try:
        max_mb = int(runtime_config_value("playground_max_upload_mb", 512, get_settings()))
    except (TypeError, ValueError):
        return DEFAULT_MAX_PLAYGROUND_UPLOAD_BYTES
    return max(max_mb, 1) * 1024 * 1024


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
        "playground": {
            "label": "OCR 실행",
            "url": prefixes["playground"],
            "kind": "html",
        },
        "docs": {
            "label": "문서",
            "url": f"{prefixes['playground']}/docs",
            "kind": "html",
        },
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
        "openapi": {
            "label": "OpenAPI 원본",
            "url": f"{prefixes['api']}/openapi.json",
            "kind": "json",
        },
        "api_guide": {
            "label": "API 사용법",
            "url": f"{prefixes['playground']}/api-guide",
            "kind": "html",
        },
        "api_guide_markdown": {
            "label": "OCR 사용법 Markdown",
            "url": f"{prefixes['playground']}/api-guide.md",
            "kind": "markdown",
        },
        "ocr_health": {
            "label": "OCR 상태",
            "url": f"{prefixes['api']}/health",
            "kind": "json",
        },
        "admin": {
            "label": "관리자",
            "url": f"{prefixes['playground']}/admin",
            "kind": "html",
        },
    }


async def _read_json_object(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid JSON body") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON object is required")
    return payload


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
