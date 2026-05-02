from __future__ import annotations

import base64
import binascii
import copy
import io
import json
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.domain.types import SUPPORTED_BLOCK_LABELS, normalize_block_label_value
from app.services.datalab_compat import DatalabCompatService, normalize_marker_mode, parse_page_range, utcnow_iso
from app.services.playground_export import (
    build_playground_export_zip,
    build_playground_partial_response_payload,
    build_playground_response_payload,
    collect_playground_assets,
    find_playground_asset,
    read_asset_bytes,
    render_playground_views,
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
MAX_PLAYGROUND_MANUAL_IMAGE_BYTES = 12 * 1024 * 1024
PLAYGROUND_EDIT_METADATA_KEY = "playground_edit"
PLAYGROUND_DEFAULT_PAGE_RANGE = ""
PLAYGROUND_MODE_DPI_CAPS = {
    "fast": 180,
    "balanced": 240,
}


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
    html = html.replace(
        'href="admin" target="_blank" rel="noopener" data-resource-link="admin"',
        f'href="{links["admin"]["url"]}" target="_blank" rel="noopener" data-resource-link="admin"',
    )
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
        "service": "Army-OCR-playground",
        "ocr_service_ready": isinstance(compat, DatalabCompatService),
        "ocr_backend": "army_ocr",
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
        "service": "Army-OCR",
        "playground": True,
        "ocr_backend": "army_ocr",
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
            "tables": True,
            "layout_block_labels": list(SUPPORTED_BLOCK_LABELS),
            "markdown_with_images": True,
            "html_with_images": True,
            "zip_export_with_images": True,
            "runtime_settings": True,
            "max_concurrent_ocr_requests": max(
                int(runtime_config_value("ocr_max_concurrent_requests", settings.ocr_max_concurrent_requests, settings) or 1),
                1,
            ),
            "default_max_pages": _playground_default_max_pages_cap(),
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


@router.get("/api/admin/overview")
def get_admin_overview(request: Request) -> dict[str, Any]:
    admin = require_admin_user(request)
    settings = get_settings()
    store = get_auth_store(settings)
    runtime_payload = get_runtime_config_store(settings).snapshot()
    return {
        "success": True,
        "user": admin,
        "auth": store.snapshot(),
        "runtime": _runtime_overview(runtime_payload),
        "settings": runtime_payload,
        "health": get_playground_health(request),
        "capabilities": get_playground_capabilities(request),
    }


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
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return {"success": True, "user": user}


@router.post("/api/admin/users/{user_id}/suspend")
def suspend_admin_user(request: Request, user_id: str) -> dict[str, Any]:
    admin = require_admin_user(request)
    try:
        user = get_auth_store(get_settings()).suspend_user(user_id, suspended_by=str(admin.get("username") or "admin"))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found") from None
    except PermissionError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    return {"success": True, "user": user}


@router.post("/api/admin/users/{user_id}/activate")
def activate_admin_user(request: Request, user_id: str) -> dict[str, Any]:
    admin = require_admin_user(request)
    try:
        user = get_auth_store(get_settings()).activate_user(user_id, activated_by=str(admin.get("username") or "admin"))
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
    page_range: str | None = Form(default=PLAYGROUND_DEFAULT_PAGE_RANGE),
    max_pages: int | None = Form(default=None),
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
    page_range: str | None = Form(default=PLAYGROUND_DEFAULT_PAGE_RANGE),
    max_pages: int | None = Form(default=None),
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


@router.put("/api/convert/{request_id}/blocks/{page_index}/{block_index}")
async def update_playground_result_block(
    request: Request,
    request_id: str,
    page_index: int,
    block_index: int,
) -> dict[str, Any]:
    payload = await _read_json_object(request)
    compat = _get_compat(request)
    record, result = _get_record_and_result(compat, request_id)
    updated_result, record_changes = _apply_playground_block_edit(
        compat=compat,
        request_id=request_id,
        record=record,
        result=result,
        page_index=page_index,
        block_index=block_index,
        payload=payload,
    )
    compat._update_request_record(
        request_id,
        status=str(updated_result.get("status") or record.get("status") or "complete"),
        result=updated_result,
        error=updated_result.get("error"),
        **record_changes,
    )
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
        headers={"Content-Disposition": f'attachment; filename="Army-OCR-result-{request_id}.zip"'},
    )


