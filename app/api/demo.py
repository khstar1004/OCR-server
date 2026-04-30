from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context
from sqlalchemy.orm import Session

from app.core.config import normalize_root_path
from app.db.session import get_db
from app.services.job_options import normalize_job_ocr_options
from app.web.demo_service import DemoMessage, DemoService, DemoServiceError

router = APIRouter(tags=["demo"])
service = DemoService()

_ALLOWED_VIEWS = {"blocks", "json", "html", "markdown", "render", "compare"}

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_ROOT = _REPO_ROOT / "templates"
_STATIC_ROOT = _REPO_ROOT / "static"
templates = Jinja2Templates(directory=str(_TEMPLATE_ROOT))

_UI_LABELS = {
    "add_block_ids": "블록 번호",
    "balanced": "보통",
    "blocked": "막힘",
    "blocks": "블록",
    "caption": "그림 설명",
    "chunks": "조각",
    "cluster": "기사 묶기",
    "compare": "비교",
    "completed": "완료",
    "completed_with_errors": "일부 실패",
    "correction": "교정",
    "crop": "그림 자르기",
    "delivered": "보냄",
    "delivery": "전송",
    "duplicate_hash": "같은 파일",
    "error": "오류",
    "failed": "실패",
    "fallback": "보조 읽기",
    "fallback_ocr": "보조 읽기",
    "fast": "빠르게",
    "html": "웹보기",
    "include_markdown_in_chunks": "조각에 글 포함",
    "info": "안내",
    "json": "원본값",
    "low_confidence": "낮은 신뢰도",
    "low_korean_ratio": "한글 비율 낮음",
    "low_text": "글자 적음",
    "markdown": "글 원본",
    "missing": "없음",
    "not_configured": "미설정",
    "ocr_fallback": "보조 읽기",
    "ocr_structure": "구조 읽기",
    "ocr_vl": "글자 읽기",
    "page": "쪽",
    "paginate": "쪽 나누기",
    "parsed": "완료",
    "pending": "대기",
    "persist": "저장",
    "picture": "그림",
    "queued": "대기",
    "ready": "준비됨",
    "render": "보기",
    "reviewed": "확인됨",
    "running": "처리중",
    "scan": "파일 찾기",
    "sectionheader": "제목",
    "skipped": "건너뜀",
    "skip_cache": "임시 저장 무시",
    "structure": "구조",
    "success": "성공",
    "table": "표",
    "text": "글",
    "title": "제목",
    "unreviewed": "미확인",
    "warning": "확인 필요",
}

_UI_TEXT_REPLACEMENTS = {
    "article": "기사",
    "articles": "기사",
    "avg": "평균",
    "batch": "묶음",
    "blocked": "막힘",
    "calling remote OCR service": "글자 읽는 중",
    "connection refused": "연결 실패",
    "completed": "완료",
    "delivered": "보냄",
    "duplicate_hash": "같은 파일",
    "failed": "실패",
    "images": "그림",
    "page": "쪽",
    "pages": "쪽",
    "parsed": "처리됨",
    "pending": "대기",
    "progress": "진행률",
    "ready": "준비됨",
    "review": "확인",
    "running": "처리중",
    "score": "점수",
    "selected": "선택됨",
    "status": "상태",
    "success": "성공",
    "total": "전체",
    "waiting": "대기중",
    "warning": "확인 필요",
}


def _ui_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    return _UI_LABELS.get(text.lower(), text)


def _ui_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return "-"
    normalized = _UI_LABELS.get(text.lower())
    if normalized is not None:
        return normalized
    for source, target in sorted(_UI_TEXT_REPLACEMENTS.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, target).replace(source.capitalize(), target)
    return text


def _request_root_path(request: Request | None) -> str:
    if request is None:
        return ""
    return normalize_root_path(str(request.scope.get("root_path") or ""))


def _with_root_path(request: Request | None, path: str) -> str:
    if not path.startswith("/") or path.startswith("//"):
        return path
    root_path = _request_root_path(request)
    if not root_path or path == root_path or path.startswith(f"{root_path}/"):
        return path
    return f"{root_path}{path}"


