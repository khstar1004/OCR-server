from __future__ import annotations

import importlib
import io
import json
import sys
import zipfile
from contextlib import contextmanager
from pathlib import Path
from time import sleep

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


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/playground/api/auth/login",
        json={"username": "admin", "password": "admin123!"},
    )
    assert response.status_code == 200
    assert response.json()["user"]["role"] == "admin"


@contextmanager
def _compat_client(tmp_path: Path, monkeypatch, *, root_path: str = ""):
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

    with TestClient(app, root_path=root_path) as client:
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


def test_ocr_capabilities_and_clean_image_response(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        capabilities = client.get("/api/v1/capabilities")
        assert capabilities.status_code == 200
        capabilities_payload = capabilities.json()
        assert capabilities_payload["features"]["page_range"] is True
        assert capabilities_payload["features"]["multi_output_format"] is True
        assert capabilities_payload["features"]["max_concurrent_ocr_requests"] == 1
        assert capabilities_payload["features"]["marker_modes"] == ["fast", "balanced", "accurate"]
        assert capabilities_payload["features"]["markdown"] is True
        assert capabilities_payload["features"]["request_runtime_metadata"] is True
        assert capabilities_payload["features"]["request_retention_cleanup"] is True
        assert capabilities_payload["endpoints"]["request_cleanup"] == "/api/v1/requests"
        assert capabilities_payload["features"]["tables"] is False

        response = client.post(
            "/api/v1/ocr/image",
            files={"file": ("page.png", _png_bytes(), "image/png")},
            data={"page_number": "3", "include_raw": "false"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["page_number"] == 3
        assert payload["text"].startswith("국방 일일 브리핑")
        assert payload["markdown"].startswith("## 국방 일일 브리핑")
        assert payload["block_count"] == 3
        assert payload["blocks"][0]["order"] == 0
        assert "raw_vl" not in payload
        assert "raw_structure" not in payload
        assert "raw_fallback_ocr" not in payload


def test_runtime_settings_api_updates_effective_values(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        assert client.get("/api/v1/runtime-settings").status_code == 401
        _login_admin(client)
        initial = client.get("/api/v1/runtime-settings")
        assert initial.status_code == 200
        initial_payload = initial.json()
        assert initial_payload["path"].endswith("settings.json")
        assert any(spec["key"] == "ocr_service_timeout_sec" for spec in initial_payload["specs"])
        spec_keys = {spec["key"] for spec in initial_payload["specs"]}
        assert {
            "ocr_service_url",
            "ocr_service_mode",
            "chandra_prompt_type",
            "chandra_batch_size",
            "llm_base_url",
            "watch_poll_interval_sec",
            "watch_stable_scan_count",
            "vllm_model_path",
            "vllm_max_num_seqs",
            "vllm_mm_processor_kwargs",
        } <= spec_keys

        saved = client.put(
            "/api/v1/runtime-settings",
            json={
                "values": {
                    "ocr_max_concurrent_requests": 2,
                    "playground_default_max_pages": 7,
                    "target_api_timeout_sec": 45,
                }
            },
        )
        assert saved.status_code == 200
        saved_payload = saved.json()
        assert saved_payload["values"]["ocr_max_concurrent_requests"] == 2
        assert saved_payload["values"]["playground_default_max_pages"] == 7
        assert saved_payload["overrides"]["target_api_timeout_sec"] == 45.0

        capabilities = client.get("/api/v1/capabilities").json()
        assert capabilities["features"]["max_concurrent_ocr_requests"] == 2
        playground_capabilities = client.get("/playground/api/capabilities").json()
        assert playground_capabilities["features"]["default_max_pages"] == 7

        bad = client.put("/api/v1/runtime-settings", json={"values": {"not_a_setting": 1}})
        assert bad.status_code == 400
        malformed = client.put(
            "/api/v1/runtime-settings",
            content="{bad",
            headers={"content-type": "application/json"},
        )
        assert malformed.status_code == 400


def test_playground_account_signup_requires_admin_approval(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        signup = client.post(
            "/playground/api/auth/signup",
            json={
                "username": "analyst1",
                "password": "strongpass1",
                "display_name": "분석관",
                "email": "analyst@example.test",
                "reason": "OCR 품질 확인",
            },
        )
        assert signup.status_code == 200
        user_id = signup.json()["user"]["id"]
        pending_login = client.post(
            "/playground/api/auth/login",
            json={"username": "analyst1", "password": "strongpass1"},
        )
        assert pending_login.status_code == 403

        _login_admin(client)
        users = client.get("/playground/api/admin/users")
        assert users.status_code == 200
        assert any(item["username"] == "analyst1" and item["status"] == "pending" for item in users.json()["users"])
        approve = client.post(f"/playground/api/admin/users/{user_id}/approve")
        assert approve.status_code == 200
        assert approve.json()["user"]["status"] == "active"
        logout = client.post("/playground/api/auth/logout")
        assert logout.status_code == 200

        active_login = client.post(
            "/playground/api/auth/login",
            json={"username": "analyst1", "password": "strongpass1"},
        )
        assert active_login.status_code == 200
        assert active_login.json()["user"]["role"] == "user"
        forbidden = client.get("/playground/api/admin/users")
        assert forbidden.status_code == 403


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


def test_playground_convert_and_download_include_images(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        page = client.get("/playground")
        assert page.status_code == 200
        assert "army-ocr Playground" in page.text
        assert "차트 읽기" in page.text
        assert "Chart Understanding" not in page.text
        assert 'id="fileInput" name="file" type="file"' in page.text
        assert "multiple hidden" in page.text
        assert 'id="fileList"' in page.text
        assert "__PLAYGROUND_BASE__" not in page.text
        assert 'href="/playground/docs"' in page.text
        assert 'href="/playground/api-guide"' in page.text
        assert 'href="/playground/api-reference"' in page.text
        assert 'href="/openapi.json"' in page.text
        assert 'href="/api/v1/capabilities"' in page.text
        assert 'href="/health"' in page.text
        assert 'href="/playground/admin"' in page.text
        assert 'data-pane="runtimeSettingsPane"' not in page.text
        assert 'data-pane="historyPane"' in page.text
        assert 'id="historyPane"' in page.text
        forwarded_page = client.get("/playground", headers={"x-forwarded-prefix": "/a-cong-ocr-playground"})
        assert '<base href="/a-cong-ocr-playground/">' in forwarded_page.text
        assert 'href="/a-cong-ocr-playground/docs"' in forwarded_page.text
        assert 'href="/a-cong-ocr-playground/api-guide"' in forwarded_page.text
        assert 'href="/a-cong-ocr-playground/api-reference"' in forwarded_page.text
        assert 'href="/a-cong-ocr-api/openapi.json"' in forwarded_page.text
        assert 'href="/a-cong-ocr-api/api/v1/capabilities"' in forwarded_page.text
        assert 'href="/a-cong-ocr-api/health"' in forwarded_page.text
        assert 'href="/a-cong-ocr-playground/admin"' in forwarded_page.text
        script = client.get("/playground/assets/playground.js")
        assert script.status_code == 200
        assert "api/history?limit=80" in script.text
        guide = client.get("/playground/docs")
        assert guide.status_code == 200
        assert '<base href="/playground/">' in guide.text
        assert "army-ocr 모델/API" in guide.text
        api_guide = client.get("/playground/api-guide")
        assert api_guide.status_code == 200
        assert "army-ocr API Guide" in api_guide.text
        assert "Reusable File Flow" in api_guide.text
        assert "unimplemented Datalab-only APIs" in api_guide.text
        api_reference = client.get("/playground/api-reference")
        assert api_reference.status_code == 200
        assert "army-ocr API Reference" in api_reference.text
        assert "Create Collection" in api_reference.text
        assert "Generate Schemas" in api_reference.text
        assert "Score Extraction" in api_reference.text
        assert "Run Custom Processor" not in api_reference.text
        assert "Submit Custom Pipeline" not in api_reference.text
        assert "Table Recognition" not in api_reference.text
        guide_md = client.get("/playground/api-guide.md")
        assert guide_md.status_code == 200
        assert "# army-ocr API Guide" in guide_md.text
        assert "Python polling 예시" in guide_md.text

        resources = client.get("/playground/api/resources")
        assert resources.status_code == 200
        resources_payload = resources.json()
        assert resources_payload["health"]["ocr_service_ready"] is True
        assert resources_payload["links"]["docs"]["url"] == "/playground/docs"
        assert resources_payload["links"]["api_reference"]["url"] == "/playground/api-reference"
        assert resources_payload["links"]["api_guide"]["url"] == "/playground/api-guide"
        assert resources_payload["links"]["openapi"]["url"] == "/openapi.json"
        assert resources_payload["links"]["api_capabilities"]["url"] == "/api/v1/capabilities"
        assert resources_payload["links"]["admin"]["url"] == "/playground/admin"
        forwarded_resources = client.get(
            "/playground/api/resources",
            headers={"x-forwarded-prefix": "/a-cong-ocr-playground"},
        )
        assert forwarded_resources.json()["links"]["api_capabilities"]["url"] == "/a-cong-ocr-api/api/v1/capabilities"
        assert forwarded_resources.json()["links"]["docs"]["url"] == "/a-cong-ocr-playground/docs"
        assert forwarded_resources.json()["links"]["api_guide"]["url"] == "/a-cong-ocr-playground/api-guide"
        assert forwarded_resources.json()["links"]["api_reference"]["url"] == "/a-cong-ocr-playground/api-reference"
        assert forwarded_resources.json()["links"]["openapi"]["url"] == "/a-cong-ocr-api/openapi.json"
        assert forwarded_resources.json()["links"]["admin"]["url"] == "/a-cong-ocr-playground/admin"

        async_response = client.post(
            "/playground/api/convert/start",
            files={"file": ("page.png", _png_bytes(), "image/png")},
            data={"page_range": "0-9", "mode": "balanced", "skip_cache": "true"},
        )
        assert async_response.status_code == 200
        async_payload = async_response.json()
        assert async_payload["status"] == "processing"
        assert async_payload["request_id"]
        polled = None
        for _ in range(20):
            polled = client.get(f"/playground/api/convert/{async_payload['request_id']}")
            assert polled.status_code == 200
            if polled.json().get("status") == "complete":
                break
            sleep(0.05)
        assert polled is not None
        assert polled.status_code == 200
        assert polled.json()["success"] is True

        response = client.post(
            "/playground/api/convert",
            files={"file": ("page.png", _png_bytes(), "image/png")},
            data={"page_range": "0-9", "mode": "balanced", "skip_cache": "true"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["success"] is True
        assert payload["request_id"]
        assert payload["pages"][0]["image_url"].endswith("/page-0001.png")
        assert f"api/images/{payload['request_id']}/page-0001-image-0001.png" in payload["views"]["markdown"]
        assert "ocr_assets" in payload["views"]["json"]

        history = client.get("/playground/api/history")
        assert history.status_code == 200
        history_payload = history.json()
        assert history_payload["success"] is True
        history_ids = {item["request_id"] for item in history_payload["items"]}
        assert async_payload["request_id"] in history_ids
        assert payload["request_id"] in history_ids
        current_item = next(item for item in history_payload["items"] if item["request_id"] == payload["request_id"])
        assert current_item["status"] == "complete"
        assert current_item["file_name"] == "page.png"
        assert current_item["page_count"] == 1
        assert current_item["result_url"] == f"api/convert/{payload['request_id']}"
        assert current_item["download_url"] == f"api/download/{payload['request_id']}"

        crop_asset = next(asset for asset in payload["assets"] if asset["kind"] == "crop")
        crop_response = client.get(f"/playground/api/images/{payload['request_id']}/{crop_asset['name']}")
        assert crop_response.status_code == 200
        assert crop_response.headers["content-type"].startswith("image/png")
        crop = Image.open(io.BytesIO(crop_response.content))
        assert crop.size == (260, 180)

        download = client.get(f"/playground/api/download/{payload['request_id']}")
        assert download.status_code == 200
        assert download.headers["content-type"] == "application/zip"
        assert "army-ocr-result-" in download.headers["content-disposition"]
        with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
            names = set(archive.namelist())
            assert "result.md" in names
            assert "result.html" in names
            assert "result.json" in names
            assert "images/page-0001.png" in names
            assert "images/page-0001-image-0001.png" in names
            markdown = archive.read("result.md").decode("utf-8")
            assert "![Page 1](images/page-0001.png)" in markdown
            assert "![Page 1 image 1](images/page-0001-image-0001.png)" in markdown


def test_playground_convert_status_returns_partial_pages(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        domain_types = importlib.import_module("app.domain.types")
        image_path = tmp_path / "partial-page.png"
        image_path.write_bytes(_png_bytes())
        request_id = client.app.state.datalab_compat.create_request(
            "marker",
            meta={"file_name": image_path.name, "playground": True},
        )
        page = domain_types.PageLayout(
            page_number=1,
            width=640,
            height=480,
            image_path=image_path,
            blocks=[
                domain_types.OCRBlock(
                    block_id="partial-title-1",
                    page_number=1,
                    label=domain_types.BlockLabel.TITLE,
                    bbox=[40, 40, 320, 92],
                    text="중간 결과 제목",
                    confidence=0.96,
                ),
                domain_types.OCRBlock(
                    block_id="partial-text-1",
                    page_number=1,
                    label=domain_types.BlockLabel.TEXT,
                    bbox=[48, 120, 600, 320],
                    text="첫 쪽이 끝나면 바로 보여야 한다.",
                    confidence=0.94,
                ),
            ],
            raw_vl={},
            raw_structure={},
            raw_fallback_ocr={},
        )
        compat = client.app.state.datalab_compat
        partial = compat._build_marker_result(
            request_id,
            image_path.name,
            [page],
            output_formats=["json", "markdown", "html", "chunks"],
            mode="balanced",
            max_pages=2,
            page_range="0-1",
            paginate=False,
            add_block_ids=True,
            include_markdown_in_chunks=True,
            skip_cache=True,
            extras="",
            additional_config="{}",
        )
        partial["status"] = "processing"
        partial["success"] = None
        partial["page_count"] = 2
        partial["processed_page_count"] = 1
        partial["progress"] = {"status": "processing", "processed_pages": 1, "total_pages": 2, "percent": 50.0}
        partial["metadata"].update({"processed_page_count": 1, "total_page_count": 2, "processing_complete": False})
        partial["json"]["page_count"] = 2
        partial["result"]["json"]["page_count"] = 2
        compat._update_request_record(
            request_id,
            status="processing",
            page_image_paths=[str(image_path)],
            result=partial,
            error=None,
        )

        response = client.get(f"/playground/api/convert/{request_id}")

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "processing"
        assert payload["success"] is None
        assert payload["page_count"] == 2
        assert payload["processed_page_count"] == 1
        assert payload["progress"]["percent"] == 50.0
        assert len(payload["pages"]) == 1
        assert payload["pages"][0]["blocks"][0]["text"] == "중간 결과 제목"
        assert payload["pages"][0]["image_url"].endswith("/page-0001.png")
        assert "중간 결과 제목" in payload["views"]["markdown"]


def test_playground_admin_page_requires_login(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        redirected = client.get("/playground/admin", follow_redirects=False)
        assert redirected.status_code == 303
        assert redirected.headers["location"] == "/playground/login"
        login_page = client.get("/playground/login")
        assert login_page.status_code == 200
        assert "계정 신청" in login_page.text
        _login_admin(client)
        admin_page = client.get("/playground/admin")
        assert admin_page.status_code == 200
        assert "army-ocr 관리자 페이지" in admin_page.text


def test_playground_links_follow_root_path_when_forwarded_prefix_is_missing(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch, root_path="/a-cong-ocr-playground") as client:
        page = client.get("/playground")
        assert page.status_code == 200
        assert '<base href="/a-cong-ocr-playground/">' in page.text
        assert 'href="/a-cong-ocr-playground/docs"' in page.text
        assert 'href="/a-cong-ocr-api/api/v1/capabilities"' in page.text
        assert 'href="/a-cong-ocr-playground/admin"' in page.text

        resources = client.get("/playground/api/resources")
        assert resources.status_code == 200
        links = resources.json()["links"]
        assert links["docs"]["url"] == "/a-cong-ocr-playground/docs"
        assert links["api_capabilities"]["url"] == "/a-cong-ocr-api/api/v1/capabilities"
        assert links["admin"]["url"] == "/a-cong-ocr-playground/admin"


def test_compat_marker_accepts_datalab_options(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/marker",
            files={"file.0": ("page.png", _png_bytes(), "image/png")},
            data={
                "output_format": "json,markdown,chunks,html",
                "mode": "accurate",
                "paginate": "true",
                "add_block_ids": "true",
                "include_markdown_in_chunks": "true",
                "skip_cache": "true",
                "extras": "extract_links,table_row_bboxes",
                "additional_config": '{"keep_pageheader_in_output": true}',
            },
        )

        assert response.status_code == 200
        request_id = response.json()["request_id"]
        result = client.get(f"/api/v1/marker/{request_id}")

        assert result.status_code == 200
        payload = result.json()
        assert payload["status"] == "complete"
        assert payload["output_formats"] == ["json", "markdown", "chunks", "html"]
        assert payload["metadata"]["mode"] == "accurate"
        assert payload["metadata"]["skip_cache"] is True
        assert payload["metadata"]["extras"] == ["extract_links", "table_row_bboxes"]
        assert payload["parse_quality_score"] > 0
        assert payload["runtime"]["request_kind"] == "marker"
        assert payload["runtime"]["status"] == "complete"
        assert payload["runtime"]["page_count"] == 1
        assert payload["runtime"]["duration_ms"] >= 0
        assert payload["chunks"][0]["markdown"].startswith("## 국방 일일 브리핑")
        assert "data-block-id='title-1'" in payload["html"]
        assert set(payload["result"]) == {"json", "markdown", "chunks", "html"}


def test_compat_request_cleanup_dry_run_and_delete(tmp_path: Path, monkeypatch) -> None:
    with _compat_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/marker",
            files={"file": ("page.png", _png_bytes(), "image/png")},
            data={"output_format": "json"},
        )
        assert response.status_code == 200
        request_id = response.json()["request_id"]
        record_path = tmp_path / "output" / "_compat_api" / "requests" / request_id / "record.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["updated_at"] = "2000-01-01T00:00:00+00:00"
        record["status"] = "complete"
        record_path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")

        dry_run = client.delete("/api/v1/requests?older_than_hours=1&status_filter=complete&dry_run=true")
        assert dry_run.status_code == 200
        dry_payload = dry_run.json()
        assert dry_payload["candidate_count"] == 1
        assert dry_payload["deleted_count"] == 0
        assert dry_payload["request_ids"] == [request_id]
        assert record_path.exists()

        deleted = client.delete("/api/v1/requests?older_than_hours=1&status_filter=complete&dry_run=false")
        assert deleted.status_code == 200
        delete_payload = deleted.json()
        assert delete_payload["candidate_count"] == 1
        assert delete_payload["deleted_count"] == 1
        assert delete_payload["request_ids"] == [request_id]
        assert not record_path.parent.exists()


def test_compat_marker_accepts_file_url_and_rejects_bad_mode(tmp_path: Path, monkeypatch) -> None:
    local_image = tmp_path / "file-url.png"
    local_image.write_bytes(_png_bytes())

    with _compat_client(tmp_path, monkeypatch) as client:
        response = client.post(
            "/api/v1/marker",
            data={"file_url": str(local_image), "output_format": "markdown"},
        )
        assert response.status_code == 200
        request_id = response.json()["request_id"]
        assert client.get(f"/api/v1/marker/{request_id}").json()["metadata"]["source_file"] == local_image.name

        bad_mode = client.post(
            "/api/v1/marker",
            files={"file": ("page.png", _png_bytes(), "image/png")},
            data={"mode": "slow"},
        )
        assert bad_mode.status_code == 400
        assert "mode must be one of" in bad_mode.json()["detail"]


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