def _apply_playground_block_edit(
    *,
    compat: DatalabCompatService,
    request_id: str,
    record: dict[str, Any],
    result: dict[str, Any],
    page_index: int,
    block_index: int,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if page_index < 0 or block_index < 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="page_index and block_index must be non-negative")

    updated = copy.deepcopy(result)
    json_payload = updated.get("json") if isinstance(updated.get("json"), dict) else None
    if json_payload is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="editable OCR JSON is not available")
    pages = json_payload.get("pages")
    if not isinstance(pages, list) or page_index >= len(pages) or not isinstance(pages[page_index], dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page not found")
    page = pages[page_index]
    blocks = page.get("blocks")
    if not isinstance(blocks, list) or block_index >= len(blocks) or not isinstance(blocks[block_index], dict):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="block not found")

    block = blocks[block_index]
    updated_at = utcnow_iso()
    original_label = str(block.get("label") or "text")
    label = _normalize_playground_edit_label(payload.get("label", original_label))
    text = _normalize_playground_edit_text(payload.get("text", block.get("text") or ""))
    table_rows = _normalize_playground_table_rows(payload.get("table_rows")) if "table_rows" in payload else None

    metadata = copy.deepcopy(block.get("metadata")) if isinstance(block.get("metadata"), dict) else {}
    edit_metadata = copy.deepcopy(metadata.get(PLAYGROUND_EDIT_METADATA_KEY)) if isinstance(metadata.get(PLAYGROUND_EDIT_METADATA_KEY), dict) else {}
    edit_metadata.setdefault("original_label", original_label)
    edit_metadata["edited"] = True
    edit_metadata["updated_at"] = updated_at

    block["label"] = label
    block["text"] = text
    if label == "table":
        if table_rows is not None:
            metadata["table_rows"] = table_rows
            if not text.strip():
                block["text"] = _table_rows_to_tsv(table_rows)
    else:
        metadata.pop("table_rows", None)

    manual_image_paths = dict(record.get("manual_image_paths") or {}) if isinstance(record.get("manual_image_paths"), dict) else {}
    if payload.get("remove_manual_image") is True:
        previous_image = edit_metadata.get("manual_image")
        if isinstance(previous_image, dict):
            previous_name = Path(str(previous_image.get("name") or "")).name
            if previous_name:
                manual_image_paths.pop(previous_name, None)
        edit_metadata.pop("manual_image", None)

    image_payload = payload.get("image")
    if image_payload is not None:
        if not isinstance(image_payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image must be an object")
        if image_payload.get("data_url") or image_payload.get("data"):
            image_info = _save_playground_manual_image(
                compat=compat,
                request_id=request_id,
                page_index=page_index,
                block_index=block_index,
                image_payload=image_payload,
                updated_at=updated_at,
            )
            source_path = str(image_info.pop("source_path"))
            manual_image_paths[image_info["name"]] = source_path
            edit_metadata["manual_image"] = image_info

    metadata[PLAYGROUND_EDIT_METADATA_KEY] = edit_metadata
    block["metadata"] = metadata
    page["text"] = _page_text_from_blocks(blocks)

    updated["json"] = json_payload
    metadata_payload = copy.deepcopy(updated.get("metadata")) if isinstance(updated.get("metadata"), dict) else {}
    playground_edits = copy.deepcopy(metadata_payload.get("playground_edits")) if isinstance(metadata_payload.get("playground_edits"), dict) else {}
    playground_edits.update(
        {
            "updated_at": updated_at,
            "last_page_index": page_index,
            "last_block_index": block_index,
        }
    )
    metadata_payload["playground_edits"] = playground_edits
    updated["metadata"] = metadata_payload

    record_changes = {"manual_image_paths": manual_image_paths}
    _refresh_playground_result_outputs(record={**record, **record_changes}, result=updated)
    return updated, record_changes


def _normalize_playground_edit_label(value: Any) -> str:
    label = normalize_block_label_value(value)
    if label not in SUPPORTED_BLOCK_LABELS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unsupported block label")
    return label


def _normalize_playground_edit_text(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) > 1_000_000:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="block text is too large")
    return text


