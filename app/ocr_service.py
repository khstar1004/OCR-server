from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, FastAPI, File, Form, HTTPException, Query, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from PIL import Image

from app.core.config import get_settings
from app.domain.types import PageLayout
from app.ocr.rendering import render_pdf_document
from app.services.artifacts import build_job_artifact_layout
from app.services.datalab_compat import DatalabCompatService
from app.services.datalab_defense import DefenseDataService
from app.services.ocr_engine import OCREngine

router = APIRouter(prefix="/api/v1", tags=["ocr-service"])
compat_router = APIRouter(tags=["datalab-compat"])


def _serialize_block(block: object) -> dict[str, Any]:
    return {
        "block_id": getattr(block, "block_id", ""),
        "page_number": getattr(block, "page_number", 0),
        "label": str(getattr(block, "label", "")).split(".")[-1].lower(),
        "bbox": list(getattr(block, "bbox", [])),
        "text": str(getattr(block, "text", "")),
        "confidence": float(getattr(block, "confidence", 0.0)),
        "metadata": dict(getattr(block, "metadata", {}) or {}),
    }


def _serialize_layout(layout: PageLayout) -> dict[str, Any]:
    return {
        "page_number": layout.page_number,
        "width": layout.width,
        "height": layout.height,
        "image_path": layout.image_path.name,
        "blocks": [_serialize_block(block) for block in layout.blocks],
        "raw_vl": layout.raw_vl,
        "raw_structure": layout.raw_structure,
        "raw_fallback_ocr": layout.raw_fallback_ocr,
    }


def _serialize_pdf_response(pages: list[dict[str, Any]], page_count: int, pdf_name: str) -> dict[str, Any]:
    return {"page_count": page_count, "pdf_name": pdf_name, "pages": pages}


def _read_image_size(image_path: Path) -> tuple[int, int]:
    with Image.open(image_path) as image:
        return image.width, image.height


def _get_ocr_engine(request: Request) -> OCREngine:
    return request.app.state.ocr_engine


def _get_compat_service(request: Request) -> DatalabCompatService:
    return request.app.state.datalab_compat


def _get_defense_service(request: Request) -> DefenseDataService:
    return request.app.state.datalab_defense


def _warmup_ocr_engine(engine: OCREngine) -> None:
    # Load the model at startup so the first OCR request does not pay the full model-load cost.
    config = engine._build_chandra_config()
    runner = engine._get_chandra_runner(config)
    runner._load_model()


def _pick_upload(file: UploadFile | None, file_alias: UploadFile | None) -> UploadFile:
    upload = file or file_alias
    if upload is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="file is required")
    return upload


def _create_async_request(
    compat: DatalabCompatService,
    kind: str,
    request: Request,
    route_name: str,
    meta: dict[str, Any] | None = None,
) -> str:
    return compat.create_request(
        kind,
        str(request.url_for(route_name, request_id="{request_id}")),
        meta=meta or {},
    )


@router.get("/health", response_class=JSONResponse)
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/ocr/image")
async def ocr_image(
    request: Request,
    file: UploadFile = File(...),
    page_number: int = Form(default=1),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
) -> dict[str, Any]:
    if page_number <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="page_number must be greater than zero")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty image upload")

    filename = file.filename or f"page-{page_number}.png"
    if not page_number:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid page_number")

    with tempfile.TemporaryDirectory(prefix="ocr-service-image-") as temp_dir:
        image_path = Path(temp_dir) / filename
        image_path.write_bytes(content)

        resolved_width, resolved_height = width, height
        if resolved_width is None or resolved_height is None:
            actual_width, actual_height = _read_image_size(image_path)
            if resolved_width is None:
                resolved_width = actual_width
            if resolved_height is None:
                resolved_height = actual_height

        if (resolved_width or 0) <= 0 or (resolved_height or 0) <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="unable to resolve image dimensions")

        engine = _get_ocr_engine(request)
        layout = await run_in_threadpool(
            engine.parse_page,
            image_path=image_path,
            page_number=page_number,
            width=int(resolved_width),
            height=int(resolved_height),
        )

    return _serialize_layout(layout)


