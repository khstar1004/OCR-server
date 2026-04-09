from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.web.demo_service import DemoMessage, DemoService, DemoServiceError

router = APIRouter(tags=["demo"])
service = DemoService()

_ALLOWED_VIEWS = {"blocks", "json", "html", "markdown", "render"}

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TEMPLATE_ROOT = _REPO_ROOT / "templates"
_STATIC_ROOT = _REPO_ROOT / "static"
templates = Jinja2Templates(directory=str(_TEMPLATE_ROOT))


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


def _preview_page_query_value(detail: object | None) -> int | None:
    if detail is None:
        return None
    preview_page_id = getattr(detail, "preview_page_id", None)
    page_id = getattr(detail, "page_id", None)
    if preview_page_id in (None, page_id):
        return None
    return int(preview_page_id)


def _build_view_urls(path: str, *, page_id: int | None = None, **query: str | int | None) -> dict[str, str]:
    params = {key: value for key, value in query.items() if value not in (None, "")}
    if page_id is not None:
        params["page_id"] = page_id
    return {
        "blocks": _build_url(path, **params, view="blocks"),
        "json": _build_url(path, **params, view="json"),
        "html": _build_url(path, **params, view="html"),
        "markdown": _build_url(path, **params, view="markdown"),
        "render": _build_url(path, **params, view="render"),
    }


@router.get("/demo")
def get_demo_root() -> RedirectResponse:
    return RedirectResponse(url="/demo/jobs", status_code=302)


def _redirect_jobs_response(*, flash: DemoMessage, view: str, job_id: str | None = None) -> RedirectResponse:
    redirect_url = _build_url(
        "/demo/jobs",
        job_id=job_id,
        flash=flash.text,
        level=flash.level,
        view=_normalize_view(view),
    )
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/static/{asset_path:path}", name="demo_static")
def get_demo_static(asset_path: str) -> FileResponse:
    target = (_STATIC_ROOT / asset_path).resolve()
    static_root = _STATIC_ROOT.resolve()
    if static_root not in target.parents and target != static_root:
        raise HTTPException(status_code=404, detail="asset not found")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(target)


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
        "page_title": "Operator Demo",
        "flash": _safe_flash(level, flash),
        "selected_view": selected_view,
        "launch_source_dir": str(service.settings.input_root),
        "jobs_content_target": "#jobs-content",
        "article_view_urls": (
            _build_view_urls(
                "/demo/jobs",
                job_id=article_detail.job_key,
                article_id=article_detail.article_id,
                page_id=preview_page_id,
            )
            if article_detail is not None
            else _build_view_urls("/demo/jobs", job_id=job_id, article_id=article_id, page_id=page_id)
        ),
        "article_back_url": (
            _build_url(
                "/demo/jobs",
                job_id=article_detail.job_key,
                article_id=article_detail.article_id,
                view=selected_view,
                page_id=preview_page_id,
            )
            if article_detail is not None
            else _build_url("/demo/jobs", job_id=job_id, article_id=article_id, view=selected_view, page_id=page_id)
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
        "page_title": f"Article {detail.article_id}",
        "flash": _safe_flash(level, flash),
        "selected_view": selected_view,
        "jobs_content_target": None,
        "article_view_urls": _build_view_urls(
            f"/demo/articles/{detail.article_id}",
            page_id=preview_page_id,
        ),
        "article_back_url": _build_url(
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
    job_id: str,
    view: str | None = Query(default="render"),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        flash = service.delete_job(db, job_id)
    except DemoServiceError as exc:
        flash = DemoMessage(level="error", text=exc.message)
    query = urlencode({"flash": flash.text, "level": flash.level, "view": _normalize_view(view)})
    return RedirectResponse(url=f"/demo/jobs?{query}", status_code=303)


@router.post("/demo/jobs/start-dir")
async def start_demo_job_from_dir(
    source_dir: str = Form(default=""),
    callback_url: str = Form(default=""),
    view: str = Form(default="render"),
    pdf_files: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        uploads: list[tuple[str, bytes]] = []
        for upload in pdf_files or []:
            if not upload.filename:
                continue
            uploads.append((upload.filename, await upload.read()))
        if uploads:
            job = await service.queue_uploaded_pdf_batch_job(
                db,
                files=uploads,
                callback_url=callback_url,
            )
        else:
            job = await service.queue_source_dir_job(
                db,
                source_dir=source_dir,
                callback_url=callback_url,
            )
        flash = DemoMessage(level="success", text=f"작업을 큐에 넣었습니다: {job.job_key}")
        return _redirect_jobs_response(flash=flash, view=view, job_id=job.job_key)
    except DemoServiceError as exc:
        return _redirect_jobs_response(flash=DemoMessage(level="error", text=exc.message), view=view)


@router.post("/demo/jobs/start-file")
async def start_demo_job_from_file(
    pdf_path: str = Form(default=""),
    callback_url: str = Form(default=""),
    view: str = Form(default="render"),
    pdf_file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    try:
        if pdf_file is not None and pdf_file.filename:
            job = await service.queue_uploaded_pdf_job(
                db,
                filename=pdf_file.filename,
                content=await pdf_file.read(),
                callback_url=callback_url,
            )
        else:
            job = await service.queue_single_pdf_job(
                db,
                pdf_path=pdf_path,
                callback_url=callback_url,
            )
        flash = DemoMessage(level="success", text=f"단일 PDF 작업을 큐에 넣었습니다: {job.job_key}")
        return _redirect_jobs_response(flash=flash, view=view, job_id=job.job_key)
    except DemoServiceError as exc:
        return _redirect_jobs_response(flash=DemoMessage(level="error", text=exc.message), view=view)


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
        redirect_url = _build_url(
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
            "/demo/jobs",
            job_id=detail.job_key,
            article_id=detail.article_id,
            page_id=preview_page_id,
        )
        if context_name == "jobs"
        else _build_view_urls(
            f"/demo/articles/{detail.article_id}",
            page_id=preview_page_id,
        )
    )

    context = {
        "request": request,
        "page_title": f"Article {detail.article_id}",
        "flash": flash,
        "selected_view": selected_view,
        "jobs_content_target": "#jobs-content" if context_name == "jobs" else None,
        "article_view_urls": article_view_urls,
        "article_back_url": _build_url(
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