def _normalize_playground_table_rows(value: Any) -> list[list[str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="table_rows must be a list")
    rows: list[list[str]] = []
    for row in value[:500]:
        if not isinstance(row, list):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="table row must be a list")
        cells = [str(cell or "").strip() for cell in row[:80]]
        if any(cells):
            rows.append(cells)
    return rows


def _save_playground_manual_image(
    *,
    compat: DatalabCompatService,
    request_id: str,
    page_index: int,
    block_index: int,
    image_payload: dict[str, Any],
    updated_at: str,
) -> dict[str, Any]:
    raw_data = str(image_payload.get("data_url") or image_payload.get("data") or "")
    if "," in raw_data and raw_data.lower().startswith("data:"):
        header, encoded = raw_data.split(",", 1)
        if not header.lower().startswith("data:image/"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="image data URL is required")
    else:
        encoded = raw_data
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image data") from None
    if not content or len(content) > MAX_PLAYGROUND_MANUAL_IMAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="manual image is too large")

    try:
        with Image.open(io.BytesIO(content)) as image:
            image.load()
            width, height = image.size
            normalized = image.convert("RGBA" if image.mode in {"RGBA", "LA", "P"} else "RGB")
            buffer = io.BytesIO()
            normalized.save(buffer, format="PNG")
    except (UnidentifiedImageError, OSError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid image file") from None

    image_bytes = buffer.getvalue()
    if len(image_bytes) > MAX_PLAYGROUND_MANUAL_IMAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="manual image is too large")
    manual_dir = _playground_manual_image_dir(compat, request_id)
    manual_dir.mkdir(parents=True, exist_ok=True)
    image_name = f"manual-page-{page_index + 1:04d}-block-{block_index + 1:04d}.png"
    image_path = manual_dir / image_name
    image_path.write_bytes(image_bytes)
    return {
        "name": image_name,
        "media_type": "image/png",
        "width": width,
        "height": height,
        "size_bytes": len(image_bytes),
        "updated_at": updated_at,
        "source_path": str(image_path),
    }