@pass_context
def _template_url_path(context: dict[str, object], path: object) -> str:
    return _with_root_path(context.get("request"), str(path))


def _static_asset_version(asset_path: str) -> str:
    normalized_asset_path = asset_path.replace("\\", "/").lstrip("/")
    target = (_STATIC_ROOT / normalized_asset_path).resolve()
    static_root = _STATIC_ROOT.resolve()
    if static_root not in target.parents and target != static_root:
        return "missing"
    try:
        stat = target.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns:x}-{stat.st_size:x}"


@pass_context
def _template_static_asset(context: dict[str, object], asset_path: object) -> str:
    normalized_asset_path = str(asset_path).replace("\\", "/").lstrip("/")
    encoded_asset_path = quote(normalized_asset_path, safe="/")
    url = _with_root_path(context.get("request"), f"/static/{encoded_asset_path}")
    return f"{url}?{urlencode({'v': _static_asset_version(normalized_asset_path)})}"


templates.env.globals["url_path"] = _template_url_path
templates.env.globals["static_asset"] = _template_static_asset
templates.env.filters["ui_label"] = _ui_label
templates.env.filters["ui_text"] = _ui_text


def _is_hx_request(request: Request) -> bool:
    return request.headers.get("HX-Request", "").lower() == "true"


def _safe_flash(level: str | None, text: str | None) -> DemoMessage | None:
    if not text:
        return None
    normalized = (level or "info").strip().lower()
    if normalized not in {"info", "success", "warning", "error"}:
        normalized = "info"
    return DemoMessage(level=normalized, text=text)


def _normalize_view(view: str | None) -> str:
    normalized = (view or "render").strip().lower()
    return normalized if normalized in _ALLOWED_VIEWS else "render"


def _build_url(path: str, **query: str | int | None) -> str:
    encoded = urlencode({key: value for key, value in query.items() if value not in (None, "")})
    return f"{path}?{encoded}" if encoded else path


def _build_request_url(request: Request, path: str, **query: str | int | None) -> str:
    return _with_root_path(request, _build_url(path, **query))


def _preview_page_query_value(detail: object | None) -> int | None:
    if detail is None:
        return None
    preview_page_id = getattr(detail, "preview_page_id", None)
    page_id = getattr(detail, "page_id", None)
    if preview_page_id in (None, page_id):
        return None
    return int(preview_page_id)


def _build_view_urls(
    request: Request,
    path: str,
    *,
    page_id: int | None = None,
    **query: str | int | None,
) -> dict[str, str]:
    params = {key: value for key, value in query.items() if value not in (None, "")}
    if page_id is not None:
        params["page_id"] = page_id
    return {
        "blocks": _build_request_url(request, path, **params, view="blocks"),
        "json": _build_request_url(request, path, **params, view="json"),
        "html": _build_request_url(request, path, **params, view="html"),
        "markdown": _build_request_url(request, path, **params, view="markdown"),
        "render": _build_request_url(request, path, **params, view="render"),
        "compare": _build_request_url(request, path, **params, view="compare"),
    }


@router.get("/demo")
def get_demo_root(request: Request) -> RedirectResponse:
    return RedirectResponse(url=_with_root_path(request, "/demo/jobs"), status_code=302)


def _redirect_jobs_response(
    request: Request,
    *,
    flash: DemoMessage,
    view: str,
    job_id: str | None = None,
) -> RedirectResponse:
    redirect_url = _build_request_url(
        request,
        "/demo/jobs",
        job_id=job_id,
        flash=flash.text,
        level=flash.level,
        view=_normalize_view(view),
    )
    return RedirectResponse(url=redirect_url, status_code=303)