@router.post("/ocr/pdf")
async def ocr_pdf(
    request: Request,
    file: UploadFile = File(...),
    dpi: int = Form(default=300),
) -> dict[str, Any]:
    filename = file.filename or "document.pdf"
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="only PDF files are supported")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty PDF upload")
    if dpi <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="dpi must be a positive integer")

    with tempfile.TemporaryDirectory(prefix="ocr-service-pdf-") as temp_dir:
        pdf_path = Path(temp_dir) / filename
        pdf_path.write_bytes(pdf_bytes)

        layout = build_job_artifact_layout(
            Path(temp_dir),
            f"ocr-service-{uuid4().hex}",
            pdf_path,
            source_key=filename,
        )
        rendered = render_pdf_document(pdf_path, layout, dpi=dpi)

        engine = _get_ocr_engine(request)
        pages: list[dict[str, Any]] = []
        for page in rendered.pages:
            layout = await run_in_threadpool(
                engine.parse_page,
                image_path=page.image_path,
                page_number=page.page_no,
                width=page.width,
                height=page.height,
            )
            pages.append(_serialize_layout(layout))

        return _serialize_pdf_response(pages=pages, page_count=len(pages), pdf_name=filename)


@compat_router.get("/health", response_class=JSONResponse)
def health_alias(request: Request) -> dict[str, Any]:
    compat = _get_compat_service(request)
    return {
        "status": "ok",
        "service": "a-cong OCR Service",
        "compat_mode": "datalab-like-v1",
        "versions": compat.versions(),
    }


@compat_router.get("/api/health", response_class=JSONResponse)
def api_health_alias(request: Request) -> dict[str, Any]:
    return health_alias(request)


@router.post("/ocr", response_class=JSONResponse)
async def submit_ocr(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    file_0: UploadFile | None = File(default=None, alias="file.0"),
    page_number: int = Form(default=1),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
    dpi: int = Form(default=300),
    max_pages: int | None = Form(default=None),
    page_range: str | None = Form(default=None),
) -> dict[str, Any]:
    upload = _pick_upload(file, file_0)
    payload = await upload.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")

    compat = _get_compat_service(request)
    request_id = compat.create_request(
        "ocr",
        str(request.url_for("get_ocr_result_check", request_id="{request_id}")),
        meta={"file_name": upload.filename},
    )
    background_tasks.add_task(
        compat.process_ocr_request,
        request_id,
        file_bytes=payload,
        file_name=upload.filename or "document.bin",
        page_number=page_number,
        width=width,
        height=height,
        dpi=dpi,
        max_pages=max_pages,
        page_range=page_range,
    )
    return compat.submission_response(request_id)


@router.get("/ocr/{request_id}", response_class=JSONResponse, name="get_ocr_result_check")
def get_ocr_result_check(request: Request, request_id: str) -> dict[str, Any]:
    compat = _get_compat_service(request)
    try:
        return compat.get_request_result(request_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="request not found") from None


@router.post("/marker", response_class=JSONResponse)
async def submit_marker(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile | None = File(default=None),
    file_0: UploadFile | None = File(default=None, alias="file.0"),
    page_number: int = Form(default=1),
    width: int | None = Form(default=None),
    height: int | None = Form(default=None),
    dpi: int = Form(default=300),
    max_pages: int | None = Form(default=None),
    page_range: str | None = Form(default=None),
    output_format: str = Form(default="json"),
) -> dict[str, Any]:
    upload = _pick_upload(file, file_0)
    payload = await upload.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")

    compat = _get_compat_service(request)
    request_id = compat.create_request(
        "marker",
        str(request.url_for("get_marker_result_check", request_id="{request_id}")),
        meta={"file_name": upload.filename, "output_format": output_format},
    )
    background_tasks.add_task(
        compat.process_marker_request,
        request_id,
        file_bytes=payload,
        file_name=upload.filename or "document.bin",
        page_number=page_number,
        width=width,
        height=height,
        dpi=dpi,
        max_pages=max_pages,
        page_range=page_range,
        output_format=output_format,
    )
    return compat.submission_response(request_id)


@router.get("/marker/{request_id}", response_class=JSONResponse, name="get_marker_result_check")
def get_marker_result_check(request: Request, request_id: str) -> dict[str, Any]:
    compat = _get_compat_service(request)
    try:
        return compat.get_request_result(request_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="request not found") from None


@router.get("/thumbnails/{lookup_key}", response_class=JSONResponse)
def get_thumbnails(
    request: Request,
    lookup_key: str,
    page_range: str | None = Query(default=None),
    thumb_width: int = Query(default=300),
) -> dict[str, Any]:
    compat = _get_compat_service(request)
    try:
        return compat.thumbnails(lookup_key, thumb_width=thumb_width, page_range=page_range)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="lookup_key not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/workflows/step_types", response_class=JSONResponse)
def list_step_types(request: Request) -> dict[str, Any]:
    return _get_compat_service(request).list_step_types()


@router.get("/workflows/workflows", response_class=JSONResponse)
def list_workflows(request: Request) -> dict[str, Any]:
    return _get_compat_service(request).list_workflows()


