from __future__ import annotations

import base64
import html
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from PyQt6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


STATUS_COLORS = {
    "queued": "#9AA5B1",
    "running": "#1D70B8",
    "completed": "#11835B",
    "completed_with_errors": "#C37B14",
    "failed": "#C0392B",
    "skipped": "#6B7280",
    "parsed": "#11835B",
}


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = self._normalize_base_url(base_url)

    def health(self) -> dict[str, Any]:
        return self._request("GET", "health")

    def start_job(self, source_dir: str) -> dict[str, Any]:
        payload: dict[str, Any] = {"force_reprocess": True}
        if source_dir.strip():
            payload["source_dir"] = source_dir.strip()
        return self._request("POST", "jobs/run-daily", json=payload)

    def start_single_file(self, pdf_path: str) -> dict[str, Any]:
        target = Path(pdf_path).expanduser()
        if not target.exists():
            raise FileNotFoundError(f"PDF file not found: {target}")
        if not target.is_file():
            raise FileNotFoundError(f"PDF file not found: {target}")

        def iter_file() -> Any:
            with target.open("rb") as handle:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    yield chunk

        with httpx.Client(timeout=120.0) as client:
            response = client.request(
                "POST",
                self.resolve_url("jobs/run-single"),
                params={"file_name": target.name, "force_reprocess": True},
                headers={"Content-Type": "application/pdf"},
                content=iter_file(),
            )
            response.raise_for_status()
            return response.json()

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"jobs/{job_id}/detail")

    def get_page_preview(self, job_id: str, page_id: int, overlay: str) -> dict[str, Any]:
        with httpx.Client(timeout=30.0) as client:
            preview_response = client.request(
                "GET",
                self.resolve_url(f"jobs/{job_id}/pages/{page_id}/preview"),
                params={"overlay": overlay},
            )
            preview_response.raise_for_status()
            return preview_response.json()

    def get_binary(self, url_or_path: str) -> bytes:
        with httpx.Client(timeout=30.0) as client:
            response = client.request("GET", self.resolve_url(url_or_path))
            response.raise_for_status()
            return response.content

    def get_article_images(self, image_urls: list[str]) -> dict[str, bytes]:
        images: dict[str, bytes] = {}
        if not image_urls:
            return images
        with httpx.Client(timeout=30.0) as client:
            for image_url in image_urls:
                response = client.request("GET", self.resolve_url(image_url))
                response.raise_for_status()
                images[image_url] = response.content
        return images

    def resolve_url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        parsed = urlparse(self.base_url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if path.startswith("/"):
            return f"{origin}{path}"
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            response = client.request(method, self.resolve_url(path), **kwargs)
            response.raise_for_status()
            return response.json()

    @staticmethod
    def _normalize_base_url(base_url: str) -> str:
        value = (base_url or "http://127.0.0.1:18000/api/v1").strip().rstrip("/")
        parsed = urlparse(value)
        if not parsed.scheme:
            value = f"http://{value}"
            parsed = urlparse(value)
        if not parsed.path or parsed.path == "/":
            value = f"{value}/api/v1"
        return value.rstrip("/")


class WorkerSignals(QObject):
    result = pyqtSignal(object)
    error = pyqtSignal(str)


class ApiTask(QRunnable):
    def __init__(self, fn: Callable[[], Any]):
        super().__init__()
        self.fn = fn
        self.signals = WorkerSignals()

    @pyqtSlot()
    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # noqa: BLE001
            self.signals.error.emit(str(exc))
            return
        self.signals.result.emit(result)


class OverlayPreviewWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setMinimumSize(720, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._pixmap = QPixmap()
        self._regions: list[dict[str, Any]] = []
        self._image_width = 1
        self._image_height = 1
        self._overlay = "merged"

    def clear(self) -> None:
        self._pixmap = QPixmap()
        self._regions = []
        self.update()

    def set_preview(self, pixmap: QPixmap, preview: dict[str, Any]) -> None:
        self._pixmap = pixmap
        self._regions = list(preview.get("regions", []))
        self._image_width = max(int(preview.get("width", 1) or 1), 1)
        self._image_height = max(int(preview.get("height", 1) or 1), 1)
        self._overlay = str(preview.get("overlay_type", "merged"))
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#F7F3ED"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#5A5146"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "페이지를 선택하면 OCR 결과가 표시됩니다.")
            return

        scaled = self._pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        offset_x = (self.width() - scaled.width()) // 2
        offset_y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(offset_x, offset_y, scaled)

        scale_x = scaled.width() / self._image_width
        scale_y = scaled.height() / self._image_height
        show_labels = len(self._regions) <= 100

        for region in self._regions:
            bbox = region.get("bbox") or []
            if len(bbox) != 4:
                continue
            color = QColor(region.get("color") or "#2D3436")
            painter.setPen(QPen(color, 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            x0 = int(offset_x + bbox[0] * scale_x)
            y0 = int(offset_y + bbox[1] * scale_y)
            x1 = int(offset_x + bbox[2] * scale_x)
            y1 = int(offset_y + bbox[3] * scale_y)
            painter.drawRect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))

            if not show_labels:
                continue
            label = str(region.get("label") or "")
            text = str(region.get("text") or "").replace("\n", " ").strip()
            if text:
                label = f"{label}: {text[:36]}" if label else text[:36]
            if not label:
                continue
            metrics = painter.fontMetrics()
            label_w = metrics.horizontalAdvance(label) + 8
            label_h = metrics.height() + 4
            painter.fillRect(x0, max(0, y0 - label_h), label_w, label_h, QColor(color.red(), color.green(), color.blue(), 190))
            painter.setPen(QColor("#FFFFFF"))
            painter.drawText(x0 + 4, max(metrics.ascent() + 1, y0 - 5), label)

        painter.setPen(QColor("#3B342C"))
        painter.drawText(12, 22, f"Overlay: {self._overlay} | Regions: {len(self._regions)}")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("A-Congress OCR Viewer")
        self.resize(1440, 840)

        self.thread_pool = QThreadPool.globalInstance()
        self.current_job_id: str | None = None
        self.current_detail: dict[str, Any] | None = None
        self.current_page_id: int | None = None
        self.current_page_status: str | None = None
        self.current_page_article_count = -1
        self.current_preview_token = 0
        self.current_preview: dict[str, Any] | None = None
        self.in_flight: set[str] = set()
        self.article_image_cache: dict[str, bytes] = {}

        self._build_ui()
        self._apply_style()

        self.poll_timer = QTimer(self)
        self.poll_timer.setInterval(5000)
        self.poll_timer.timeout.connect(self.refresh_job)
        self.poll_timer.start()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        self.setCentralWidget(central)

        toolbar = QFrame()
        toolbar.setObjectName("panel")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 14, 14, 14)
        toolbar_layout.setSpacing(8)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("API"))
        self.base_url_input = QLineEdit("http://127.0.0.1:18000/api/v1")
        row1.addWidget(self.base_url_input, 2)
        row1.addWidget(QLabel("Source"))
        self.source_dir_input = QLineEdit()
        self.source_dir_input.setPlaceholderText("입력 폴더 전체 처리")
        row1.addWidget(self.source_dir_input, 3)
        browse_button = QPushButton("폴더")
        browse_button.clicked.connect(self.browse_source_dir)
        row1.addWidget(browse_button)
        row1.addWidget(QLabel("PDF"))
        self.source_file_input = QLineEdit()
        self.source_file_input.setPlaceholderText("PDF 한 개만 선택해서 처리")
        row1.addWidget(self.source_file_input, 3)
        browse_file_button = QPushButton("PDF")
        browse_file_button.clicked.connect(self.browse_source_file)
        row1.addWidget(browse_file_button)
        start_button = QPushButton("작업 시작")
        start_button.clicked.connect(self.start_job)
        row1.addWidget(start_button)
        toolbar_layout.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Job ID"))
        self.job_id_input = QLineEdit()
        row2.addWidget(self.job_id_input, 3)
        load_button = QPushButton("불러오기")
        load_button.clicked.connect(self.load_job)
        refresh_button = QPushButton("새로고침")
        refresh_button.clicked.connect(self.refresh_job)
        health_button = QPushButton("헬스체크")
        health_button.clicked.connect(self.check_health)
        row2.addWidget(load_button)
        row2.addWidget(refresh_button)
        row2.addWidget(health_button)
        row2.addSpacing(12)
        row2.addWidget(QLabel("Overlay"))
        self.overlay_combo = QComboBox()
        self.overlay_combo.addItem("Merged", "merged")
        self.overlay_combo.addItem("Primary OCR", "vl")
        self.overlay_combo.addItem("Layout / Structure", "structure")
        self.overlay_combo.addItem("Fallback OCR", "fallback")
        self.overlay_combo.currentIndexChanged.connect(self.reload_preview)
        row2.addWidget(self.overlay_combo)
        toolbar_layout.addLayout(row2)
        root.addWidget(toolbar)

        self.status_label = QLabel("대기 중")
        root.addWidget(self.status_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        root.addWidget(self.progress_bar)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.setChildrenCollapsible(False)
        root.addWidget(main_splitter, 1)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["PDF / Page", "상태"])
        self.tree.itemSelectionChanged.connect(self.on_tree_selected)
        main_splitter.addWidget(self.tree)

        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setChildrenCollapsible(False)
        main_splitter.addWidget(right_splitter)
        main_splitter.setStretchFactor(0, 2)
        main_splitter.setStretchFactor(1, 5)

        preview_frame = QFrame()
        preview_frame.setObjectName("panel")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(10, 10, 10, 10)
        self.preview_info_label = QLabel("선택된 페이지 없음")
        preview_layout.addWidget(self.preview_info_label)
        self.preview_widget = OverlayPreviewWidget()
        preview_layout.addWidget(self.preview_widget, 1)
        right_splitter.addWidget(preview_frame)

        text_splitter = QSplitter(Qt.Orientation.Horizontal)
        text_splitter.setChildrenCollapsible(False)
        right_splitter.addWidget(text_splitter)
        right_splitter.setStretchFactor(0, 5)
        right_splitter.setStretchFactor(1, 3)

        extracted_frame = QFrame()
        extracted_frame.setObjectName("panel")
        extracted_layout = QVBoxLayout(extracted_frame)
        extracted_layout.setContentsMargins(10, 10, 10, 10)
        extracted_layout.addWidget(QLabel("Parsed Result"))
        self.extracted_text = QTextBrowser()
        self.extracted_text.setReadOnly(True)
        extracted_layout.addWidget(self.extracted_text)
        text_splitter.addWidget(extracted_frame)

        logs_frame = QFrame()
        logs_frame.setObjectName("panel")
        logs_layout = QVBoxLayout(logs_frame)
        logs_layout.setContentsMargins(10, 10, 10, 10)
        logs_layout.addWidget(QLabel("Logs / Raw"))
        self.logs_text = QPlainTextEdit()
        self.logs_text.setReadOnly(True)
        logs_layout.addWidget(self.logs_text)
        text_splitter.addWidget(logs_frame)
        text_splitter.setStretchFactor(0, 4)
        text_splitter.setStretchFactor(1, 3)

        self.statusBar().showMessage("준비됨")

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #F5F1EA;
                color: #1F1F1F;
                font-family: "Malgun Gothic";
                font-size: 13px;
            }
            QFrame#panel, QTreeWidget, QPlainTextEdit, QTextBrowser, QLineEdit, QComboBox, QProgressBar {
                background: #FFFDF9;
                border: 1px solid #D8D0C2;
                border-radius: 10px;
            }
            QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser {
                padding: 6px;
            }
            QPushButton {
                background: #193A59;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 8px 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #234B70;
            }
            QProgressBar {
                min-height: 18px;
                text-align: center;
            }
            QProgressBar::chunk {
                background: #11835B;
                border-radius: 8px;
            }
            QTreeWidget::item:selected {
                background: #DCEAF7;
                color: #1F1F1F;
            }
            """
        )

    def api_client(self) -> ApiClient:
        return ApiClient(self.base_url_input.text())

    def browse_source_dir(self) -> None:
        selected_dir = QFileDialog.getExistingDirectory(self, "OCR 입력 폴더 선택")
        if selected_dir:
            self.source_dir_input.setText(selected_dir)
            self.source_file_input.clear()

    def browse_source_file(self) -> None:
        selected_file, _ = QFileDialog.getOpenFileName(self, "OCR 입력 PDF 선택", "", "PDF Files (*.pdf)")
        if selected_file:
            self.source_file_input.setText(selected_file)
            self.source_dir_input.clear()

    def check_health(self) -> None:
        self._run_task(self.api_client().health, self._handle_health_success, "health")

    def start_job(self) -> None:
        source_file = self.source_file_input.text().strip()
        if source_file:
            self._run_task(
                lambda: self.api_client().start_single_file(source_file),
                self._handle_start_job,
                "start",
                self._show_request_error,
            )
            return
        self._run_task(
            lambda: self.api_client().start_job(self.source_dir_input.text()),
            self._handle_start_job,
            "start",
            self._show_request_error,
        )

    def load_job(self) -> None:
        job_id = self.job_id_input.text().strip()
        if not job_id:
            self._show_warning("Job ID를 입력하세요.")
            return
        if job_id != self.current_job_id:
            self.article_image_cache.clear()
        self.current_job_id = job_id
        self.refresh_job()

    def refresh_job(self) -> None:
        if not self.current_job_id:
            return
        self._run_task(
            lambda: self.api_client().get_job_detail(self.current_job_id or ""),
            self._handle_job_detail,
            "detail",
            self._show_status_error,
        )

    def reload_preview(self) -> None:
        if self.current_page_id is not None:
            self._request_preview(self.current_page_id)

    def on_tree_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        item = items[0]
        kind = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "page":
            page_id = item.data(1, Qt.ItemDataRole.UserRole)
            if page_id is None:
                return
            self.current_page_id = int(page_id)
            self.current_page_status = str(item.data(0, Qt.ItemDataRole.UserRole + 1) or "")
            self.current_page_article_count = int(item.data(1, Qt.ItemDataRole.UserRole + 1) or 0)
            self._request_preview(self.current_page_id)
            return
        if kind == "pdf" and item.childCount() > 0:
            child = item.child(0)
            self.tree.setCurrentItem(child)

    def _handle_health_success(self, result: dict[str, Any]) -> None:
        self.statusBar().showMessage(f"헬스체크 성공: {result.get('status', 'ok')}", 4000)

    def _handle_start_job(self, result: dict[str, Any]) -> None:
        self.article_image_cache.clear()
        self.current_job_id = str(result.get("job_id") or "")
        self.job_id_input.setText(self.current_job_id)
        self.statusBar().showMessage(f"작업 시작: {self.current_job_id}", 5000)
        self.refresh_job()

    def _handle_job_detail(self, detail: dict[str, Any]) -> None:
        self.current_detail = detail
        self.job_id_input.setText(str(detail.get("job_id", self.current_job_id or "")))
        self.progress_bar.setValue(max(0, min(int(float(detail.get("progress_percent", 0.0) or 0.0)), 100)))
        skipped_files = [pdf.get("file_name") for pdf in detail.get("pdf_files", []) if pdf.get("status") == "skipped"]
        if skipped_files and len(skipped_files) == len(detail.get("pdf_files", [])):
            self.status_label.setText(
                f"Job {detail.get('job_id')} | skipped by duplicate_hash | files: {', '.join(skipped_files)}"
            )
        else:
            self.status_label.setText(
                f"Job {detail.get('job_id')} | {detail.get('status')} | "
                f"PDF {detail.get('processed_pdfs', 0)}/{detail.get('total_pdfs', 0)} | "
                f"Articles {detail.get('total_articles', 0)}"
            )
        self._fill_tree(detail.get("pdf_files", []))
        self._fill_logs(detail.get("recent_logs", []))

    def _fill_tree(self, pdf_files: list[dict[str, Any]]) -> None:
        previous_page_id = self.current_page_id
        self.tree.blockSignals(True)
        self.tree.clear()
        page_to_select: QTreeWidgetItem | None = None
        first_page: QTreeWidgetItem | None = None
        selected_page_status = self.current_page_status
        selected_page_article_count = self.current_page_article_count

        for pdf in pdf_files:
            pdf_item = QTreeWidgetItem(
                [
                    f"{pdf.get('file_name')} ({pdf.get('article_count', 0)} articles)",
                    str(pdf.get("status", "")),
                ]
            )
            pdf_item.setData(0, Qt.ItemDataRole.UserRole, "pdf")
            pdf_item.setForeground(1, QBrush(QColor(STATUS_COLORS.get(str(pdf.get("status", "")), "#555555"))))
            self.tree.addTopLevelItem(pdf_item)

            for page in pdf.get("pages", []):
                page_item = QTreeWidgetItem(
                    [
                        f"Page {page.get('page_number')} ({page.get('article_count', 0)})",
                        str(page.get("status", "")),
                    ]
                )
                page_item.setData(0, Qt.ItemDataRole.UserRole, "page")
                page_item.setData(1, Qt.ItemDataRole.UserRole, int(page["page_id"]))
                page_item.setData(0, Qt.ItemDataRole.UserRole + 1, str(page.get("status", "")))
                page_item.setData(1, Qt.ItemDataRole.UserRole + 1, int(page.get("article_count", 0)))
                page_item.setForeground(1, QBrush(QColor(STATUS_COLORS.get(str(page.get("status", "")), "#555555"))))
                pdf_item.addChild(page_item)
                if first_page is None:
                    first_page = page_item
                if previous_page_id == int(page["page_id"]):
                    page_to_select = page_item
                    selected_page_status = str(page.get("status", ""))
                    selected_page_article_count = int(page.get("article_count", 0))

            pdf_item.setExpanded(True)

        self.tree.blockSignals(False)
        if page_to_select is None:
            page_to_select = first_page
        if page_to_select is not None:
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(page_to_select)
            self.tree.blockSignals(False)
            page_id = page_to_select.data(1, Qt.ItemDataRole.UserRole)
            if page_id is not None:
                page_id_int = int(page_id)
                should_refresh = (
                    page_id_int != self.current_page_id
                    or selected_page_status != self.current_page_status
                    or selected_page_article_count != self.current_page_article_count
                )
                self.current_page_id = page_id_int
                self.current_page_status = selected_page_status
                self.current_page_article_count = selected_page_article_count
                if should_refresh:
                    self._request_preview(self.current_page_id)
        else:
            self.current_page_id = None
            self.current_page_status = None
            self.current_page_article_count = -1
            self.preview_info_label.setText("선택된 페이지 없음")
            self.preview_widget.clear()
            skipped = [pdf.get("file_name") for pdf in pdf_files if pdf.get("status") == "skipped"]
            if skipped:
                self.extracted_text.setPlainText(
                    "이번 작업은 duplicate_hash 때문에 스킵되었습니다.\n"
                    "같은 PDF를 다시 보고 싶으면 GUI에서 새 작업을 시작하면 강제 재처리로 다시 수행됩니다.\n\n"
                    f"Skipped files: {', '.join(str(name) for name in skipped)}"
                )
            else:
                self.extracted_text.clear()

    def _fill_logs(self, logs: list[dict[str, Any]]) -> None:
        lines: list[str] = []
        for log in logs[-80:]:
            created = str(log.get("created_at", "")).replace("T", " ")[:19]
            pdf_file = log.get("pdf_file") or "-"
            page = log.get("page_number")
            page_text = f" page={page}" if page else ""
            lines.append(
                f"[{created}] {log.get('step_name')} {log.get('status')} pdf={pdf_file}{page_text} | {log.get('message', '')}"
            )
        self.logs_text.setPlainText("\n".join(lines))
        self.logs_text.verticalScrollBar().setValue(self.logs_text.verticalScrollBar().maximum())

    def _request_preview(self, page_id: int) -> None:
        if not self.current_job_id:
            return
        overlay = str(self.overlay_combo.currentData())
        self.current_preview_token += 1
        token = self.current_preview_token
        self.current_preview = None
        self.preview_info_label.setText(f"page {page_id} 로드 중...")
        self.preview_widget.clear()
        self.extracted_text.setHtml(self._build_loading_html(page_id, overlay))
        self._run_task(
            lambda: self.api_client().get_page_preview(self.current_job_id or "", page_id, overlay),
            lambda preview: self._handle_preview(token, preview),
            f"preview-{token}",
            self._show_status_error,
        )

    def _handle_preview(self, token: int, preview: dict[str, Any]) -> None:
        if token != self.current_preview_token:
            return
        self.current_preview = preview
        self.preview_info_label.setText(
            f"{preview.get('pdf_file')} | page {preview.get('page_number')} | "
            f"{preview.get('parse_status')} | {preview.get('overlay_type')}"
        )
        self.extracted_text.setHtml(self._build_article_html(preview, self.article_image_cache))
        image_url = str(preview.get("image_url") or "").strip()
        if image_url:
            self._run_task(
                lambda: self.api_client().get_binary(image_url),
                lambda image_bytes: self._handle_preview_page_image(token, preview, image_bytes),
                f"preview-page-image-{token}",
                self._show_status_error,
            )
        missing_article_images = self._missing_article_image_urls(preview)
        if missing_article_images:
            self._run_task(
                lambda: self.api_client().get_article_images(missing_article_images),
                lambda image_map: self._handle_preview_article_images(token, image_map),
                f"preview-article-images-{token}",
                self._show_status_error,
            )

    def _handle_preview_page_image(self, token: int, preview: dict[str, Any], image_bytes: bytes) -> None:
        if token != self.current_preview_token:
            return
        pixmap = QPixmap()
        pixmap.loadFromData(image_bytes)
        self.preview_widget.set_preview(pixmap, preview)

    def _handle_preview_article_images(self, token: int, image_map: dict[str, bytes]) -> None:
        self.article_image_cache.update(image_map)
        if token != self.current_preview_token or self.current_preview is None:
            return
        self.extracted_text.setHtml(self._build_article_html(self.current_preview, self.article_image_cache))

    def _missing_article_image_urls(self, preview: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        for article in preview.get("articles", []):
            for image in article.get("images", []):
                image_url = str(image.get("image_url") or "").strip()
                if not image_url or image_url in self.article_image_cache:
                    continue
                if image_url in missing:
                    continue
                missing.append(image_url)
        return missing

    @staticmethod
    def _build_loading_html(page_id: int, overlay: str) -> str:
        return f"""
        <html>
            <body style="font-family:'Malgun Gothic'; padding:18px; background:#FFFDF9; color:#1E1E1E;">
                <div style="font-size:12px; color:#6A5D50; margin-bottom:8px;">Page {page_id} | Overlay {html.escape(overlay)}</div>
                <div style="padding:18px; border:1px solid #E4D9CA; border-radius:12px; background:#FFFFFF;">
                    OCR 결과를 불러오는 중입니다.
                </div>
            </body>
        </html>
        """

    def _build_article_html(self, preview: dict[str, Any], image_map: dict[str, bytes]) -> str:
        articles = list(preview.get("articles", []))
        cards = [self._build_article_card(index, article, image_map) for index, article in enumerate(articles, start=1)]
        if not cards:
            cards = [
                """
                <section class="article-card empty">
                    <div class="article-kicker">NO ARTICLES</div>
                    <h2>이 페이지에서 아직 기사 결과가 만들어지지 않았습니다.</h2>
                    <p>작업이 진행 중이거나, 해당 페이지가 기사로 군집화되지 않은 상태입니다.</p>
                </section>
                """
            ]

        page_title = html.escape(str(preview.get("pdf_file") or ""))
        page_number = int(preview.get("page_number") or 0)
        overlay_type = html.escape(str(preview.get("overlay_type") or "merged"))
        return f"""
        <html>
            <head>
                <style>
                    body {{
                        background: #FFFDF9;
                        color: #1E1E1E;
                        font-family: "Malgun Gothic";
                        margin: 0;
                        padding: 18px;
                    }}
                    .page-header {{
                        border-bottom: 1px solid #E6DDCF;
                        margin-bottom: 18px;
                        padding-bottom: 12px;
                    }}
                    .page-header h1 {{
                        font-size: 21px;
                        margin: 0 0 6px 0;
                    }}
                    .page-meta {{
                        color: #6A5D50;
                        font-size: 12px;
                    }}
                    .article-card {{
                        background: #FFFFFF;
                        border: 1px solid #E4D9CA;
                        border-radius: 12px;
                        margin-bottom: 16px;
                        padding: 16px 18px;
                    }}
                    .article-card.empty {{
                        background: #F8F3EC;
                    }}
                    .article-kicker {{
                        color: #8A6D4B;
                        font-size: 11px;
                        font-weight: 700;
                        letter-spacing: 0.08em;
                        margin-bottom: 8px;
                    }}
                    .article-card h2 {{
                        font-size: 20px;
                        line-height: 1.35;
                        margin: 0 0 12px 0;
                    }}
                    .article-body p {{
                        font-size: 13px;
                        line-height: 1.65;
                        margin: 0 0 10px 0;
                    }}
                    .image-grid {{
                        margin: 0 0 14px 0;
                    }}
                    .image-grid img {{
                        display: block;
                        width: 100%;
                        max-width: 460px;
                        height: auto;
                        border-radius: 8px;
                        border: 1px solid #DED3C3;
                        margin-bottom: 10px;
                    }}
                    .image-label {{
                        color: #6A5D50;
                        font-size: 11px;
                        margin: -2px 0 12px 0;
                    }}
                    .image-placeholder {{
                        display: flex;
                        align-items: center;
                        justify-content: center;
                        width: 100%;
                        max-width: 460px;
                        min-height: 160px;
                        margin-bottom: 10px;
                        border-radius: 8px;
                        border: 1px dashed #CBBEAC;
                        background: #F8F3EC;
                        color: #6A5D50;
                        font-size: 12px;
                    }}
                    .image-caption {{
                        color: #2D2419;
                        font-size: 12px;
                        line-height: 1.55;
                        margin: -4px 0 12px 0;
                    }}
                </style>
            </head>
            <body>
                <div class="page-header">
                    <h1>{page_title}</h1>
                    <div class="page-meta">Page {page_number} | Overlay {overlay_type} | Articles {len(articles)}</div>
                </div>
                {"".join(cards)}
            </body>
        </html>
        """

    def _build_article_card(self, index: int, article: dict[str, Any], image_map: dict[str, bytes]) -> str:
        title = html.escape(str(article.get("title") or f"Article {index}"))
        body = str(article.get("body_text") or "").strip()
        paragraphs = [segment.strip() for segment in body.replace("\r", "").split("\n") if segment.strip()]
        if not paragraphs and body:
            paragraphs = [body]

        image_html: list[str] = []
        for image_index, image in enumerate(article.get("images", []), start=1):
            image_url = str(image.get("image_url") or "").strip()
            image_bytes = image_map.get(image_url)
            caption_html = "".join(
                f'<div class="image-caption">{html.escape(str(caption.get("text") or "").strip())}</div>'
                for caption in image.get("captions", [])
                if str(caption.get("text") or "").strip()
            )
            if image_bytes:
                encoded = base64.b64encode(image_bytes).decode("ascii")
                visual_html = f'<img src="data:image/png;base64,{encoded}" alt="article image {image_index}">'
            else:
                visual_html = f'<div class="image-placeholder">Image {image_index} loading...</div>'
            image_html.append(
                f"""
                {visual_html}
                <div class="image-label">Image {image_index}</div>
                {caption_html}
                """
            )

        image_block = f'<div class="image-grid">{"".join(image_html)}</div>' if image_html else ""
        body_block = "".join(f"<p>{html.escape(paragraph)}</p>" for paragraph in paragraphs) or "<p>본문이 비어 있습니다.</p>"
        return f"""
        <section class="article-card">
            <div class="article-kicker">ARTICLE {index:02d}</div>
            <h2>{title}</h2>
            {image_block}
            <div class="article-body">{body_block}</div>
        </section>
        """

    def _run_task(
        self,
        fn: Callable[[], Any],
        on_result: Callable[[Any], None],
        lock_key: str,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        if lock_key in self.in_flight:
            return
        self.in_flight.add(lock_key)
        task = ApiTask(fn)

        def handle_result(result: Any) -> None:
            self.in_flight.discard(lock_key)
            on_result(result)

        def handle_error(message: str) -> None:
            self.in_flight.discard(lock_key)
            if on_error is not None:
                on_error(message)
            else:
                self.statusBar().showMessage(message, 6000)

        task.signals.result.connect(handle_result)
        task.signals.error.connect(handle_error)
        self.thread_pool.start(task)

    def _show_request_error(self, message: str) -> None:
        hint = message
        lowered = message.lower()
        if "404" in lowered and "/run-single" in lowered:
            hint = "단일 PDF 업로드 API가 없는 예전 컨테이너입니다. `docker compose up --build` 또는 API 재시작 후 다시 시도하세요."
        self.statusBar().showMessage(hint, 6000)
        QMessageBox.warning(self, "OCR Viewer", hint)

    def _show_status_error(self, message: str) -> None:
        hint = message
        lowered = message.lower()
        if "404" in lowered and "/detail" in lowered:
            hint = "API 컨테이너가 예전 코드로 떠 있습니다. `docker compose up --build` 또는 API 재시작 후 다시 시도하세요."
        elif "404" in lowered and "/preview" in lowered:
            hint = "preview API가 없는 예전 컨테이너입니다. API 컨테이너를 재빌드/재시작하세요."
        self.statusBar().showMessage(hint, 8000)

    def _show_warning(self, message: str) -> None:
        QMessageBox.warning(self, "OCR Viewer", message)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("A-Congress OCR Viewer")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
