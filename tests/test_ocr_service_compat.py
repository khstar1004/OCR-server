from __future__ import annotations

import importlib
import io
import sys
from contextlib import contextmanager
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image


def _reset_app_modules() -> None:
    for name in list(sys.modules):
        if name == "app" or name.startswith("app."):
            sys.modules.pop(name, None)


def _png_bytes(size: tuple[int, int] = (640, 480), color: str = "white") -> bytes:
    image = Image.new("RGB", size, color=color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@contextmanager
def _compat_client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("INPUT_ROOT", str((tmp_path / "input").resolve()))
    monkeypatch.setenv("OUTPUT_ROOT", str((tmp_path / "output").resolve()))
    monkeypatch.setenv("MODELS_ROOT", str((tmp_path / "models").resolve()))
    monkeypatch.setenv("CHANDRA_MODEL_DIR", "")

    _reset_app_modules()
    ocr_service = importlib.import_module("app.ocr_service")
    domain_types = importlib.import_module("app.domain.types")

    monkeypatch.setattr(ocr_service, "_warmup_ocr_engine", lambda engine: None)
    app = ocr_service.create_app()

    class StubEngine:
        def parse_page(self, image_path: Path, page_number: int, width: int, height: int, stage_callback=None):
            OCRBlock = domain_types.OCRBlock
            BlockLabel = domain_types.BlockLabel
            PageLayout = domain_types.PageLayout
            if stage_callback is not None:
                stage_callback("ocr_vl", "completed", "stubbed")
            return PageLayout(
                page_number=page_number,
                width=width,
                height=height,
                image_path=image_path,
                blocks=[
                    OCRBlock(
                        block_id=f"title-{page_number}",
                        page_number=page_number,
                        label=BlockLabel.TITLE,
                        bbox=[40, 40, 320, 92],
                        text="국방 일일 브리핑",
                        confidence=0.98,
                    ),
                    OCRBlock(
                        block_id=f"text-{page_number}",
                        page_number=page_number,
                        label=BlockLabel.TEXT,
                        bbox=[48, 120, 600, 320],
                        text="훈련 결과와 장비 점검 내용을 정리했다.",
                        confidence=0.95,
                    ),
                    OCRBlock(
                        block_id=f"image-{page_number}",
                        page_number=page_number,
                        label=BlockLabel.IMAGE,
                        bbox=[360, 80, 620, 260],
                        text="",
                        confidence=0.88,
                    ),
                ],
                raw_vl={
                    "engine": "stub",
                    "parsing_res_list": [
                        {"label": "title", "bbox": [40, 40, 320, 92], "content": "국방 일일 브리핑"},
                        {"label": "text", "bbox": [48, 120, 600, 320], "content": "훈련 결과와 장비 점검 내용을 정리했다."},
                        {"label": "image", "bbox": [360, 80, 620, 260], "content": ""},
                    ],
                },
                raw_structure={},
                raw_fallback_ocr={},
            )

    with TestClient(app) as client:
        stub = StubEngine()
        app.state.ocr_engine = stub
        app.state.datalab_compat.engine = stub
        yield client


def test_compat_health_and_ocr_result_check(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/api/health").json()["status"] == "ok"

        response = client.post(
            "/api/v1/ocr",
            files={"file": ("page.png", _png_bytes(), "image/png")},
            data={"page_number": "1"},
        )

        assert response.status_code == 200
        payload = response.json()
        request_id = payload["request_id"]
        assert payload["request_check_url"].endswith(f"/api/v1/ocr/{request_id}")

        result = client.get(f"/api/v1/ocr/{request_id}")

        assert result.status_code == 200
        result_payload = result.json()
        assert result_payload["status"] == "complete"
        assert result_payload["success"] is True
        assert result_payload["page_count"] == 1
        assert result_payload["pages"][0]["lines"][0]["text"] == "국방 일일 브리핑"


def test_compat_marker_and_thumbnails(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/marker",
            files={"file": ("page.png", _png_bytes(), "image/png")},
            data={"output_format": "json"},
        )

        assert response.status_code == 200
        request_id = response.json()["request_id"]

        result = client.get(f"/api/v1/marker/{request_id}")
        assert result.status_code == 200
        payload = result.json()
        assert payload["status"] == "complete"
        assert payload["success"] is True
        assert payload["json"]["pages"][0]["blocks"][0]["label"] == "title"
        assert payload["json"]["pages"][0]["text"].startswith("국방 일일 브리핑")

        thumbs = client.get(f"/api/v1/thumbnails/{request_id}")
        assert thumbs.status_code == 200
        thumbs_payload = thumbs.json()
        assert thumbs_payload["success"] is True
        assert len(thumbs_payload["thumbnails"]) == 1


def test_compat_workflow_crud_and_execute(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        create_workflow = client.post(
            "/api/v1/workflows/workflows",
            json={
                "name": "OCR Workflow",
                "steps": [
                    {
                        "step_key": "ocr",
                        "unique_name": "ocr_step",
                        "settings": {"max_pages": 1},
                    }
                ],
            },
        )

        assert create_workflow.status_code == 200
        workflow_id = create_workflow.json()["workflow_id"]

        listed = client.get("/api/v1/workflows/workflows")
        assert listed.status_code == 200
        assert listed.json()["count"] == 1

        local_image = tmp_path / "workflow.png"
        local_image.write_bytes(_png_bytes())

        execution = client.post(
            f"/api/v1/workflows/workflows/{workflow_id}/execute",
            json={"input_config": {"file_urls": [str(local_image)]}},
        )

        assert execution.status_code == 200
        execution_id = execution.json()["execution_id"]

        status_response = client.get(f"/api/v1/workflows/executions/{execution_id}")
        assert status_response.status_code == 200
        status_payload = status_response.json()
        assert status_payload["status"] == "COMPLETED"
        assert "ocr_step" in status_payload["step_outputs"]
        first_output = next(iter(status_payload["step_outputs"]["ocr_step"].values()))
        assert first_output["status"] == "COMPLETED"
        assert first_output["result"]["page_count"] == 1

        deleted = client.delete(f"/api/v1/workflows/workflows/{workflow_id}")
        assert deleted.status_code == 200
        assert deleted.json()["success"] is True


def test_defense_file_document_convert_segment_extract_flow(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        file_response = client.post(
            "/api/v1/files",
            files={"file": ("report.png", _png_bytes(), "image/png")},
        )
        assert file_response.status_code == 200
        file_id = file_response.json()["file_id"]

        create_doc = client.post("/api/v1/create_document", json={"file_id": file_id})
        assert create_doc.status_code == 200
        create_doc_result = client.get(f"/api/v1/create_document/{create_doc.json()['request_id']}")
        assert create_doc_result.status_code == 200
        document_id = create_doc_result.json()["document"]["document_id"]

        convert = client.post("/api/v1/convert_document", json={"document_id": document_id, "output_format": "json"})
        assert convert.status_code == 200
        convert_result = client.get(f"/api/v1/convert_document/{convert.json()['request_id']}")
        assert convert_result.status_code == 200
        assert convert_result.json()["json"]["pages"][0]["articles"][0]["title"] == "국방 일일 브리핑"

        segment = client.post("/api/v1/segment_document", json={"document_id": document_id})
        assert segment.status_code == 200
        segment_result = client.get(f"/api/v1/segment_document/{segment.json()['request_id']}")
        assert segment_result.status_code == 200
        segment_payload = segment_result.json()
        assert segment_payload["total_articles"] == 1
        assert segment_payload["pages"][0]["articles"][0]["body_text"] == "훈련 결과와 장비 점검 내용을 정리했다."

        schema_response = client.post(
            "/api/v1/generate_extraction_schemas",
            json={"field_names": ["title", "summary", "document_date"]},
        )
        assert schema_response.status_code == 200
        schema_result = client.get(f"/api/v1/generate_extraction_schemas/{schema_response.json()['request_id']}")
        assert schema_result.status_code == 200
        schema = schema_result.json()["schema"]

        extract = client.post(
            "/api/v1/extract_structured_data",
            json={"document_id": document_id, "schema": schema},
        )
        assert extract.status_code == 200
        extract_result = client.get(f"/api/v1/extract_structured_data/{extract.json()['request_id']}")
        assert extract_result.status_code == 200
        extracted = extract_result.json()
        assert extracted["structured_data"]["title"] == "국방 일일 브리핑"
        assert "훈련 결과와 장비 점검" in extracted["structured_data"]["summary"]


def test_defense_upload_template_form_and_scoring_flow(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        slot = client.post("/api/v1/files/request_upload_url", json={"file_name": "example.png", "content_type": "image/png"})
        assert slot.status_code == 200
        slot_payload = slot.json()

        upload = client.put(slot_payload["upload_url"], content=_png_bytes(), headers={"content-type": "image/png"})
        assert upload.status_code == 200

        confirm = client.get(slot_payload["confirm_url"])
        assert confirm.status_code == 200
        file_id = confirm.json()["file_id"]

        template = client.post(
            "/api/v1/templates/promote",
            json={
                "name": "보고서 템플릿",
                "kind": "form",
                "content": {"template_text": "제목={{ title }}\n요약={{ summary }}"},
            },
        )
        assert template.status_code == 200
        template_id = template.json()["template_id"]

        add_example = client.post(
            f"/api/v1/templates/{template_id}/examples",
            json={"file_ids": [file_id]},
        )
        assert add_example.status_code == 200
        example_id = add_example.json()["examples"][0]["example_id"]

        thumb = client.get(f"/api/v1/templates/{template_id}/examples/{example_id}/thumbnail")
        assert thumb.status_code == 200
        assert thumb.json()["thumbnail"]

        filled = client.post(
            "/api/v1/form_filling",
            json={
                "template_id": template_id,
                "values": {"title": "국방 일일 브리핑", "summary": "점검 결과 정상"},
            },
        )
        assert filled.status_code == 200
        filled_result = client.get(f"/api/v1/form_filling/{filled.json()['request_id']}")
        assert "국방 일일 브리핑" in filled_result.json()["filled_output"]["template_text"]

        rubric = client.post(
            "/api/v1/eval_rubrics",
            json={"name": "기본 루브릭", "weights": {"title": 2, "summary": 1}},
        )
        assert rubric.status_code == 200
        rubric_id = rubric.json()["eval_rubric_id"]

        score = client.post(
            "/api/v1/score_extraction_results",
            json={
                "predicted": {"title": "국방 일일 브리핑", "summary": "점검 결과 정상"},
                "reference": {"title": "국방 일일 브리핑", "summary": "점검 결과 일부 차이"},
                "rubric_id": rubric_id,
            },
        )
        assert score.status_code == 200
        score_result = client.get(f"/api/v1/score_extraction_results/{score.json()['request_id']}")
        assert score_result.status_code == 200
        assert 0 < score_result.json()["overall_score"] < 1

        changes = client.post(
            "/api/v1/track_changes",
            json={"before": {"title": "A", "summary": "old"}, "after": {"title": "A", "summary": "new"}},
        )
        assert changes.status_code == 200
        changes_result = client.get(f"/api/v1/track_changes/{changes.json()['request_id']}")
        assert changes_result.status_code == 200
        assert "summary" in changes_result.json()["changed_fields"]


def test_defense_collection_and_batch_runs(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        file_ids = []
        for index in range(2):
            file_response = client.post(
                "/api/v1/files",
                files={"file": (f"batch_{index}.png", _png_bytes(), "image/png")},
            )
            assert file_response.status_code == 200
            file_ids.append(file_response.json()["file_id"])

        collection = client.post(
            "/api/v1/collections",
            json={"name": "국방 배치", "file_ids": file_ids},
        )
        assert collection.status_code == 200
        collection_id = collection.json()["collection_id"]

        batch = client.post(
            "/api/v1/batch_runs",
            json={
                "collection_id": collection_id,
                "operation": "extract_structured_data",
                "params": {"schema": {"name": "batch", "fields": [{"name": "title"}, {"name": "summary"}]}},
            },
        )
        assert batch.status_code == 200
        batch_id = batch.json()["batch_run_id"]

        batch_result = client.get(f"/api/v1/batch_runs/{batch_id}/results")
        assert batch_result.status_code == 200
        payload = batch_result.json()
        assert payload["success_count"] == 2
        first = next(iter(payload["results"].values()))
        assert first["structured_data"]["title"] == "국방 일일 브리핑"

        assert client.get("/api/v1/check_pipeline_access").json()["access"] is True
        assert client.get("/api/v1/custom_pipelines").json()["count"] >= 0
