from __future__ import annotations

import base64
import copy
import html
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QRectF, Qt, QProcess, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QKeySequence, QPainter, QPen, QPixmap, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app.services.labeling_dataset import (
    ArticleRecord,
    LABEL_STATUSES,
    build_default_annotation,
    discover_article_records,
    export_fine_tuning_dataset,
    load_annotation,
    normalize_reviewer_name,
    save_annotation,
)

STATUS_LABELS = {"pending": "대기", "accepted": "채택", "needs_review": "재검토", "rejected": "제외"}
STATUS_COLORS = {"pending": "#6B7280", "accepted": "#11835B", "needs_review": "#C37B14", "rejected": "#C0392B"}
EDIT_MODE_LABELS = {"select": "선택", "draw_article": "본문 박스 추가", "draw_title": "제목 박스 추가", "add_image": "이미지 박스 추가"}
BOX_KIND_LABELS = {"title": "제목", "article": "본문", "image": "이미지"}
BOX_SHORT = {"title": "T", "article": "A", "image": "I"}
BOX_COLORS = {"article": QColor("#00B894"), "title": QColor("#FDCB6E"), "image": QColor("#0984E3")}
BOX_MIN_SIZE = 4
BOX_HANDLE_NAMES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")
BOX_CURSOR_MAP = {
    "move": Qt.CursorShape.SizeAllCursor,
    "n": Qt.CursorShape.SizeVerCursor,
    "s": Qt.CursorShape.SizeVerCursor,
    "e": Qt.CursorShape.SizeHorCursor,
    "w": Qt.CursorShape.SizeHorCursor,
    "ne": Qt.CursorShape.SizeBDiagCursor,
    "sw": Qt.CursorShape.SizeBDiagCursor,
    "nw": Qt.CursorShape.SizeFDiagCursor,
    "se": Qt.CursorShape.SizeFDiagCursor,
}


def _bbox(raw: Any) -> list[int] | None:
    return [int(raw[0]), int(raw[1]), int(raw[2]), int(raw[3])] if isinstance(raw, list) and len(raw) == 4 else None


def _regions(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    result: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict):
            result.append({"bbox": _bbox(item.get("bbox")), "text": str(item.get("text") or "")})
    return result


def _boxes(raw: Any) -> list[list[int]]:
    return [box for box in (_bbox(item) for item in raw or []) if box is not None]


def _region_bbox(region: dict[str, Any]) -> list[int] | None:
    return _bbox(region.get("bbox"))


def _region_text(region: dict[str, Any]) -> str:
    return str(region.get("text") or "")


def _compose(regions: list[dict[str, Any]], separator: str) -> str:
    return separator.join([text for text in (_region_text(region).strip() for region in regions) if text]).strip()


def _bbox_label(box: list[int] | None) -> str:
    return "bbox 없음" if box is None else f"{box[0]},{box[1]}-{box[2]},{box[3]}"


def _preview(value: str, max_len: int = 50) -> str:
    text = " ".join((value or "").split())
    if not text:
        return "텍스트 없음"
    return text if len(text) <= max_len else f"{text[: max_len - 1]}..."


def _bbox_union(boxes: list[list[int] | None]) -> list[int] | None:
    valid = [box for box in boxes if isinstance(box, list) and len(box) == 4]
    if not valid:
        return None
    return [
        min(box[0] for box in valid),
        min(box[1] for box in valid),
        max(box[2] for box in valid),
        max(box[3] for box in valid),
    ]