@router.post("/workflows/workflows", response_class=JSONResponse)
async def create_workflow(request: Request) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    try:
        return compat.create_workflow(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/workflows/workflows/{workflow_id}", response_class=JSONResponse)
def get_workflow(request: Request, workflow_id: int) -> dict[str, Any]:
    compat = _get_compat_service(request)
    try:
        return compat.get_workflow(workflow_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found") from None


@router.delete("/workflows/workflows/{workflow_id}", response_class=JSONResponse)
def delete_workflow(request: Request, workflow_id: int) -> dict[str, Any]:
    compat = _get_compat_service(request)
    try:
        return compat.delete_workflow(workflow_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found") from None


@router.post("/workflows/workflows/{workflow_id}/execute", response_class=JSONResponse)
async def execute_workflow(request: Request, workflow_id: int, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    try:
        execution = compat.create_execution(workflow_id, payload.get("input_config") or {})
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="workflow not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    background_tasks.add_task(compat.run_execution, execution["execution_id"])
    return {
        "execution_id": execution["execution_id"],
        "workflow_id": execution["workflow_id"],
        "status": execution["status"],
    }


@router.get("/workflows/executions/{execution_id}", response_class=JSONResponse)
def get_execution_status(request: Request, execution_id: int) -> dict[str, Any]:
    compat = _get_compat_service(request)
    try:
        return compat.get_execution(execution_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="execution not found") from None


@router.post("/files", response_class=JSONResponse)
async def create_file(
    request: Request,
    file: UploadFile | None = File(default=None),
    file_0: UploadFile | None = File(default=None, alias="file.0"),
) -> dict[str, Any]:
    upload = _pick_upload(file, file_0)
    payload = await upload.read()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty upload")
    return _get_defense_service(request).create_file_from_bytes(
        file_name=upload.filename or "document.bin",
        payload=payload,
        content_type=upload.content_type,
        source={"transport": "multipart"},
    )


@router.post("/files/request_upload_url", response_class=JSONResponse)
async def request_upload_url(request: Request) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        slot = service.create_upload_slot(
            file_name=str(payload.get("file_name") or "upload.bin"),
            content_type=str(payload.get("content_type") or "") or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    upload_id = slot["upload_id"]
    return {
        **slot,
        "upload_url": str(request.url_for("upload_file_payload", upload_id=upload_id)),
        "confirm_url": str(request.url_for("confirm_file_upload", upload_id=upload_id)),
    }


@router.put("/files/uploads/{upload_id}", response_class=JSONResponse, name="upload_file_payload")
async def upload_file_payload(request: Request, upload_id: str) -> dict[str, Any]:
    payload = await request.body()
    if not payload:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty request body")
    service = _get_defense_service(request)
    try:
        return service.put_upload_payload(upload_id, payload, request.headers.get("content-type"))
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload slot not found") from None


@router.get("/files/uploads/{upload_id}/confirm", response_class=JSONResponse, name="confirm_file_upload")
def confirm_file_upload(request: Request, upload_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.confirm_upload(upload_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="upload slot not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/files", response_class=JSONResponse)
def list_files(request: Request) -> dict[str, Any]:
    return _get_defense_service(request).list_files()


@router.get("/files/{file_id}", response_class=JSONResponse)
def get_file_record(request: Request, file_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.get_file(file_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found") from None


@router.get("/files/{file_id}/metadata", response_class=JSONResponse)
def get_file_metadata(request: Request, file_id: str) -> dict[str, Any]:
    return get_file_record(request, file_id)


@router.get("/files/{file_id}/download_url", response_class=JSONResponse)
def get_file_download_url(request: Request, file_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.file_download_info(file_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found") from None


@router.get("/files/{file_id}/download")
def download_file(request: Request, file_id: str):
    service = _get_defense_service(request)
    try:
        path = service.get_file_payload_path(file_id)
        record = service.get_file(file_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found") from None
    from fastapi.responses import FileResponse

    return FileResponse(path=path, filename=record["file_name"], media_type=record["content_type"])


@router.delete("/files/{file_id}", response_class=JSONResponse)
def delete_file(request: Request, file_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.delete_file(file_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found") from None


@router.get("/collections", response_class=JSONResponse)
def list_collections(request: Request) -> dict[str, Any]:
    return _get_defense_service(request).list_collections()


@router.post("/collections", response_class=JSONResponse)
async def create_collection(request: Request) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.create_collection(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/collections/{collection_id}", response_class=JSONResponse)
def get_collection(request: Request, collection_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.get_collection(collection_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection not found") from None


@router.put("/collections/{collection_id}", response_class=JSONResponse)
async def update_collection(request: Request, collection_id: int) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.update_collection(collection_id, payload)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.delete("/collections/{collection_id}", response_class=JSONResponse)
def delete_collection(request: Request, collection_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.delete_collection(collection_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection not found") from None


@router.post("/collections/{collection_id}/files", response_class=JSONResponse)
async def add_files_to_collection(request: Request, collection_id: int) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.add_files_to_collection(collection_id, [str(item) for item in payload.get("file_ids", []) or []])
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection not found") from None


@router.delete("/collections/{collection_id}/files/{file_id}", response_class=JSONResponse)
def remove_file_from_collection(request: Request, collection_id: int, file_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.remove_file_from_collection(collection_id, file_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="collection not found") from None


@router.get("/templates", response_class=JSONResponse)
def list_templates(request: Request) -> dict[str, Any]:
    return _get_defense_service(request).list_templates()


@router.post("/templates/promote", response_class=JSONResponse)
async def promote_to_template(request: Request) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.promote_to_template(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/templates/{template_id}", response_class=JSONResponse)
def get_template(request: Request, template_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.get_template(template_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found") from None


@router.put("/templates/{template_id}", response_class=JSONResponse)
async def update_template(request: Request, template_id: int) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.update_template(template_id, payload)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found") from None


@router.delete("/templates/{template_id}", response_class=JSONResponse)
def remove_template(request: Request, template_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.remove_template(template_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found") from None


@router.post("/templates/{template_id}/clone", response_class=JSONResponse)
async def clone_template(request: Request, template_id: int) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.clone_template(template_id, payload)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found") from None


@router.post("/templates/{template_id}/examples", response_class=JSONResponse)
async def add_template_examples(request: Request, template_id: int) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.add_template_examples(template_id, payload)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template not found") from None


@router.get("/templates/{template_id}/examples/{example_id}/download", response_class=JSONResponse)
def download_template_example(request: Request, template_id: int, example_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.download_template_example(template_id, example_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template example not found") from None


@router.delete("/templates/{template_id}/examples/{example_id}", response_class=JSONResponse)
def remove_template_example(request: Request, template_id: int, example_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.remove_template_example(template_id, example_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template example not found") from None


@router.get("/templates/{template_id}/examples/{example_id}/thumbnail", response_class=JSONResponse)
def template_example_thumbnail(request: Request, template_id: int, example_id: str, thumb_width: int = Query(default=300)) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.template_example_thumbnail(template_id, example_id, thumb_width=thumb_width)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="template example not found") from None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/eval_rubrics", response_class=JSONResponse)
def list_eval_rubrics(request: Request) -> dict[str, Any]:
    return _get_defense_service(request).list_eval_rubrics()


@router.post("/eval_rubrics", response_class=JSONResponse)
async def create_eval_rubric(request: Request) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.create_eval_rubric(payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None


@router.get("/eval_rubrics/{rubric_id}", response_class=JSONResponse)
def get_eval_rubric(request: Request, rubric_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.get_eval_rubric(rubric_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="eval rubric not found") from None


@router.put("/eval_rubrics/{rubric_id}", response_class=JSONResponse)
async def update_eval_rubric(request: Request, rubric_id: int) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        return service.update_eval_rubric(rubric_id, payload)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="eval rubric not found") from None


@router.delete("/eval_rubrics/{rubric_id}", response_class=JSONResponse)
def delete_eval_rubric(request: Request, rubric_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.delete_eval_rubric(rubric_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="eval rubric not found") from None


@router.post("/create_document", response_class=JSONResponse)
async def create_document_request(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(compat, "create_document", request, "get_create_document_result_check", payload)
    background_tasks.add_task(service.process_create_document, request_id, payload)
    return compat.submission_response(request_id)


@router.get("/create_document/{request_id}", response_class=JSONResponse, name="get_create_document_result_check")
def get_create_document_result_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.get("/documents/{document_id}", response_class=JSONResponse)
def get_document(request: Request, document_id: str) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.get_document(document_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found") from None


@router.post("/convert_document", response_class=JSONResponse)
async def convert_document(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(compat, "convert_document", request, "get_convert_document_result_check", payload)
    background_tasks.add_task(service.process_convert_document, request_id, payload)
    return compat.submission_response(request_id)


@router.get("/convert_document/{request_id}", response_class=JSONResponse, name="get_convert_document_result_check")
def get_convert_document_result_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.post("/segment_document", response_class=JSONResponse)
async def segment_document(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(compat, "segment_document", request, "get_segment_document_result_check", payload)
    background_tasks.add_task(service.process_segment_document, request_id, payload)
    return compat.submission_response(request_id)


@router.get("/segment_document/{request_id}", response_class=JSONResponse, name="get_segment_document_result_check")
def get_segment_document_result_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.post("/generate_extraction_schemas", response_class=JSONResponse)
async def generate_extraction_schemas(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(
        compat,
        "generate_extraction_schemas",
        request,
        "get_generate_extraction_schemas_result_check",
        payload,
    )
    background_tasks.add_task(service.process_generate_extraction_schemas, request_id, payload)
    return compat.submission_response(request_id)


@router.get(
    "/generate_extraction_schemas/{request_id}",
    response_class=JSONResponse,
    name="get_generate_extraction_schemas_result_check",
)
def get_generate_extraction_schemas_result_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.post("/extract_structured_data", response_class=JSONResponse)
async def extract_structured_data(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(compat, "extract_structured_data", request, "get_extract_structured_data_result_check", payload)
    background_tasks.add_task(service.process_extract_structured_data, request_id, payload)
    return compat.submission_response(request_id)


@router.get(
    "/extract_structured_data/{request_id}",
    response_class=JSONResponse,
    name="get_extract_structured_data_result_check",
)
def get_extract_structured_data_result_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.post("/score_extraction_results", response_class=JSONResponse)
async def score_extraction_results(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(compat, "score_extraction_results", request, "get_score_extraction_results_check", payload)
    background_tasks.add_task(service.process_score_extraction_results, request_id, payload)
    return compat.submission_response(request_id)


@router.get(
    "/score_extraction_results/{request_id}",
    response_class=JSONResponse,
    name="get_score_extraction_results_check",
)
def get_score_extraction_results_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.post("/form_filling", response_class=JSONResponse)
async def form_filling(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(compat, "form_filling", request, "get_form_filling_result_check", payload)
    background_tasks.add_task(service.process_form_filling, request_id, payload)
    return compat.submission_response(request_id)


@router.get("/form_filling/{request_id}", response_class=JSONResponse, name="get_form_filling_result_check")
def get_form_filling_result_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.post("/track_changes", response_class=JSONResponse)
async def track_changes(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    compat = _get_compat_service(request)
    service = _get_defense_service(request)
    request_id = _create_async_request(compat, "track_changes", request, "get_track_changes_result_check", payload)
    background_tasks.add_task(service.process_track_changes, request_id, payload)
    return compat.submission_response(request_id)


@router.get("/track_changes/{request_id}", response_class=JSONResponse, name="get_track_changes_result_check")
def get_track_changes_result_check(request: Request, request_id: str) -> dict[str, Any]:
    return get_ocr_result_check(request, request_id)


@router.get("/batch_runs", response_class=JSONResponse)
def list_batch_runs(request: Request) -> dict[str, Any]:
    return _get_defense_service(request).list_batch_runs()


@router.post("/batch_runs", response_class=JSONResponse)
async def start_batch_run(request: Request, background_tasks: BackgroundTasks) -> dict[str, Any]:
    payload = await request.json()
    service = _get_defense_service(request)
    try:
        batch_run = service.start_batch_run(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from None
    background_tasks.add_task(service.process_batch_run, batch_run["batch_run_id"])
    return batch_run


@router.get("/batch_runs/{batch_run_id}", response_class=JSONResponse)
def get_batch_run(request: Request, batch_run_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.get_batch_run(batch_run_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="batch run not found") from None


@router.get("/batch_runs/{batch_run_id}/results", response_class=JSONResponse)
def get_batch_run_results(request: Request, batch_run_id: int) -> dict[str, Any]:
    service = _get_defense_service(request)
    try:
        return service.get_batch_run_results(batch_run_id)
    except KeyError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="batch run not found") from None


@router.get("/check_pipeline_access", response_class=JSONResponse)
def check_pipeline_access(request: Request) -> dict[str, Any]:
    return _get_defense_service(request).check_pipeline_access()


@router.get("/custom_pipelines", response_class=JSONResponse)
def list_custom_pipelines(request: Request) -> dict[str, Any]:
    return _get_defense_service(request).list_custom_pipelines()


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings = get_settings()
        settings.ensure_directories()
        app.state.ocr_engine = OCREngine()
        app.state.datalab_compat = DatalabCompatService(settings, app.state.ocr_engine)
        app.state.datalab_defense = DefenseDataService(settings, app.state.datalab_compat)
        _warmup_ocr_engine(app.state.ocr_engine)
        yield

    app = FastAPI(
        title="a-cong OCR Service",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.include_router(router)
    app.include_router(compat_router)
    return app


app = create_app()