def _demo_ocr_options(
    *,
    ocr_mode: str = "balanced",
    page_range: str | None = None,
    max_pages: int | None = None,
    output_format: str = "markdown",
    paginate: bool = False,
    add_block_ids: bool = False,
    include_markdown_in_chunks: bool = False,
    skip_cache: bool = False,
) -> dict[str, object]:
    return normalize_job_ocr_options(
        {
            "ocr_mode": ocr_mode,
            "page_range": page_range,
            "max_pages": max_pages,
            "output_format": output_format,
            "paginate": paginate,
            "add_block_ids": add_block_ids,
            "include_markdown_in_chunks": include_markdown_in_chunks,
            "skip_cache": skip_cache,
        }
    )


@router.get("/static/{asset_path:path}", name="demo_static")
def get_demo_static(asset_path: str) -> FileResponse:
    target = (_STATIC_ROOT / asset_path).resolve()
    static_root = _STATIC_ROOT.resolve()
    if static_root not in target.parents and target != static_root:
        raise HTTPException(status_code=404, detail="asset not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(
        target,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/demo/jobs", response_class=HTMLResponse)
def get_demo_jobs(
    request: Request,
    job_id: str | None = Query(default=None),
    article_id: int | None = Query(default=None),
    page_id: int | None = Query(default=None),
    view: str | None = Query(default="render"),
    flash: str | None = Query(default=None),
    level: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    page = service.build_jobs_page(
        db,
        selected_job_key=job_id,
        selected_article_id=article_id,
        selected_preview_page_id=page_id,
    )
    selected_view = _normalize_view(view)
    article_detail = page.get("article_detail")
    preview_page_id = _preview_page_query_value(article_detail)
    context = {
        "request": request,
        "page_title": "작업 화면",
        "flash": _safe_flash(level, flash),
        "selected_view": selected_view,
        "launch_source_dir": str(service.settings.input_root),
        "jobs_content_target": "#jobs-content",
        "article_view_urls": (
            _build_view_urls(
                request,
                "/demo/jobs",
                job_id=article_detail.job_key,
                article_id=article_detail.article_id,
                page_id=preview_page_id,
            )
            if article_detail is not None
            else _build_view_urls(request, "/demo/jobs", job_id=job_id, article_id=article_id, page_id=page_id)
        ),
        "article_back_url": (
            _build_request_url(
                request,
                "/demo/jobs",
                job_id=article_detail.job_key,
                article_id=article_detail.article_id,
                view=selected_view,
                page_id=preview_page_id,
            )
            if article_detail is not None
            else _build_request_url(request, "/demo/jobs", job_id=job_id, article_id=article_id, view=selected_view, page_id=page_id)
        ),
        **page,
    }
    template_name = "demo/_jobs_content.html" if _is_hx_request(request) else "demo/jobs.html"
    return templates.TemplateResponse(request=request, name=template_name, context=context)


@router.get("/demo/articles/{article_id}", response_class=HTMLResponse)
def get_demo_article(
    request: Request,
    article_id: int,
    page_id: int | None = Query(default=None),
    view: str | None = Query(default="render"),
    flash: str | None = Query(default=None),
    level: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    detail = service.get_article_detail(db, article_id, preview_page_id=page_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="article not found")
    selected_view = _normalize_view(view)
    preview_page_id = _preview_page_query_value(detail)
    context = {
        "request": request,
        "page_title": f"기사 {detail.article_id}",
        "flash": _safe_flash(level, flash),
        "selected_view": selected_view,
        "jobs_content_target": None,
        "article_view_urls": _build_view_urls(
            request,
            f"/demo/articles/{detail.article_id}",
            page_id=preview_page_id,
        ),
        "article_back_url": _build_request_url(
            request,
            "/demo/jobs",
            job_id=detail.job_key,
            article_id=detail.article_id,
            view=selected_view,
            page_id=preview_page_id,
        ),
        "article_detail": detail,
    }
    template_name = "demo/_article_detail.html" if _is_hx_request(request) else "demo/article_page.html"
    return templates.TemplateResponse(request=request, name=template_name, context=context)


@router.post("/api/articles/{article_id}/reprocess", response_class=HTMLResponse, response_model=None)
async def reprocess_article(request: Request, article_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        flash = await service.queue_reprocess(db, article_id)
        status_code = 200
    except DemoServiceError as exc:
        flash = DemoMessage(level="error", text=exc.message)
        status_code = exc.status_code
    return _render_action_response(request, article_id, flash, db, status_code=status_code)


@router.post("/api/articles/{article_id}/redeliver", response_class=HTMLResponse, response_model=None)
async def redeliver_article(request: Request, article_id: int, db: Session = Depends(get_db)) -> Response:
    try:
        flash = await service.redeliver_article(db, article_id)
        status_code = 200
    except DemoServiceError as exc:
        flash = DemoMessage(level="error", text=exc.message)
        status_code = exc.status_code
    return _render_action_response(request, article_id, flash, db, status_code=status_code)


@router.post("/demo/jobs/{job_id}/delete")
def delete_demo_job(
    request: Request,
    job_id: str,
    view: str | None = Query(default="render"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        flash = service.delete_job(db, job_id)
    except DemoServiceError as exc:
        flash = DemoMessage(level="error", text=exc.message)
    query = urlencode({"flash": flash.text, "level": flash.level, "view": _normalize_view(view)})
    return RedirectResponse(url=_with_root_path(request, f"/demo/jobs?{query}"), status_code=303)


@router.post("/demo/jobs/{job_id}/deliver")
def deliver_demo_job(
    request: Request,
    job_id: str,
    view: str | None = Query(default="render"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        flash = service.deliver_job(db, job_id)
    except DemoServiceError as exc:
        flash = DemoMessage(level="error", text=exc.message)
    query = urlencode({"job_id": job_id, "flash": flash.text, "level": flash.level, "view": _normalize_view(view)})
    return RedirectResponse(url=_with_root_path(request, f"/demo/jobs?{query}"), status_code=303)


@router.post("/demo/jobs/refresh-archive")
def refresh_archived_jobs(
    request: Request,
    view: str | None = Query(default="render"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    imported = service.sync_archived_results(db, force=True)
    if imported:
        flash = DemoMessage(level="success", text=f"과거 OCR 결과 {imported}개를 복구했습니다.")
    else:
        flash = DemoMessage(level="info", text="새로 복구할 과거 OCR 결과가 없습니다.")
    query = urlencode({"flash": flash.text, "level": flash.level, "view": _normalize_view(view)})
    return RedirectResponse(url=_with_root_path(request, f"/demo/jobs?{query}"), status_code=303)


@router.post("/demo/jobs/start-dir")
async def start_demo_job_from_dir(
    request: Request,
    source_dir: str = Form(default=""),
    view: str = Form(default="render"),
    ocr_mode: str = Form(default="balanced"),
    page_range: str | None = Form(default=None),
    max_pages: int | None = Form(default=None),
    output_format: str = Form(default="markdown"),
    paginate: bool = Form(default=False),
    add_block_ids: bool = Form(default=False),
    include_markdown_in_chunks: bool = Form(default=False),
    skip_cache: bool = Form(default=False),
    pdf_files: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        ocr_options = _demo_ocr_options(
            ocr_mode=ocr_mode,
            page_range=page_range,
            max_pages=max_pages,
            output_format=output_format,
            paginate=paginate,
            add_block_ids=add_block_ids,
            include_markdown_in_chunks=include_markdown_in_chunks,
            skip_cache=skip_cache,
        )
        uploads: list[tuple[str, bytes]] = []
        for upload in pdf_files or []:
            if not upload.filename:
                continue
            uploads.append((upload.filename, await upload.read()))
        if uploads:
            job = await service.queue_uploaded_pdf_batch_job(
                db,
                files=uploads,
                ocr_options=ocr_options,
            )
        else:
            job = await service.queue_source_dir_job(
                db,
                source_dir=source_dir,
                ocr_options=ocr_options,
            )
        flash = DemoMessage(level="success", text=f"작업을 큐에 넣었습니다: {job.job_key}")
        return _redirect_jobs_response(request, flash=flash, view=view, job_id=job.job_key)
    except DemoServiceError as exc:
        return _redirect_jobs_response(request, flash=DemoMessage(level="error", text=exc.message), view=view)
    except ValueError as exc:
        return _redirect_jobs_response(request, flash=DemoMessage(level="error", text=str(exc)), view=view)


@router.post("/demo/jobs/start-file")
async def start_demo_job_from_file(
    request: Request,
    pdf_path: str = Form(default=""),
    view: str = Form(default="render"),
    ocr_mode: str = Form(default="balanced"),
    page_range: str | None = Form(default=None),
    max_pages: int | None = Form(default=None),
    output_format: str = Form(default="markdown"),
    paginate: bool = Form(default=False),
    add_block_ids: bool = Form(default=False),
    include_markdown_in_chunks: bool = Form(default=False),
    skip_cache: bool = Form(default=False),
    pdf_file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        ocr_options = _demo_ocr_options(
            ocr_mode=ocr_mode,
            page_range=page_range,
            max_pages=max_pages,
            output_format=output_format,
            paginate=paginate,
            add_block_ids=add_block_ids,
            include_markdown_in_chunks=include_markdown_in_chunks,
            skip_cache=skip_cache,
        )
        if pdf_file is not None and pdf_file.filename:
            job = await service.queue_uploaded_pdf_job(
                db,
                filename=pdf_file.filename,
                content=await pdf_file.read(),
                ocr_options=ocr_options,
            )
        else:
            job = await service.queue_single_pdf_job(
                db,
                pdf_path=pdf_path,
                ocr_options=ocr_options,
            )
        flash = DemoMessage(level="success", text=f"단일 파일 작업을 큐에 넣었습니다: {job.job_key}")
        return _redirect_jobs_response(request, flash=flash, view=view, job_id=job.job_key)
    except DemoServiceError as exc:
        return _redirect_jobs_response(request, flash=DemoMessage(level="error", text=exc.message), view=view)
    except ValueError as exc:
        return _redirect_jobs_response(request, flash=DemoMessage(level="error", text=str(exc)), view=view)


def _render_action_response(
    request: Request,
    article_id: int,
    flash: DemoMessage,
    db: Session,
    *,
    status_code: int,
) -> Response:
    selected_view = _normalize_view(request.query_params.get("view"))
    page_id = request.query_params.get("page_id")
    context_name = request.query_params.get("context")
    if not _is_hx_request(request):
        redirect_url = _build_request_url(
            request,
            f"/demo/articles/{article_id}",
            flash=flash.text,
            level=flash.level,
            view=selected_view,
            page_id=page_id,
        )
        return RedirectResponse(url=redirect_url, status_code=303)

    detail = service.get_article_detail(
        db,
        article_id,
        preview_page_id=int(page_id) if page_id and page_id.isdigit() else None,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail="article not found")
    preview_page_id = _preview_page_query_value(detail)
    article_view_urls = (
        _build_view_urls(
            request,
            "/demo/jobs",
            job_id=detail.job_key,
            article_id=detail.article_id,
            page_id=preview_page_id,
        )
        if context_name == "jobs"
        else _build_view_urls(
            request,
            f"/demo/articles/{detail.article_id}",
            page_id=preview_page_id,
        )
    )

    context = {
        "request": request,
        "page_title": f"기사 {detail.article_id}",
        "flash": flash,
        "selected_view": selected_view,
        "jobs_content_target": "#jobs-content" if context_name == "jobs" else None,
        "article_view_urls": article_view_urls,
        "article_back_url": _build_request_url(
            request,
            "/demo/jobs",
            job_id=detail.job_key,
            article_id=detail.article_id,
            view=selected_view,
            page_id=preview_page_id,
        ),
        "article_detail": detail,
    }
    return templates.TemplateResponse(
        request=request,
        name="demo/_article_detail.html",
        context=context,
        status_code=status_code,
    )