def _playground_manual_image_dir(compat: DatalabCompatService, request_id: str) -> Path:
    request_root = (compat.requests_dir / Path(request_id).name).resolve()
    try:
        request_root.relative_to(compat.requests_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid request id") from None
    return request_root / "manual_images"


def _refresh_playground_result_outputs(*, record: dict[str, Any], result: dict[str, Any]) -> None:
    json_payload = result.get("json") if isinstance(result.get("json"), dict) else {}
    pages = json_payload.get("pages") if isinstance(json_payload.get("pages"), list) else []
    source_file = str(result.get("metadata", {}).get("source_file") or json_payload.get("file_name") or "")
    assets = collect_playground_assets(record, result)
    views = render_playground_views(result, assets, image_ref_prefix="images", relative_images=True)
    chunks = _chunks_from_pages(pages, source_file)

    result["markdown"] = views["markdown"]
    result["html"] = views["html"]
    result["chunks"] = chunks
    formats = _playground_result_output_formats(result)
    result["result"] = _playground_result_payload_for_formats(
        formats=formats,
        json_payload=json_payload,
        markdown=views["markdown"],
        html=views["html"],
        chunks=chunks,
    )


def _playground_result_output_formats(result: dict[str, Any]) -> list[str]:
    raw_formats = result.get("output_formats")
    if isinstance(raw_formats, list):
        formats = [str(item).strip().lower() for item in raw_formats]
    else:
        formats = [item.strip().lower() for item in str(result.get("output_format") or "").split(",")]
    formats = [item for item in formats if item in {"json", "markdown", "html", "chunks"}]
    return formats or ["json", "markdown", "html", "chunks"]


def _playground_result_payload_for_formats(
    *,
    formats: list[str],
    json_payload: dict[str, Any],
    markdown: str,
    html: str,
    chunks: list[dict[str, Any]],
) -> Any:
    outputs = {
        "json": json_payload,
        "markdown": markdown,
        "html": html,
        "chunks": chunks,
    }
    if len(formats) == 1:
        return outputs[formats[0]]
    return {name: outputs[name] for name in formats}


def _chunks_from_pages(pages: list[Any], source_file: str) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_number = page.get("page_number")
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            chunk = copy.deepcopy(block)
            chunk["page_number"] = page_number
            if source_file:
                chunk["file_name"] = source_file
            chunk["markdown"] = _block_markdown_for_chunk(block)
            chunks.append(chunk)
    return chunks


def _block_markdown_for_chunk(block: dict[str, Any]) -> str:
    text = str(block.get("text") or "").strip()
    label = str(block.get("label") or "").strip().lower()
    if not text:
        return ""
    if label in {"title", "sectionheader", "section_header", "heading"}:
        return f"## {text}"
    if label == "table":
        rows = block.get("metadata", {}).get("table_rows") if isinstance(block.get("metadata"), dict) else None
        if isinstance(rows, list) and rows:
            return _markdown_table_from_rows(rows)
        return f"**Table**\n\n{text}"
    if label == "code_block":
        return f"```text\n{text}\n```"
    if label in {"equation_block", "chemical_block"}:
        return f"$$\n{text}\n$$"
    if label in {"form", "table_of_contents", "bibliography", "complex_block", "blank_page"}:
        return f"**{label.replace('_', ' ').title()}**\n\n{text}"
    if label in {"caption", "footnote", "pageheader", "pagefooter", "header", "footer"}:
        return f"*{text}*"
    return text


def _markdown_table_from_rows(rows: list[Any]) -> str:
    normalized = [
        [str(cell or "").replace("|", "\\|").strip() for cell in row]
        for row in rows
        if isinstance(row, list) and any(str(cell or "").strip() for cell in row)
    ]
    if not normalized:
        return ""
    column_count = max(len(row) for row in normalized)
    for row in normalized:
        while len(row) < column_count:
            row.append("")
    header = normalized[0]
    body = normalized[1:]
    return "\n".join(
        [
            f"| {' | '.join(header)} |",
            f"| {' | '.join(['---'] * column_count)} |",
            *[f"| {' | '.join(row)} |" for row in body],
        ]
    )


def _table_rows_to_tsv(rows: list[list[str]]) -> str:
    return "\n".join("\t".join(cell for cell in row) for row in rows)


def _page_text_from_blocks(blocks: list[Any]) -> str:
    chunks = [
        str(block.get("text") or "").strip()
        for block in blocks
        if isinstance(block, dict) and str(block.get("text") or "").strip()
    ]
    return "\n".join(chunks)


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
    normalized_max_pages = _normalize_playground_max_pages(max_pages, normalized_page_range)
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
        "dpi": _playground_pdf_dpi(normalized_mode),
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


def _normalize_playground_max_pages(max_pages: int | None, page_range: str | None) -> int | None:
    if max_pages is not None:
        return _normalize_max_pages(max_pages)
    if page_range:
        return None
    return _playground_default_max_pages_cap()


def _playground_default_max_pages_cap() -> int | None:
    settings = get_settings()
    try:
        value = int(runtime_config_value("playground_default_max_pages", settings.playground_default_max_pages, settings) or 0)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return value


def _playground_pdf_dpi(mode: str) -> int:
    settings = get_settings()
    configured = int(runtime_config_value("pdf_render_dpi", settings.pdf_render_dpi, settings) or settings.pdf_render_dpi)
    if configured <= 0:
        configured = settings.pdf_render_dpi
    cap = PLAYGROUND_MODE_DPI_CAPS.get(mode)
    if cap is None:
        return configured
    return min(configured, cap)


def _extras_payload(**flags: bool) -> list[str]:
    return [name for name, enabled in flags.items() if enabled]


def _start_playground_worker(compat: DatalabCompatService, request_id: str, process_kwargs: dict[str, Any]) -> None:
    worker = threading.Thread(
        target=compat.process_marker_request,
        args=(request_id,),
        kwargs=process_kwargs,
        name=f"Army-OCR-playground-{request_id[:8]}",
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