class RecordPreviewWidget(QWidget):
    bboxesChanged = pyqtSignal(object)
    selectionChanged = pyqtSignal(object)
    zoomChanged = pyqtSignal(float)
    historyChanged = pyqtSignal(bool, bool)

    def __init__(self):
        super().__init__()
        self.setMinimumSize(720, 420)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMouseTracking(True)
        self._pixmap = QPixmap()
        self._page_size = (1, 1)
        self._title_regions: list[dict[str, Any]] = []
        self._article_regions: list[dict[str, Any]] = []
        self._image_bboxes: list[list[int]] = []
        self._edit_mode = "select"
        self._selected_kind: str | None = None
        self._selected_index: int | None = None
        self._draw_start: tuple[int, int] | None = None
        self._draw_current: tuple[int, int] | None = None
        self._dragging = False
        self._box_drag_mode: str | None = None
        self._box_drag_handle: str | None = None
        self._box_drag_start: tuple[int, int] | None = None
        self._box_drag_origin: list[int] | None = None
        self._box_drag_snapshot: dict[str, Any] | None = None
        self._panning = False
        self._last_pan: tuple[float, float] | None = None
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._undo_stack: list[dict[str, Any]] = []
        self._redo_stack: list[dict[str, Any]] = []

    def set_edit_mode(self, mode: str):
        self._edit_mode = mode if mode in EDIT_MODE_LABELS else "select"
        self._update_cursor_shape()
        self.update()

    def set_record(self, record: ArticleRecord, annotation: dict[str, Any]):
        self._pixmap = QPixmap(str(record.page_image_path))
        self._page_size = (max(self._pixmap.width(), 1), max(self._pixmap.height(), 1))
        self._title_regions = _regions(annotation.get("title_regions"))
        self._article_regions = _regions(annotation.get("article_regions"))
        self._image_bboxes = _boxes(annotation.get("image_bboxes"))
        self._selected_kind = None
        self._selected_index = None
        self._dragging = False
        self._box_drag_mode = None
        self._box_drag_handle = None
        self._box_drag_start = None
        self._box_drag_origin = None
        self._box_drag_snapshot = None
        self._panning = False
        self._last_pan = None
        self.clear_history()
        self.set_fit_view()
        self.selectionChanged.emit(self._selection_payload())

    def clear(self):
        self._pixmap = QPixmap()
        self._page_size = (1, 1)
        self._title_regions = []
        self._article_regions = []
        self._image_bboxes = []
        self._selected_kind = None
        self._selected_index = None
        self._dragging = False
        self._box_drag_mode = None
        self._box_drag_handle = None
        self._box_drag_start = None
        self._box_drag_origin = None
        self._box_drag_snapshot = None
        self._panning = False
        self._last_pan = None
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.clear_history()
        self.zoomChanged.emit(100.0)
        self.selectionChanged.emit(self._selection_payload())
        self.update()

    def set_regions(self, *, title_regions: list[dict[str, Any]], article_regions: list[dict[str, Any]], image_bboxes: list[list[int]], reset_history: bool = False):
        self._title_regions = _regions(title_regions)
        self._article_regions = _regions(article_regions)
        self._image_bboxes = _boxes(image_bboxes)
        self._selected_kind = None
        self._selected_index = None
        self._dragging = False
        self._box_drag_mode = None
        self._box_drag_handle = None
        self._box_drag_start = None
        self._box_drag_origin = None
        self._box_drag_snapshot = None
        if reset_history:
            self.clear_history()
        self.selectionChanged.emit(self._selection_payload())
        self._update_cursor_shape()
        self.update()

    def set_selected_region(self, kind: str | None, index: int | None):
        valid = (
            kind == "title"
            and index is not None
            and 0 <= index < len(self._title_regions)
            or kind == "article"
            and index is not None
            and 0 <= index < len(self._article_regions)
            or kind == "image"
            and index is not None
            and 0 <= index < len(self._image_bboxes)
        )
        self._selected_kind = kind if valid else None
        self._selected_index = index if valid else None
        self._update_cursor_shape()
        self.update()

    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    def clear_history(self):
        self._undo_stack = []
        self._redo_stack = []
        self.historyChanged.emit(False, False)

    def push_undo_state(self):
        self._push_undo_snapshot(self._snapshot())

    def undo(self):
        if not self._undo_stack:
            return
        snapshot = self._snapshot()
        target = self._undo_stack.pop()
        self._redo_stack.append(snapshot)
        self._restore_snapshot(target)
        self.historyChanged.emit(self.can_undo(), self.can_redo())

    def redo(self):
        if not self._redo_stack:
            return
        snapshot = self._snapshot()
        target = self._redo_stack.pop()
        self._undo_stack.append(snapshot)
        self._restore_snapshot(target)
        self.historyChanged.emit(self.can_undo(), self.can_redo())

    def set_fit_view(self):
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self.zoomChanged.emit(self._zoom_percent())
        self.update()

    def set_actual_size(self):
        fit = self._fit_scale()
        if fit > 0:
            self._set_zoom(1.0 / fit)

    def zoom_in(self):
        self._zoom_at((self.width() / 2, self.height() / 2), 1.2)

    def zoom_out(self):
        self._zoom_at((self.width() / 2, self.height() / 2), 1 / 1.2)

    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#F7F3ED"))
        if self._pixmap.isNull():
            painter.setPen(QColor("#5A5146"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "기사 레코드를 선택하면 미리보기가 표시됩니다.")
            return
        rect = self._image_rect()
        painter.drawPixmap(rect, self._pixmap, QRectF(0, 0, self._page_size[0], self._page_size[1]))
        sx = rect.width() / self._page_size[0]
        sy = rect.height() / self._page_size[1]
        for idx, region in enumerate(self._article_regions, start=1):
            self._draw_box(painter, _region_bbox(region), rect, sx, sy, BOX_COLORS["article"], f"{BOX_SHORT['article']}{idx}", self._selected_kind == "article" and self._selected_index == idx - 1)
        for idx, region in enumerate(self._title_regions, start=1):
            self._draw_box(painter, _region_bbox(region), rect, sx, sy, BOX_COLORS["title"], f"{BOX_SHORT['title']}{idx}", self._selected_kind == "title" and self._selected_index == idx - 1)
        for idx, box in enumerate(self._image_bboxes, start=1):
            self._draw_box(painter, box, rect, sx, sy, BOX_COLORS["image"], f"{BOX_SHORT['image']}{idx}", self._selected_kind == "image" and self._selected_index == idx - 1)
        if self._dragging and self._draw_start and self._draw_current:
            color = BOX_COLORS["image"] if self._edit_mode == "add_image" else BOX_COLORS["title"] if self._edit_mode == "draw_title" else BOX_COLORS["article"]
            self._draw_box(painter, self._normalize_bbox(self._draw_start, self._draw_current), rect, sx, sy, color, EDIT_MODE_LABELS.get(self._edit_mode, self._edit_mode), False, True)
        self._draw_selected_handles(painter, rect, sx, sy)
        painter.setPen(QColor("#3B342C"))
        painter.drawText(12, 24, f"Zoom {self._zoom_percent():.0f}% | Title {len(self._title_regions)} | Body {len(self._article_regions)} | Image {len(self._image_bboxes)}")

    def wheelEvent(self, event):  # type: ignore[override]
        if not self._pixmap.isNull():
            self._zoom_at((event.position().x(), event.position().y()), 1.15 if event.angleDelta().y() > 0 else 1 / 1.15)
            event.accept()

    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.set_fit_view()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):  # type: ignore[override]
        if self._pixmap.isNull():
            return
        if event.button() in {Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton}:
            self._panning = True
            self._last_pan = (event.position().x(), event.position().y())
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = self._widget_to_image(event.position().x(), event.position().y())
        if point is None:
            return
        if self._edit_mode == "select":
            picked = self._pick_box(point)
            if picked is not None:
                self._selected_kind, self._selected_index = picked
                action = self._hit_test_selected_box(point)
                if action is not None:
                    self._start_box_drag(point, action)
            else:
                self._selected_kind, self._selected_index = None, None
            self.selectionChanged.emit(self._selection_payload(focus_editor=self._box_drag_mode is None and self._selected_kind in {"title", "article"}))
            self._update_cursor_shape()
            self.update()
            return
        self._dragging = True
        self._draw_start = point
        self._draw_current = point
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.update()

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._panning and self._last_pan is not None:
            cur = (event.position().x(), event.position().y())
            self._pan_x += cur[0] - self._last_pan[0]
            self._pan_y += cur[1] - self._last_pan[1]
            self._last_pan = cur
            self._clamp_pan()
            self.update()
            return
        point = self._widget_to_image(event.position().x(), event.position().y(), clamp=True)
        if self._box_drag_mode and point is not None:
            self._update_box_drag(point)
            self.update()
            return
        if not self._dragging:
            self._update_cursor_shape((event.position().x(), event.position().y()))
            return
        if point is not None:
            self._draw_current = point
            self.update()

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() in {Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton}:
            self._panning = False
            self._last_pan = None
            self._update_cursor_shape((event.position().x(), event.position().y()))
            return
        if self._box_drag_mode:
            self._finish_box_drag()
            self._update_cursor_shape((event.position().x(), event.position().y()))
            return
        if not self._dragging:
            return
        point = self._widget_to_image(event.position().x(), event.position().y(), clamp=True) or self._draw_current
        self._dragging = False
        if self._draw_start is None or point is None:
            self._update_cursor_shape((event.position().x(), event.position().y()))
            self.update()
            return
        box = self._normalize_bbox(self._draw_start, point)
        self._draw_start = None
        self._draw_current = None
        if box[2] - box[0] < BOX_MIN_SIZE or box[3] - box[1] < BOX_MIN_SIZE:
            self._update_cursor_shape((event.position().x(), event.position().y()))
            self.update()
            return
        self._push_undo_snapshot(self._snapshot())
        if self._edit_mode == "draw_title":
            self._title_regions.append({"bbox": box, "text": ""})
            self._selected_kind, self._selected_index = "title", len(self._title_regions) - 1
        elif self._edit_mode == "draw_article":
            self._article_regions.append({"bbox": box, "text": ""})
            self._selected_kind, self._selected_index = "article", len(self._article_regions) - 1
        elif self._edit_mode == "add_image":
            self._image_bboxes.append(box)
            self._selected_kind, self._selected_index = "image", len(self._image_bboxes) - 1
        else:
            self._update_cursor_shape((event.position().x(), event.position().y()))
            self.update()
            return
        self._emit_change()
        self.selectionChanged.emit(self._selection_payload())
        self._update_cursor_shape((event.position().x(), event.position().y()))
        self.update()

    def leaveEvent(self, event):  # type: ignore[override]
        if not self._panning and not self._dragging and not self._box_drag_mode:
            self._update_cursor_shape()
        super().leaveEvent(event)

    def clear_selected(self):
        snapshot = self._snapshot()
        if self._selected_kind == "title" and self._selected_index is not None and 0 <= self._selected_index < len(self._title_regions):
            self._title_regions.pop(self._selected_index)
        elif self._selected_kind == "article" and self._selected_index is not None and 0 <= self._selected_index < len(self._article_regions):
            self._article_regions.pop(self._selected_index)
        elif self._selected_kind == "image" and self._selected_index is not None and 0 <= self._selected_index < len(self._image_bboxes):
            self._image_bboxes.pop(self._selected_index)
        else:
            return
        self._push_undo_snapshot(snapshot)
        self._selected_kind = None
        self._selected_index = None
        self._emit_change()
        self.selectionChanged.emit(self._selection_payload())
        self._update_cursor_shape()
        self.update()

    def clear_all_images(self):
        if not self._image_bboxes:
            return
        self._push_undo_snapshot(self._snapshot())
        self._image_bboxes = []
        if self._selected_kind == "image":
            self._selected_kind = None
            self._selected_index = None
        self._emit_change()
        self.selectionChanged.emit(self._selection_payload())
        self._update_cursor_shape()
        self.update()

    def _draw_box(self, painter: QPainter, box: list[int] | None, rect: QRectF, sx: float, sy: float, color: QColor, label: str, selected: bool, dashed: bool = False):
        if not box:
            return
        x0, y0 = rect.left() + box[0] * sx, rect.top() + box[1] * sy
        x1, y1 = rect.left() + box[2] * sx, rect.top() + box[3] * sy
        pen = QPen(color, 3 if selected else 2)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0)))
        th = painter.fontMetrics().height() + 4
        tw = painter.fontMetrics().horizontalAdvance(label) + 10
        painter.fillRect(QRectF(x0, max(0.0, y0 - th), tw, th), QColor(color.red(), color.green(), color.blue(), 200))
        painter.setPen(QColor("#FFFFFF"))
        painter.drawText(QRectF(x0 + 4, max(0.0, y0 - th), tw, th), Qt.AlignmentFlag.AlignVCenter, label)

    def _draw_selected_handles(self, painter: QPainter, rect: QRectF, sx: float, sy: float):
        box = self._selected_box()
        if not box:
            return
        color = self._selected_color()
        if color is None:
            return
        handle_size = max(6.0, min(12.0, self._current_scale() * 0.14))
        painter.setPen(QPen(QColor("#FFFFFF"), 1))
        painter.setBrush(color)
        for cx, cy in self._handle_positions(box, rect, sx, sy).values():
            painter.drawRect(QRectF(cx - handle_size / 2, cy - handle_size / 2, handle_size, handle_size))

    def _fit_scale(self) -> float:
        return 1.0 if self._pixmap.isNull() else min(self.width() / max(self._page_size[0], 1), self.height() / max(self._page_size[1], 1))

    def _current_scale(self) -> float:
        return self._fit_scale() * self._zoom

    def _zoom_percent(self) -> float:
        return self._current_scale() * 100.0

    def _image_rect(self) -> QRectF:
        scale = self._current_scale()
        w, h = self._page_size[0] * scale, self._page_size[1] * scale
        return QRectF((self.width() - w) / 2 + self._pan_x, (self.height() - h) / 2 + self._pan_y, w, h)

    def _zoom_at(self, anchor: tuple[float, float], factor: float):
        self._set_zoom(self._zoom * factor, anchor=anchor)

    def _set_zoom(self, value: float, anchor: tuple[float, float] | None = None):
        fit = self._fit_scale()
        if fit <= 0:
            return
        new_zoom = max(1.0, min(value, max(8.0, (1.0 / fit) * 6.0)))
        if abs(new_zoom - self._zoom) < 0.001:
            return
        image_point = self._widget_to_image(anchor[0], anchor[1], clamp=True) if anchor else None
        self._zoom = new_zoom
        if image_point is not None:
            scale = self._current_scale()
            w, h = self._page_size[0] * scale, self._page_size[1] * scale
            cx, cy = (self.width() - w) / 2, (self.height() - h) / 2
            self._pan_x = anchor[0] - image_point[0] * scale - cx
            self._pan_y = anchor[1] - image_point[1] * scale - cy
        self._clamp_pan()
        self.zoomChanged.emit(self._zoom_percent())
        self.update()

    def _clamp_pan(self):
        if self._zoom <= 1.0:
            self._pan_x = 0.0
            self._pan_y = 0.0
            return
        rect = self._image_rect()
        margin = 80.0
        cl = max(margin - rect.width(), min(self.width() - margin, rect.left()))
        ct = max(margin - rect.height(), min(self.height() - margin, rect.top()))
        self._pan_x = cl - (self.width() - rect.width()) / 2
        self._pan_y = ct - (self.height() - rect.height()) / 2

    def _widget_to_image(self, x: float, y: float, clamp: bool = False) -> tuple[int, int] | None:
        if self._pixmap.isNull():
            return None
        rect = self._image_rect()
        if rect.width() <= 0 or rect.height() <= 0:
            return None
        if not clamp and not rect.contains(x, y):
            return None
        cx = min(max(x, rect.left()), rect.right())
        cy = min(max(y, rect.top()), rect.bottom())
        ix = int(((cx - rect.left()) / rect.width()) * self._page_size[0])
        iy = int(((cy - rect.top()) / rect.height()) * self._page_size[1])
        return max(0, min(self._page_size[0] - 1, ix)), max(0, min(self._page_size[1] - 1, iy))

    def _pick_box(self, point: tuple[int, int]) -> tuple[str, int] | None:
        x, y = point
        for idx in range(len(self._image_bboxes) - 1, -1, -1):
            if self._contains(self._image_bboxes[idx], x, y):
                return "image", idx
        for idx in range(len(self._title_regions) - 1, -1, -1):
            if self._contains(_region_bbox(self._title_regions[idx]), x, y):
                return "title", idx
        for idx in range(len(self._article_regions) - 1, -1, -1):
            if self._contains(_region_bbox(self._article_regions[idx]), x, y):
                return "article", idx
        return None

    @staticmethod
    def _normalize_bbox(start: tuple[int, int], end: tuple[int, int]) -> list[int]:
        return [min(start[0], end[0]), min(start[1], end[1]), max(start[0], end[0]), max(start[1], end[1])]

    @staticmethod
    def _contains(box: list[int] | None, x: int, y: int) -> bool:
        return bool(box and box[0] <= x <= box[2] and box[1] <= y <= box[3])

    def _emit_change(self):
        self.bboxesChanged.emit({"title_regions": _regions(self._title_regions), "article_regions": _regions(self._article_regions), "image_bboxes": _boxes(self._image_bboxes)})

    def _snapshot(self) -> dict[str, Any]:
        return {
            "title_regions": _regions(self._title_regions),
            "article_regions": _regions(self._article_regions),
            "image_bboxes": _boxes(self._image_bboxes),
            "selected_kind": self._selected_kind,
            "selected_index": self._selected_index,
        }

    def _restore_snapshot(self, snapshot: dict[str, Any]):
        self._title_regions = _regions(snapshot.get("title_regions"))
        self._article_regions = _regions(snapshot.get("article_regions"))
        self._image_bboxes = _boxes(snapshot.get("image_bboxes"))
        self._selected_kind = snapshot.get("selected_kind") if isinstance(snapshot.get("selected_kind"), str) else None
        self._selected_index = snapshot.get("selected_index") if isinstance(snapshot.get("selected_index"), int) else None
        if self._selected_box() is None:
            self._selected_kind = None
            self._selected_index = None
        self._emit_change()
        self.selectionChanged.emit(self._selection_payload())
        self._update_cursor_shape()
        self.update()

    def _push_undo_snapshot(self, snapshot: dict[str, Any]):
        if self._undo_stack and self._undo_stack[-1] == snapshot:
            return
        self._undo_stack.append(copy.deepcopy(snapshot))
        if len(self._undo_stack) > 100:
            self._undo_stack.pop(0)
        self._redo_stack = []
        self.historyChanged.emit(self.can_undo(), self.can_redo())

    def _selected_color(self) -> QColor | None:
        if self._selected_kind == "title":
            return BOX_COLORS["title"]
        if self._selected_kind == "article":
            return BOX_COLORS["article"]
        if self._selected_kind == "image":
            return BOX_COLORS["image"]
        return None

    def _selected_box(self) -> list[int] | None:
        if self._selected_kind == "title" and self._selected_index is not None and 0 <= self._selected_index < len(self._title_regions):
            return _region_bbox(self._title_regions[self._selected_index])
        if self._selected_kind == "article" and self._selected_index is not None and 0 <= self._selected_index < len(self._article_regions):
            return _region_bbox(self._article_regions[self._selected_index])
        if self._selected_kind == "image" and self._selected_index is not None and 0 <= self._selected_index < len(self._image_bboxes):
            return _bbox(self._image_bboxes[self._selected_index])
        return None

    def _set_selected_box(self, box: list[int]):
        if self._selected_kind == "title" and self._selected_index is not None and 0 <= self._selected_index < len(self._title_regions):
            self._title_regions[self._selected_index]["bbox"] = box
        elif self._selected_kind == "article" and self._selected_index is not None and 0 <= self._selected_index < len(self._article_regions):
            self._article_regions[self._selected_index]["bbox"] = box
        elif self._selected_kind == "image" and self._selected_index is not None and 0 <= self._selected_index < len(self._image_bboxes):
            self._image_bboxes[self._selected_index] = box

    def _handle_positions(self, box: list[int], rect: QRectF, sx: float, sy: float) -> dict[str, tuple[float, float]]:
        x0, y0 = rect.left() + box[0] * sx, rect.top() + box[1] * sy
        x1, y1 = rect.left() + box[2] * sx, rect.top() + box[3] * sy
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {
            "nw": (x0, y0),
            "n": (mx, y0),
            "ne": (x1, y0),
            "e": (x1, my),
            "se": (x1, y1),
            "s": (mx, y1),
            "sw": (x0, y1),
            "w": (x0, my),
        }

    def _hit_test_selected_box(self, point: tuple[int, int]) -> str | None:
        box = self._selected_box()
        if box is None:
            return None
        x, y = point
        tolerance = max(3, int(round(10 / max(self._current_scale(), 0.25))))
        x0, y0, x1, y1 = box
        corners = {
            "nw": (x0, y0),
            "n": ((x0 + x1) // 2, y0),
            "ne": (x1, y0),
            "e": (x1, (y0 + y1) // 2),
            "se": (x1, y1),
            "s": ((x0 + x1) // 2, y1),
            "sw": (x0, y1),
            "w": (x0, (y0 + y1) // 2),
        }
        for name in BOX_HANDLE_NAMES:
            hx, hy = corners[name]
            if abs(x - hx) <= tolerance and abs(y - hy) <= tolerance:
                return name
        if self._contains(box, x, y):
            return "move"
        return None

    def _start_box_drag(self, point: tuple[int, int], action: str):
        box = self._selected_box()
        if box is None:
            return
        self._box_drag_mode = "move" if action == "move" else "resize"
        self._box_drag_handle = None if action == "move" else action
        self._box_drag_start = point
        self._box_drag_origin = list(box)
        self._box_drag_snapshot = self._snapshot()

    def _update_box_drag(self, point: tuple[int, int]):
        if self._box_drag_start is None or self._box_drag_origin is None:
            return
        if self._box_drag_mode == "move":
            next_box = self._move_box(self._box_drag_origin, point[0] - self._box_drag_start[0], point[1] - self._box_drag_start[1])
        elif self._box_drag_mode == "resize" and self._box_drag_handle:
            next_box = self._resize_box(self._box_drag_origin, self._box_drag_handle, point)
        else:
            return
        self._set_selected_box(next_box)

    def _finish_box_drag(self):
        before = self._box_drag_snapshot
        after = self._snapshot()
        changed = before is not None and before != after
        self._box_drag_mode = None
        self._box_drag_handle = None
        self._box_drag_start = None
        self._box_drag_origin = None
        self._box_drag_snapshot = None
        if changed and before is not None:
            self._push_undo_snapshot(before)
            self._emit_change()
        self.selectionChanged.emit(self._selection_payload())
        self.update()

    def _move_box(self, box: list[int], dx: int, dy: int) -> list[int]:
        x0, y0, x1, y1 = box
        width = x1 - x0
        height = y1 - y0
        max_x = self._page_size[0] - 1
        max_y = self._page_size[1] - 1
        nx0 = min(max(0, x0 + dx), max(0, max_x - width))
        ny0 = min(max(0, y0 + dy), max(0, max_y - height))
        return [nx0, ny0, nx0 + width, ny0 + height]

    def _resize_box(self, box: list[int], handle: str, point: tuple[int, int]) -> list[int]:
        x0, y0, x1, y1 = box
        px = min(max(0, point[0]), self._page_size[0] - 1)
        py = min(max(0, point[1]), self._page_size[1] - 1)
        min_x1 = x0 + BOX_MIN_SIZE
        min_y1 = y0 + BOX_MIN_SIZE
        max_x0 = x1 - BOX_MIN_SIZE
        max_y0 = y1 - BOX_MIN_SIZE
        if "w" in handle:
            x0 = min(max(0, px), max_x0)
        if "e" in handle:
            x1 = max(min(self._page_size[0] - 1, px), min_x1)
        if "n" in handle:
            y0 = min(max(0, py), max_y0)
        if "s" in handle:
            y1 = max(min(self._page_size[1] - 1, py), min_y1)
        return [x0, y0, x1, y1]

    def _update_cursor_shape(self, widget_point: tuple[float, float] | None = None):
        if self._panning:
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            return
        if self._dragging:
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._box_drag_mode:
            self.setCursor(BOX_CURSOR_MAP.get(self._box_drag_handle or "move", Qt.CursorShape.SizeAllCursor))
            return
        if self._edit_mode != "select":
            self.setCursor(Qt.CursorShape.CrossCursor)
            return
        if widget_point is None:
            self.unsetCursor()
            return
        point = self._widget_to_image(widget_point[0], widget_point[1])
        if point is None:
            self.unsetCursor()
            return
        picked = self._pick_box(point)
        if picked is not None:
            kind, index = picked
            previous_kind, previous_index = self._selected_kind, self._selected_index
            self._selected_kind, self._selected_index = kind, index
            action = self._hit_test_selected_box(point) or "move"
            self._selected_kind, self._selected_index = previous_kind, previous_index
            self.setCursor(BOX_CURSOR_MAP.get(action, Qt.CursorShape.ArrowCursor))
            return
        self.unsetCursor()

    def _selection_payload(self, *, focus_editor: bool = False) -> dict[str, Any]:
        if self._selected_kind is None or self._selected_index is None:
            return {"kind": None, "index": None, "label": "선택 없음", "focus_editor": False}
        box, text = None, ""
        if self._selected_kind == "title" and 0 <= self._selected_index < len(self._title_regions):
            box, text = _region_bbox(self._title_regions[self._selected_index]), _region_text(self._title_regions[self._selected_index])
        elif self._selected_kind == "article" and 0 <= self._selected_index < len(self._article_regions):
            box, text = _region_bbox(self._article_regions[self._selected_index]), _region_text(self._article_regions[self._selected_index])
        elif self._selected_kind == "image" and 0 <= self._selected_index < len(self._image_bboxes):
            box = _bbox(self._image_bboxes[self._selected_index])
        return {
            "kind": self._selected_kind,
            "index": self._selected_index,
            "bbox": box,
            "text": text,
            "focus_editor": focus_editor,
            "label": f"선택: {BOX_KIND_LABELS[self._selected_kind]} {self._selected_index + 1} | {_bbox_label(box)}",
        }


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Army-OCR Labeling Tool")
        self.resize(1680, 1020)
        self.records: list[ArticleRecord] = []
        self.current_index: int | None = None
        self.current_annotation: dict[str, Any] | None = None
        self.last_export_dir: Path | None = None
        self.process: QProcess | None = None
        self._shortcuts: list[QShortcut] = []
        self._syncing_region_ui = False
        self._active_kind: str | None = None
        self._active_index: int | None = None
        self._build_ui()
        self._apply_style()
        self._install_shortcuts()
        self._load_initial_paths()
        self.reload_records()

    def _build_ui(self):
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)
        self.setCentralWidget(central)

        toolbar = QFrame()
        toolbar.setObjectName("panel")
        tl = QVBoxLayout(toolbar)
        tl.setContentsMargins(14, 14, 14, 14)
        tl.setSpacing(8)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Output Root"))
        self.output_root_input = QLineEdit()
        row1.addWidget(self.output_root_input, 3)
        btn = QPushButton("선택")
        btn.clicked.connect(self.browse_output_root)
        row1.addWidget(btn)
        row1.addWidget(QLabel("Reviewer"))
        self.reviewer_input = QLineEdit("reviewer01")
        row1.addWidget(self.reviewer_input, 1)
        btn = QPushButton("리로드")
        btn.clicked.connect(self.reload_records)
        row1.addWidget(btn)
        btn = QPushButton("다음 미라벨")
        btn.clicked.connect(self.select_next_unlabeled)
        row1.addWidget(btn)
        tl.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Label Root"))
        self.label_root_input = QLineEdit()
        row2.addWidget(self.label_root_input, 2)
        btn = QPushButton("선택")
        btn.clicked.connect(self.browse_label_root)
        row2.addWidget(btn)
        row2.addWidget(QLabel("Export Root"))
        self.export_root_input = QLineEdit()
        row2.addWidget(self.export_root_input, 2)
        btn = QPushButton("선택")
        btn.clicked.connect(self.browse_export_root)
        row2.addWidget(btn)
        btn = QPushButton("데이터셋 Export")
        btn.clicked.connect(self.export_dataset)
        row2.addWidget(btn)
        tl.addLayout(row2)

        self.summary_label = QLabel("레코드 0건")
        tl.addWidget(self.summary_label)
        root.addWidget(toolbar)

        main = QSplitter(Qt.Orientation.Horizontal)
        main.setChildrenCollapsible(False)
        root.addWidget(main, 1)
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Job / PDF / Page / Article", "상태"])
        self.tree.setUniformRowHeights(True)
        self.tree.setAlternatingRowColors(True)
        self.tree.setIndentation(18)
        self.tree.setMinimumWidth(420)
        self.tree.itemSelectionChanged.connect(self.on_tree_selected)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        main.addWidget(self.tree)

        right = QSplitter(Qt.Orientation.Vertical)
        right.setChildrenCollapsible(False)
        main.addWidget(right)
        main.setSizes([460, 1180])

        preview_frame = QFrame()
        preview_frame.setObjectName("panel")
        pl = QVBoxLayout(preview_frame)
        pl.setContentsMargins(10, 10, 10, 10)
        pl.setSpacing(8)
        self.preview_label = QLabel("선택된 기사 없음")
        pl.addWidget(self.preview_label)

        row = QHBoxLayout()
        row.addWidget(QLabel("BBox Edit"))
        self.bbox_mode_combo = QComboBox()
        for value, label in EDIT_MODE_LABELS.items():
            self.bbox_mode_combo.addItem(label, value)
        self.bbox_mode_combo.currentIndexChanged.connect(self._change_bbox_mode)
        row.addWidget(self.bbox_mode_combo)
        btn = QPushButton("선택 삭제")
        btn.clicked.connect(self._delete_selected_bbox)
        row.addWidget(btn)
        self.undo_button = QPushButton("실행 취소")
        self.undo_button.clicked.connect(self._undo_bbox_change)
        self.undo_button.setEnabled(False)
        row.addWidget(self.undo_button)
        self.redo_button = QPushButton("다시 실행")
        self.redo_button.clicked.connect(self._redo_bbox_change)
        self.redo_button.setEnabled(False)
        row.addWidget(self.redo_button)
        btn = QPushButton("이미지 전체삭제")
        btn.clicked.connect(self._clear_all_image_bboxes)
        row.addWidget(btn)
        btn = QPushButton("OCR 박스로 복원")
        btn.clicked.connect(self._restore_current_bboxes_from_source)
        row.addWidget(btn)
        btn = QPushButton("박스/내용 비우기")
        btn.clicked.connect(self._clear_current_annotation)
        row.addWidget(btn)
        self.bbox_status_label = QLabel("선택 없음")
        row.addWidget(self.bbox_status_label, 1)
        pl.addLayout(row)

        row = QHBoxLayout()
        row.addWidget(QLabel("보기"))
        btn = QPushButton("-")
        btn.clicked.connect(self.preview_zoom_out)
        row.addWidget(btn)
        btn = QPushButton("+")
        btn.clicked.connect(self.preview_zoom_in)
        row.addWidget(btn)
        btn = QPushButton("맞춤")
        btn.clicked.connect(self.preview_fit)
        row.addWidget(btn)
        btn = QPushButton("100%")
        btn.clicked.connect(self.preview_actual)
        row.addWidget(btn)
        self.zoom_label = QLabel("100%")
        row.addWidget(self.zoom_label)
        row.addStretch(1)
        pl.addLayout(row)
        pl.addWidget(QLabel("왼쪽 클릭으로 선택/그리기, 선택 모드에서 드래그로 이동/리사이즈, 휠로 확대/축소, 우클릭 드래그로 이동, 더블클릭으로 맞춤"))
        pl.addWidget(QLabel("단축키: 1 제목, 2 본문, 3 이미지, 0 선택, Del 삭제, Ctrl+Z/Ctrl+Y 실행취소/다시실행, Ctrl+S 저장, N 다음 미라벨"))

        self.preview_widget = RecordPreviewWidget()
        self.preview_widget.bboxesChanged.connect(self._handle_preview_changed)
        self.preview_widget.selectionChanged.connect(self._handle_preview_selection)
        self.preview_widget.zoomChanged.connect(self._handle_zoom_changed)
        self.preview_widget.historyChanged.connect(self._update_history_buttons)
        pl.addWidget(self.preview_widget, 1)
        right.addWidget(preview_frame)

        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.setChildrenCollapsible(False)
        right.addWidget(bottom)
        right.setSizes([560, 460])

        source = QFrame()
        source.setObjectName("panel")
        sl = QVBoxLayout(source)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.setSpacing(8)
        sl.addWidget(QLabel("원본 OCR"))
        self.source_title_view = QPlainTextEdit()
        self.source_title_view.setReadOnly(True)
        sl.addWidget(self.source_title_view, 1)
        self.source_body_view = QPlainTextEdit()
        self.source_body_view.setReadOnly(True)
        sl.addWidget(self.source_body_view, 3)
        sl.addWidget(QLabel("기사 이미지"))
        self.image_browser = QTextBrowser()
        sl.addWidget(self.image_browser, 2)
        bottom.addWidget(source)

        edit = QFrame()
        edit.setObjectName("panel")
        el = QVBoxLayout(edit)
        el.setContentsMargins(10, 10, 10, 10)
        el.setSpacing(8)
        el.addWidget(QLabel("라벨 편집"))
        row = QHBoxLayout()
        row.addWidget(QLabel("상태"))
        self.status_combo = QComboBox()
        for value in LABEL_STATUSES:
            self.status_combo.addItem(STATUS_LABELS[value], value)
        row.addWidget(self.status_combo)
        row.addWidget(QLabel("태그"))
        self.tags_input = QLineEdit()
        row.addWidget(self.tags_input, 1)
        el.addLayout(row)

        rs = QSplitter(Qt.Orientation.Horizontal)
        tf = QFrame()
        tl = QVBoxLayout(tf)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(QLabel("제목 박스"))
        self.title_region_list = QListWidget()
        self.title_region_list.currentRowChanged.connect(lambda row: self._select_region_from_list("title", row))
        tl.addWidget(self.title_region_list, 1)
        rs.addWidget(tf)
        af = QFrame()
        al = QVBoxLayout(af)
        al.setContentsMargins(0, 0, 0, 0)
        al.addWidget(QLabel("본문 박스"))
        self.article_region_list = QListWidget()
        self.article_region_list.currentRowChanged.connect(lambda row: self._select_region_from_list("article", row))
        al.addWidget(self.article_region_list, 1)
        rs.addWidget(af)
        rs.setSizes([280, 320])
        el.addWidget(rs, 3)

        self.region_editor_label = QLabel("선택된 박스 없음")
        el.addWidget(self.region_editor_label)
        self.region_text_edit = QPlainTextEdit()
        self.region_text_edit.textChanged.connect(self._handle_region_text_changed)
        el.addWidget(self.region_text_edit, 2)
        self.auto_compose_checkbox = QCheckBox("박스 내용을 최종 제목/본문에 자동 합치기")
        self.auto_compose_checkbox.setChecked(True)
        self.auto_compose_checkbox.toggled.connect(self._toggle_auto_compose)
        el.addWidget(self.auto_compose_checkbox)
        el.addWidget(QLabel("최종 제목"))
        self.title_edit = QPlainTextEdit()
        el.addWidget(self.title_edit, 1)
        el.addWidget(QLabel("최종 본문"))
        self.body_edit = QPlainTextEdit()
        el.addWidget(self.body_edit, 3)
        el.addWidget(QLabel("노트"))
        self.notes_edit = QPlainTextEdit()
        el.addWidget(self.notes_edit, 1)

        row = QHBoxLayout()
        btn = QPushButton("OCR로 되돌리기")
        btn.clicked.connect(self.reset_to_source)
        row.addWidget(btn)
        btn = QPushButton("저장")
        btn.clicked.connect(self.save_current_annotation)
        row.addWidget(btn)
        el.addLayout(row)

        el.addWidget(QLabel("명령 실행"))
        row = QHBoxLayout()
        self.command_input = QLineEdit()
        row.addWidget(self.command_input, 1)
        btn = QPushButton("실행")
        btn.clicked.connect(self.run_command)
        row.addWidget(btn)
        btn = QPushButton("중단")
        btn.clicked.connect(self.stop_command)
        row.addWidget(btn)
        el.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(QLabel("Working Dir"))
        self.command_workdir_input = QLineEdit()
        row.addWidget(self.command_workdir_input, 1)
        btn = QPushButton("선택")
        btn.clicked.connect(self.browse_command_workdir)
        row.addWidget(btn)
        el.addLayout(row)
        self.command_log = QPlainTextEdit()
        self.command_log.setReadOnly(True)
        el.addWidget(self.command_log, 2)
        bottom.addWidget(edit)
        bottom.setSizes([620, 760])

        self.statusBar().showMessage("준비됨")
        self._update_final_text_edit_state()
        self._set_active_region(None, None, sync_preview=False)

    def _apply_style(self):
        self.setStyleSheet(
            """
            QWidget { background:#F5F1EA; color:#1F1F1F; font-family:"Malgun Gothic"; font-size:13px; }
            QFrame#panel, QTreeWidget, QListWidget, QPlainTextEdit, QTextBrowser, QLineEdit, QComboBox { background:#FFFDF9; border:1px solid #D8D0C2; border-radius:10px; }
            QLineEdit, QComboBox, QPlainTextEdit, QTextBrowser, QListWidget { padding:6px; }
            QHeaderView::section { background:#EFE6D9; border:none; border-bottom:1px solid #D8D0C2; padding:6px; font-weight:700; }
            QPushButton { background:#193A59; color:white; border:none; border-radius:8px; padding:8px 12px; font-weight:700; }
            QPushButton:hover { background:#234B70; }
            QTreeWidget::item, QListWidget::item { padding:4px 2px; }
            QTreeWidget::item:selected, QListWidget::item:selected { background:#DCEAF7; color:#1F1F1F; }
            """
        )

    def _load_initial_paths(self):
        cwd = Path.cwd()
        repo_root = Path(__file__).resolve().parents[2]
        candidates = [
            repo_root / "news_output",
            cwd / "news_output",
            cwd / "output",
        ]
        output_root = next((path for path in candidates if path.exists()), repo_root / "news_output")
        self.output_root_input.setText(str(output_root))
        self.label_root_input.setText(str(output_root / "_labels"))
        self.export_root_input.setText(str(output_root / "_fine_tuning_exports"))
        self.command_workdir_input.setText(str(Path.cwd()))

    def browse_output_root(self):
        if selected := QFileDialog.getExistingDirectory(self, "OCR output root 선택"):
            self.output_root_input.setText(selected)
            output_root = Path(selected)
            self.label_root_input.setText(str(output_root / "_labels"))
            self.export_root_input.setText(str(output_root / "_fine_tuning_exports"))

    def browse_label_root(self):
        if selected := QFileDialog.getExistingDirectory(self, "label root 선택"):
            self.label_root_input.setText(selected)

    def browse_export_root(self):
        if selected := QFileDialog.getExistingDirectory(self, "export root 선택"):
            self.export_root_input.setText(selected)

    def browse_command_workdir(self):
        if selected := QFileDialog.getExistingDirectory(self, "command working dir 선택"):
            self.command_workdir_input.setText(selected)

    def reload_records(self):
        output_root = self.output_root_path()
        if output_root is None:
            return
        self.records = discover_article_records(output_root)
        self._fill_tree()
        self.summary_label.setText(f"레코드 {len(self.records)}건 | reviewer={normalize_reviewer_name(self.reviewer_input.text())}")
        if self.records:
            self._select_record_index(0)
        else:
            self.current_index = None
            self.current_annotation = None
            self.preview_label.setText("선택된 기사 없음")
            self.preview_widget.clear()
            self.bbox_status_label.setText("선택 없음")
            self.source_title_view.clear()
            self.source_body_view.clear()
            self.image_browser.clear()
            self.title_edit.clear()
            self.body_edit.clear()
            self.notes_edit.clear()
            self.tags_input.clear()
            self.title_region_list.clear()
            self.article_region_list.clear()
            self._set_active_region(None, None, sync_preview=False)

    def _fill_tree(self):
        label_root = self.label_root_path()
        reviewer = normalize_reviewer_name(self.reviewer_input.text())
        self.tree.blockSignals(True)
        self.tree.clear()
        jobs: dict[str, QTreeWidgetItem] = {}
        pdfs: dict[tuple[str, str], QTreeWidgetItem] = {}
        pages: dict[tuple[str, str, int], QTreeWidgetItem] = {}
        page_counts: dict[tuple[str, str, int], int] = {}
        for index, record in enumerate(self.records):
            annotation = load_annotation(label_root, reviewer, record)
            status = str(annotation.get("status") or "pending")
            job_item = jobs.get(record.job_key)
            if job_item is None:
                job_item = QTreeWidgetItem([record.job_key, ""])
                jobs[record.job_key] = job_item
                self.tree.addTopLevelItem(job_item)
            pdf_key = (record.job_key, record.pdf_slug)
            pdf_item = pdfs.get(pdf_key)
            if pdf_item is None:
                pdf_item = QTreeWidgetItem([record.pdf_file, ""])
                pdfs[pdf_key] = pdf_item
                job_item.addChild(pdf_item)
            page_key = (record.job_key, record.pdf_slug, record.page_number)
            page_counts[page_key] = page_counts.get(page_key, 0) + 1
            page_item = pages.get(page_key)
            if page_item is None:
                page_item = QTreeWidgetItem([f"Page {record.page_number:04d}", ""])
                pages[page_key] = page_item
                pdf_item.addChild(page_item)
            title_count = len(annotation.get("title_regions", []) or [])
            body_count = len(annotation.get("article_regions", []) or [])
            text = f"A{record.article_order:02d} | T{title_count} B{body_count} | {_preview(str(annotation.get('corrected_title') or record.title or ''), 56)}"
            article_item = QTreeWidgetItem([text, STATUS_LABELS.get(status, status)])
            article_item.setData(0, Qt.ItemDataRole.UserRole, index)
            article_item.setForeground(1, QBrush(QColor(STATUS_COLORS.get(status, "#555555"))))
            article_item.setToolTip(0, f"{record.pdf_file}\npage {record.page_number} article {record.article_order}")
            page_item.addChild(article_item)
        for page_key, page_item in pages.items():
            page_item.setText(0, f"Page {page_key[2]:04d} | {page_counts.get(page_key, 0)} articles")
        for group in (jobs, pdfs, pages):
            for item in group.values():
                item.setExpanded(True)
        self.tree.resizeColumnToContents(1)
        if self.tree.columnWidth(1) < 76:
            self.tree.setColumnWidth(1, 76)
        self.tree.blockSignals(False)

    def on_tree_selected(self):
        items = self.tree.selectedItems()
        if not items:
            return
        index = items[0].data(0, Qt.ItemDataRole.UserRole)
        if index is not None:
            self._load_record(int(index))

    def _select_record_index(self, index: int):
        if not (0 <= index < len(self.records)):
            return
        self.current_index = index
        if item := self._find_tree_item(index):
            self.tree.blockSignals(True)
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
            self.tree.blockSignals(False)
        self._load_record(index)

    def _find_tree_item(self, index: int) -> QTreeWidgetItem | None:
        for i in range(self.tree.topLevelItemCount()):
            found = self._find_tree_item_recursive(self.tree.topLevelItem(i), index)
            if found is not None:
                return found
        return None

    def _find_tree_item_recursive(self, item: QTreeWidgetItem, index: int) -> QTreeWidgetItem | None:
        if item.data(0, Qt.ItemDataRole.UserRole) == index:
            return item
        for child_index in range(item.childCount()):
            found = self._find_tree_item_recursive(item.child(child_index), index)
            if found is not None:
                return found
        return None

    def _load_record(self, index: int):
        if not (0 <= index < len(self.records)):
            return
        self.current_index = index
        record = self.records[index]
        annotation = load_annotation(self.label_root_path(), self.reviewer_input.text(), record)
        self.current_annotation = copy.deepcopy(annotation)
        self.preview_label.setText(f"{record.job_key} | {record.pdf_file} | page {record.page_number} | article {record.article_order}")
        self.preview_widget.set_record(record, self.current_annotation)
        self._set_combo_value(self.bbox_mode_combo, "select")
        self.preview_widget.set_edit_mode("select")
        self.source_title_view.setPlainText(record.title)
        self.source_body_view.setPlainText(record.body_text)
        self.image_browser.setHtml(self._build_image_html(record))
        self._set_combo_value(self.status_combo, str(self.current_annotation.get("status") or "pending"))
        self.notes_edit.setPlainText(str(self.current_annotation.get("notes") or ""))
        self.tags_input.setText(", ".join(self.current_annotation.get("tags", []) or []))
        self.title_edit.setPlainText(str(self.current_annotation.get("corrected_title") or ""))
        self.body_edit.setPlainText(str(self.current_annotation.get("corrected_body_text") or ""))
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()
        self._refresh_region_lists()
        self._set_active_region(None, None, sync_preview=False)
        self.bbox_status_label.setText("선택 없음")

    def _set_combo_value(self, combo: QComboBox, value: str):
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def reset_to_source(self):
        if self.current_index is None:
            return
        record = self.records[self.current_index]
        self.current_annotation = copy.deepcopy(build_default_annotation(record, self.reviewer_input.text()))
        self.preview_widget.set_record(record, self.current_annotation)
        self._set_combo_value(self.bbox_mode_combo, "select")
        self.preview_widget.set_edit_mode("select")
        self.source_title_view.setPlainText(record.title)
        self.source_body_view.setPlainText(record.body_text)
        self.image_browser.setHtml(self._build_image_html(record))
        self._set_combo_value(self.status_combo, "pending")
        self.notes_edit.clear()
        self.tags_input.clear()
        self.title_edit.setPlainText(str(self.current_annotation.get("corrected_title") or ""))
        self.body_edit.setPlainText(str(self.current_annotation.get("corrected_body_text") or ""))
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()
        self._refresh_region_lists()
        self._set_active_region(None, None, sync_preview=False)
        self.bbox_status_label.setText("선택 없음")

    def save_current_annotation(self):
        if self.current_index is None or self.current_annotation is None:
            self._show_warning("저장할 기사를 먼저 선택하세요.")
            return
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()
        self._sync_annotation_bboxes_from_regions()
        record = self.records[self.current_index]
        payload = copy.deepcopy(self.current_annotation)
        payload.update(
            {
                "status": str(self.status_combo.currentData()),
                "corrected_title": self.title_edit.toPlainText().strip(),
                "corrected_body_text": self.body_edit.toPlainText().strip(),
                "notes": self.notes_edit.toPlainText().strip(),
                "tags": [token.strip() for token in self.tags_input.text().split(",") if token.strip()],
                "title_regions": _regions(self.current_annotation.get("title_regions")),
                "article_regions": _regions(self.current_annotation.get("article_regions")),
                "image_bboxes": _boxes(self.current_annotation.get("image_bboxes")),
            }
        )
        self.current_annotation = payload
        save_annotation(self.label_root_path(), self.reviewer_input.text(), record, payload)
        self.statusBar().showMessage(f"저장됨: page {record.page_number} article {record.article_order}", 4000)
        self._fill_tree()
        self._select_record_index(self.current_index)

    def select_next_unlabeled(self):
        if not self.records:
            return
        label_root = self.label_root_path()
        reviewer = self.reviewer_input.text()
        start = (self.current_index or 0) + 1 if self.current_index is not None else 0
        for index in range(start, len(self.records)):
            if load_annotation(label_root, reviewer, self.records[index]).get("status") == "pending":
                self._select_record_index(index)
                return
        for index in range(0, start):
            if load_annotation(label_root, reviewer, self.records[index]).get("status") == "pending":
                self._select_record_index(index)
                return
        self.statusBar().showMessage("대기 상태 레코드가 없습니다.", 4000)

    def export_dataset(self) -> dict[str, Any] | None:
        if self.current_index is not None:
            self.save_current_annotation()
        reviewer = normalize_reviewer_name(self.reviewer_input.text())
        if not reviewer:
            self._show_warning("reviewer 이름이 필요합니다.")
            return None
        if not self.records:
            self._show_warning("export할 레코드가 없습니다.")
            return None
        result = export_fine_tuning_dataset(label_root=self.label_root_path(), reviewer=reviewer, records=self.records, export_root=self.export_root_path())
        self.last_export_dir = Path(result["dataset_dir"])
        self._append_command_log(f"[export] dir={self.last_export_dir}\n[export] accepted={result['accepted_count']} title_crops={result['title_crop_count']} article_crops={result['article_crop_count']}")
        self.statusBar().showMessage(f"데이터셋 export 완료: {self.last_export_dir}", 5000)
        return result

    def run_command(self):
        command = self.command_input.text().strip()
        if not command:
            self._show_warning("실행할 명령어를 입력하세요.")
            return
        if command in {"파인튜닝하기", "fine-tune", "export"}:
            self.export_dataset()
            return
        if self.process is not None and self.process.state() != QProcess.ProcessState.NotRunning:
            self._show_warning("이미 실행 중인 명령이 있습니다.")
            return
        resolved = command
        if "{dataset_dir}" in resolved:
            if self.last_export_dir is None:
                result = self.export_dataset()
                if not result:
                    return
            resolved = resolved.replace("{dataset_dir}", str(self.last_export_dir))
        self.process = QProcess(self)
        self.process.setWorkingDirectory(self.command_workdir_input.text().strip() or str(Path.cwd()))
        self.process.readyReadStandardOutput.connect(self._read_process_stdout)
        self.process.readyReadStandardError.connect(self._read_process_stderr)
        self.process.finished.connect(self._process_finished)
        self._append_command_log(f"[run] cwd={self.process.workingDirectory()}\n[run] {resolved}")
        self.process.start("cmd", ["/c", resolved])
        self.statusBar().showMessage("명령 실행 중", 3000)

    def stop_command(self):
        if self.process is None or self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.process.kill()
        self._append_command_log("[run] process killed")
        self.statusBar().showMessage("명령 중단됨", 3000)

    def _read_process_stdout(self):
        if self.process is None:
            return
        payload = bytes(self.process.readAllStandardOutput()).decode("utf-8", errors="replace").strip()
        if payload:
            self._append_command_log(payload)

    def _read_process_stderr(self):
        if self.process is None:
            return
        payload = bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip()
        if payload:
            self._append_command_log(payload)

    def _process_finished(self, exit_code: int, exit_status):  # type: ignore[override]
        self._append_command_log(f"[run] finished exit_code={exit_code}")
        self.statusBar().showMessage(f"명령 종료 exit={exit_code}", 5000)

    def _append_command_log(self, message: str):
        current = self.command_log.toPlainText().strip()
        self.command_log.setPlainText(f"{current}\n{message}" if current else message)
        self.command_log.verticalScrollBar().setValue(self.command_log.verticalScrollBar().maximum())

    def preview_zoom_in(self):
        self.preview_widget.zoom_in()

    def preview_zoom_out(self):
        self.preview_widget.zoom_out()

    def preview_fit(self):
        self.preview_widget.set_fit_view()

    def preview_actual(self):
        self.preview_widget.set_actual_size()

    def _install_shortcuts(self):
        self._shortcuts = [
            QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_current_annotation),
            QShortcut(QKeySequence("Ctrl+Z"), self, activated=self._undo_bbox_change),
            QShortcut(QKeySequence("Ctrl+Y"), self, activated=self._redo_bbox_change),
            QShortcut(QKeySequence("Ctrl+Shift+Z"), self, activated=self._redo_bbox_change),
            QShortcut(QKeySequence("Delete"), self, activated=self._delete_selected_bbox),
            QShortcut(QKeySequence("0"), self, activated=lambda: self._set_bbox_mode("select")),
            QShortcut(QKeySequence("1"), self, activated=lambda: self._set_bbox_mode("draw_title")),
            QShortcut(QKeySequence("2"), self, activated=lambda: self._set_bbox_mode("draw_article")),
            QShortcut(QKeySequence("3"), self, activated=lambda: self._set_bbox_mode("add_image")),
            QShortcut(QKeySequence("N"), self, activated=self._select_next_unlabeled_shortcut),
        ]

    def _focus_is_text_input(self) -> bool:
        focused = QApplication.focusWidget()
        return isinstance(focused, (QLineEdit, QPlainTextEdit))

    def _set_bbox_mode(self, mode: str):
        if self._focus_is_text_input():
            return
        self._set_combo_value(self.bbox_mode_combo, mode)
        self.preview_widget.set_edit_mode(mode)
        self.statusBar().showMessage(f"BBox 모드: {EDIT_MODE_LABELS.get(mode, mode)}", 2000)

    def _select_next_unlabeled_shortcut(self):
        if self._focus_is_text_input():
            return
        self.select_next_unlabeled()

    def _change_bbox_mode(self):
        self.preview_widget.set_edit_mode(str(self.bbox_mode_combo.currentData()))

    def _handle_preview_changed(self, payload: dict[str, Any]):
        if self.current_annotation is None:
            return
        self.current_annotation["title_regions"] = _regions(payload.get("title_regions"))
        self.current_annotation["article_regions"] = _regions(payload.get("article_regions"))
        self.current_annotation["image_bboxes"] = _boxes(payload.get("image_bboxes"))
        self._sync_annotation_bboxes_from_regions()
        self._refresh_region_lists()
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()
        self.statusBar().showMessage("BBox 수정됨. 저장하면 annotation에 반영됩니다.", 3000)

    def _handle_preview_selection(self, payload: dict[str, Any]):
        self.bbox_status_label.setText(str(payload.get("label") or "선택 없음"))
        self._set_active_region(
            payload.get("kind") if isinstance(payload.get("kind"), str) else None,
            payload.get("index") if isinstance(payload.get("index"), int) else None,
            sync_preview=False,
            focus=bool(payload.get("focus_editor")),
        )

    def _handle_zoom_changed(self, percent: float):
        self.zoom_label.setText(f"{percent:.0f}%")

    def _update_history_buttons(self, can_undo: bool, can_redo: bool):
        self.undo_button.setEnabled(can_undo)
        self.redo_button.setEnabled(can_redo)

    def _sync_annotation_bboxes_from_regions(self):
        if self.current_annotation is None:
            return
        self.current_annotation["title_bbox"] = _bbox_union([_region_bbox(region) for region in _regions(self.current_annotation.get("title_regions"))])
        self.current_annotation["article_bbox"] = _bbox_union([_region_bbox(region) for region in _regions(self.current_annotation.get("article_regions"))])

    def _select_region_from_list(self, kind: str, row: int):
        if self._syncing_region_ui:
            return
        if row < 0:
            if self._active_kind == kind:
                self._set_active_region(None, None)
            return
        self._set_active_region(kind, row)

    def _set_active_region(self, kind: str | None, index: int | None, *, sync_preview: bool = True, focus: bool = False):
        if self.current_annotation is None:
            kind, index = None, None
        if kind == "title":
            items = list(self.current_annotation.get("title_regions", [])) if self.current_annotation else []
            if index is None or not (0 <= index < len(items)):
                kind, index = None, None
        elif kind == "article":
            items = list(self.current_annotation.get("article_regions", [])) if self.current_annotation else []
            if index is None or not (0 <= index < len(items)):
                kind, index = None, None
        elif kind == "image":
            items = list(self.current_annotation.get("image_bboxes", [])) if self.current_annotation else []
            if index is None or not (0 <= index < len(items)):
                kind, index = None, None
        else:
            kind, index = None, None
        self._active_kind, self._active_index = kind, index
        self._syncing_region_ui = True
        self.title_region_list.blockSignals(True)
        self.article_region_list.blockSignals(True)
        self.title_region_list.setCurrentRow(index if kind == "title" and index is not None else -1)
        self.article_region_list.setCurrentRow(index if kind == "article" and index is not None else -1)
        if kind != "title":
            self.title_region_list.clearSelection()
        if kind != "article":
            self.article_region_list.clearSelection()
        self.title_region_list.blockSignals(False)
        self.article_region_list.blockSignals(False)
        if kind in {"title", "article"} and index is not None and self.current_annotation is not None:
            key = "title_regions" if kind == "title" else "article_regions"
            region = self.current_annotation[key][index]
            self.region_editor_label.setText(f"{BOX_KIND_LABELS[kind]} {index + 1} | {_bbox_label(_region_bbox(region))}")
            self.region_text_edit.setEnabled(True)
            self.region_text_edit.setPlainText(_region_text(region))
            if focus:
                self.region_text_edit.setFocus()
                self.region_text_edit.selectAll()
        elif kind == "image" and index is not None and self.current_annotation is not None:
            self.region_editor_label.setText(f"이미지 {index + 1} | {_bbox_label(_bbox(self.current_annotation['image_bboxes'][index]))}")
            self.region_text_edit.clear()
            self.region_text_edit.setEnabled(False)
        else:
            self.region_editor_label.setText("선택된 박스 없음")
            self.region_text_edit.clear()
            self.region_text_edit.setEnabled(False)
        if sync_preview:
            self.preview_widget.set_selected_region(kind, index)
        self._syncing_region_ui = False

    def _handle_region_text_changed(self):
        if self._syncing_region_ui or self.current_annotation is None:
            return
        if self._active_kind not in {"title", "article"} or self._active_index is None:
            return
        key = "title_regions" if self._active_kind == "title" else "article_regions"
        if not (0 <= self._active_index < len(self.current_annotation.get(key, []))):
            return
        self.current_annotation[key][self._active_index]["text"] = self.region_text_edit.toPlainText()
        self._refresh_region_lists()
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()

    def _refresh_region_lists(self):
        title_regions = _regions(self.current_annotation.get("title_regions")) if self.current_annotation else []
        article_regions = _regions(self.current_annotation.get("article_regions")) if self.current_annotation else []
        active_kind, active_index = self._active_kind, self._active_index
        self._syncing_region_ui = True
        self.title_region_list.clear()
        self.article_region_list.clear()
        for index, region in enumerate(title_regions, start=1):
            item = QListWidgetItem(f"제목 {index} | {_bbox_label(_region_bbox(region))} | {_preview(_region_text(region))}")
            item.setToolTip(_region_text(region))
            self.title_region_list.addItem(item)
        for index, region in enumerate(article_regions, start=1):
            item = QListWidgetItem(f"본문 {index} | {_bbox_label(_region_bbox(region))} | {_preview(_region_text(region))}")
            item.setToolTip(_region_text(region))
            self.article_region_list.addItem(item)
        self._syncing_region_ui = False
        if active_kind == "title" and active_index is not None and active_index < len(title_regions):
            self._set_active_region("title", active_index, sync_preview=False)
        elif active_kind == "article" and active_index is not None and active_index < len(article_regions):
            self._set_active_region("article", active_index, sync_preview=False)
        elif active_kind == "image" and self.current_annotation is not None and active_index is not None and active_index < len(self.current_annotation.get("image_bboxes", [])):
            self._set_active_region("image", active_index, sync_preview=False)
        else:
            self._set_active_region(None, None, sync_preview=False)

    def _sync_final_from_regions(self):
        if self.current_annotation is None:
            return
        title_text = _compose(_regions(self.current_annotation.get("title_regions")), "\n")
        body_text = _compose(_regions(self.current_annotation.get("article_regions")), "\n\n")
        self.current_annotation["corrected_title"] = title_text
        self.current_annotation["corrected_body_text"] = body_text
        self.title_edit.setPlainText(title_text)
        self.body_edit.setPlainText(body_text)

    def _toggle_auto_compose(self, checked: bool = False):
        self._update_final_text_edit_state()
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()

    def _update_final_text_edit_state(self):
        auto = self.auto_compose_checkbox.isChecked()
        self.title_edit.setReadOnly(auto)
        self.body_edit.setReadOnly(auto)

    def _delete_selected_bbox(self):
        if self._focus_is_text_input():
            return
        self.preview_widget.clear_selected()

    def _undo_bbox_change(self):
        if self._focus_is_text_input():
            return
        self.preview_widget.undo()

    def _redo_bbox_change(self):
        if self._focus_is_text_input():
            return
        self.preview_widget.redo()

    def _clear_all_image_bboxes(self):
        if self._focus_is_text_input():
            return
        self.preview_widget.clear_all_images()

    def _restore_current_bboxes_from_source(self):
        if self.current_index is None:
            return
        record = self.records[self.current_index]
        source = build_default_annotation(record, self.reviewer_input.text())
        if self.current_annotation is None:
            self.current_annotation = copy.deepcopy(source)
        else:
            self.current_annotation["title_regions"] = self._merge_region_texts(source.get("title_regions", []), self.current_annotation.get("title_regions", []))
            self.current_annotation["article_regions"] = self._merge_region_texts(source.get("article_regions", []), self.current_annotation.get("article_regions", []))
            self.current_annotation["image_bboxes"] = _boxes(source.get("image_bboxes"))
        self.preview_widget.push_undo_state()
        self.preview_widget.set_regions(
            title_regions=self.current_annotation.get("title_regions", []),
            article_regions=self.current_annotation.get("article_regions", []),
            image_bboxes=self.current_annotation.get("image_bboxes", []),
        )
        self._sync_annotation_bboxes_from_regions()
        self._refresh_region_lists()
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()
        self.statusBar().showMessage("OCR 박스로 복원했습니다.", 3000)

    def _clear_current_annotation(self):
        if self.current_index is None or self.current_annotation is None:
            return
        answer = QMessageBox.question(
            self,
            "Labeling Tool",
            "현재 기사의 제목/본문/이미지 박스와 최종 텍스트를 모두 비울까요?\n저장해야 annotation에 반영됩니다.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.current_annotation["title_bbox"] = None
        self.current_annotation["article_bbox"] = None
        self.current_annotation["title_regions"] = []
        self.current_annotation["article_regions"] = []
        self.current_annotation["image_bboxes"] = []
        self.current_annotation["corrected_title"] = ""
        self.current_annotation["corrected_body_text"] = ""
        self._set_combo_value(self.status_combo, "pending")
        self.preview_widget.set_regions(title_regions=[], article_regions=[], image_bboxes=[], reset_history=True)
        self._refresh_region_lists()
        if self.auto_compose_checkbox.isChecked():
            self._sync_final_from_regions()
        else:
            self.title_edit.clear()
            self.body_edit.clear()
        self.statusBar().showMessage("박스와 최종 텍스트를 비웠습니다. 저장하면 annotation에 반영됩니다.", 4000)

    def _merge_region_texts(self, source_regions: list[dict[str, Any]], current_regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        current = _regions(current_regions)
        for index, source_region in enumerate(_regions(source_regions)):
            text = _region_text(current[index]) if index < len(current) else _region_text(source_region)
            merged.append({"bbox": _region_bbox(source_region), "text": text})
        return merged

    def _build_image_html(self, record: ArticleRecord) -> str:
        blocks: list[str] = []
        for index, image_entry in enumerate(record.image_entries, start=1):
            image_path = Path(str(image_entry.get("image_path") or "")).expanduser()
            if not image_path.exists():
                continue
            encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
            blocks.append(
                f"""
                <section style="margin-bottom:12px;">
                    <img src="data:image/png;base64,{encoded}" style="max-width: 100%; border:1px solid #DED3C3; border-radius:8px;">
                    <div style="font-size:11px; color:#6A5D50; margin-top:4px;">Image {index} | {html.escape(image_path.name)}</div>
                </section>
                """
            )
        if not blocks:
            return "<p style='color:#6A5D50;'>기사 이미지가 없습니다.</p>"
        return "<html><body style='background:#FFFDF9;'>" + "".join(blocks) + "</body></html>"

    def output_root_path(self) -> Path | None:
        raw = self.output_root_input.text().strip()
        if not raw:
            self._show_warning("output root 경로가 필요합니다.")
            return None
        path = Path(raw)
        if not path.exists():
            self._show_warning(f"output root를 찾을 수 없습니다: {path}")
            return None
        return path

    def label_root_path(self) -> Path:
        return Path(self.label_root_input.text().strip() or "_labels")

    def export_root_path(self) -> Path:
        return Path(self.export_root_input.text().strip() or "_fine_tuning_exports")

    def _show_warning(self, message: str):
        QMessageBox.warning(self, "Labeling Tool", message)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Army-OCR Labeling Tool")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
