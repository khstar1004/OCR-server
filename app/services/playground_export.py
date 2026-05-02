from __future__ import annotations

import copy
import io
import json
import mimetypes
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

IMAGE_BLOCK_LABELS = {"image", "figure", "photo", "picture", "illustration", "chart", "graphic", "diagram"}
PAGE_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PLAYGROUND_EDIT_METADATA_KEY = "playground_edit"


@dataclass(frozen=True, slots=True)
class PlaygroundImageAsset:
    name: str
    relative_path: str
    page_index: int
    page_number: int
    kind: str
    source_path: Path
    bbox: tuple[int, int, int, int] | None = None
    alt: str = ""
    block_id: str = ""

    def public_payload(self, image_url_prefix: str) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.relative_path,
            "url": f"{image_url_prefix.rstrip('/')}/{self.name}",
            "page_index": self.page_index,
            "page_number": self.page_number,
            "kind": self.kind,
            "bbox": list(self.bbox) if self.bbox is not None else None,
            "alt": self.alt,
            "block_id": self.block_id,
        }


def build_playground_response_payload(
    *,
    request_id: str,
    record: dict[str, Any],
    result: dict[str, Any],
    image_url_prefix: str,
) -> dict[str, Any]:
    assets = collect_playground_assets(record, result)
    views = render_playground_views(result, assets, image_ref_prefix=image_url_prefix, relative_images=False)
    pages = _result_pages(result)
    page_assets = {(asset.page_index, asset.kind): asset for asset in assets if asset.kind == "page"}
    public_assets = [asset.public_payload(image_url_prefix) for asset in assets]

    page_payloads: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages):
        page_asset = page_assets.get((page_index, "page"))
        page_payloads.append(
            {
                "page_index": page_index,
                "page_number": _safe_int(page.get("page_number"), page_index + 1),
                "width": _safe_int(page.get("width"), 0),
                "height": _safe_int(page.get("height"), 0),
                "image_url": page_asset.public_payload(image_url_prefix)["url"] if page_asset else None,
                "blocks": list(page.get("blocks") or []),
                "articles": list(page.get("articles") or []),
                "assets": [item for item in public_assets if item["page_index"] == page_index],
                "runtime": dict(page.get("runtime") or {}) if isinstance(page.get("runtime"), dict) else None,
            }
        )

    return {
        "success": bool(result.get("success")),
        "status": str(result.get("status") or ""),
        "request_id": request_id,
        "page_count": int(result.get("page_count") or len(page_payloads)),
        "processed_page_count": _safe_int(result.get("processed_page_count"), len(page_payloads)),
        "progress": _progress_payload(result, len(page_payloads)),
        "parse_quality_score": result.get("parse_quality_score"),
        "metadata": result.get("metadata") or {},
        "pages": page_payloads,
        "assets": public_assets,
        "views": views,
        "download_url": f"api/download/{request_id}",
        "error": result.get("error"),
    }


def build_playground_partial_response_payload(
    *,
    request_id: str,
    record: dict[str, Any],
    result: dict[str, Any],
    image_url_prefix: str,
) -> dict[str, Any]:
    assets = collect_playground_assets(record, result)
    pages = _result_pages(result)
    page_assets = {(asset.page_index, asset.kind): asset for asset in assets if asset.kind == "page"}
    public_assets = [asset.public_payload(image_url_prefix) for asset in assets]

    page_payloads: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages):
        page_asset = page_assets.get((page_index, "page"))
        page_payloads.append(
            {
                "page_index": page_index,
                "page_number": _safe_int(page.get("page_number"), page_index + 1),
                "width": _safe_int(page.get("width"), 0),
                "height": _safe_int(page.get("height"), 0),
                "image_url": page_asset.public_payload(image_url_prefix)["url"] if page_asset else None,
                "blocks": list(page.get("blocks") or []),
                "articles": list(page.get("articles") or []),
                "assets": [item for item in public_assets if item["page_index"] == page_index],
                "runtime": dict(page.get("runtime") or {}) if isinstance(page.get("runtime"), dict) else None,
            }
        )

    total_pages = _safe_int(result.get("page_count"), len(page_payloads))
    processed_pages = _safe_int(result.get("processed_page_count"), len(page_payloads))
    progress = _progress_payload(result, len(page_payloads))
    compact_json = {
        "request_id": request_id,
        "status": str(result.get("status") or "processing"),
        "page_count": total_pages,
        "processed_page_count": processed_pages,
        "progress": progress,
        "pages": page_payloads,
        "assets": public_assets,
        "metadata": result.get("metadata") or {},
    }
    return {
        "success": result.get("success"),
        "status": str(result.get("status") or "processing"),
        "request_id": request_id,
        "page_count": total_pages,
        "processed_page_count": processed_pages,
        "progress": progress,
        "parse_quality_score": result.get("parse_quality_score"),
        "metadata": result.get("metadata") or {},
        "pages": page_payloads,
        "assets": public_assets,
        "views": {
            "json": json.dumps(compact_json, ensure_ascii=False, indent=2),
            "blocks": _render_blocks_text(pages),
            "html": str(result.get("html") or ""),
            "markdown": str(result.get("markdown") or ""),
        },
        "download_url": f"api/download/{request_id}",
        "error": result.get("error"),
    }


def build_playground_export_zip(*, request_id: str, record: dict[str, Any], result: dict[str, Any]) -> bytes:
    assets = collect_playground_assets(record, result)
    views = render_playground_views(result, assets, image_ref_prefix="images", relative_images=True)
    export_json = build_export_json_payload(result, assets, image_ref_prefix="images", relative_images=True)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("result.json", json.dumps(export_json, ensure_ascii=False, indent=2))
        archive.writestr("result.md", views["markdown"])
        archive.writestr("result.html", views["html"])
        archive.writestr("README.txt", _download_readme(request_id, assets))
        for asset in assets:
            try:
                data, _media_type = read_asset_bytes(asset)
            except FileNotFoundError:
                continue
            archive.writestr(asset.relative_path, data)
    return buffer.getvalue()


def build_export_json_payload(
    result: dict[str, Any],
    assets: list[PlaygroundImageAsset],
    *,
    image_ref_prefix: str,
    relative_images: bool,
) -> dict[str, Any]:
    payload = copy.deepcopy(_result_json(result))
    payload["ocr_assets"] = [
        _asset_payload_for_view(asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images)
        for asset in assets
    ]
    payload["ocr_export"] = {
        "contains_images": bool(assets),
        "image_directory": "images",
        "markdown": "result.md",
        "html": "result.html",
    }
    return payload


def collect_playground_assets(record: dict[str, Any], result: dict[str, Any]) -> list[PlaygroundImageAsset]:
    page_paths = [Path(path) for path in record.get("page_image_paths", []) if str(path or "").strip()]
    pages = _result_pages(result)
    assets: list[PlaygroundImageAsset] = []
    seen_names: set[str] = set()

    for page_index, source_path in enumerate(page_paths):
        page = pages[page_index] if page_index < len(pages) else {}
        page_number = _safe_int(page.get("page_number"), page_index + 1)
        page_name = _page_asset_name(page_number, source_path)
        assets.append(
            PlaygroundImageAsset(
                name=page_name,
                relative_path=f"images/{page_name}",
                page_index=page_index,
                page_number=page_number,
                kind="page",
                source_path=source_path,
                alt=f"Page {page_number}",
            )
        )
        seen_names.add(page_name)

        image_order = 1
        seen_bboxes: set[tuple[int, int, int, int]] = set()
        for block in page.get("blocks") or []:
            manual_asset = _manual_image_asset(record, page_index, page_number, block)
            if manual_asset is not None:
                assets.append(manual_asset)
                seen_names.add(manual_asset.name)
                if _is_image_block(block):
                    image_order += 1
                    continue
            if not _is_image_block(block):
                continue
            bbox = _bbox_tuple(block.get("bbox"))
            if bbox is None or bbox in seen_bboxes:
                continue
            seen_bboxes.add(bbox)
            crop_name = f"page-{page_number:04d}-image-{image_order:04d}.png"
            while crop_name in seen_names:
                image_order += 1
                crop_name = f"page-{page_number:04d}-image-{image_order:04d}.png"
            seen_names.add(crop_name)
            assets.append(
                PlaygroundImageAsset(
                    name=crop_name,
                    relative_path=f"images/{crop_name}",
                    page_index=page_index,
                    page_number=page_number,
                    kind="crop",
                    source_path=source_path,
                    bbox=bbox,
                    alt=str(block.get("text") or f"Page {page_number} image {image_order}"),
                    block_id=str(block.get("block_id") or ""),
                )
            )
            image_order += 1

    return assets


def find_playground_asset(
    *,
    record: dict[str, Any],
    result: dict[str, Any],
    asset_name: str,
) -> PlaygroundImageAsset | None:
    cleaned = Path(asset_name).name
    for asset in collect_playground_assets(record, result):
        if asset.name == cleaned:
            return asset
    return None


def read_asset_bytes(asset: PlaygroundImageAsset) -> tuple[bytes, str]:
    if not asset.source_path.exists():
        raise FileNotFoundError(str(asset.source_path))

    if asset.kind in {"page", "manual"}:
        media_type = mimetypes.guess_type(asset.source_path.name)[0] or "application/octet-stream"
        return asset.source_path.read_bytes(), media_type

    if asset.bbox is None:
        raise FileNotFoundError(asset.name)
    with Image.open(asset.source_path) as image:
        bbox = _clamp_bbox(asset.bbox, image.width, image.height)
        if bbox is None:
            raise FileNotFoundError(asset.name)
        crop = image.crop(bbox)
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
    return buffer.getvalue(), "image/png"


def render_playground_views(
    result: dict[str, Any],
    assets: list[PlaygroundImageAsset],
    *,
    image_ref_prefix: str,
    relative_images: bool,
) -> dict[str, str]:
    pages = _result_pages(result)
    if not pages:
        return {
            "blocks": "",
            "json": json.dumps(build_export_json_payload(result, assets, image_ref_prefix=image_ref_prefix, relative_images=relative_images), ensure_ascii=False, indent=2),
            "html": str(result.get("html") or ""),
            "markdown": str(result.get("markdown") or ""),
        }

    markdown = _render_markdown(pages, assets, image_ref_prefix=image_ref_prefix, relative_images=relative_images)
    html_payload = _render_html(pages, assets, image_ref_prefix=image_ref_prefix, relative_images=relative_images)
    blocks = _render_blocks_text(pages)
    json_payload = build_export_json_payload(result, assets, image_ref_prefix=image_ref_prefix, relative_images=relative_images)
    return {
        "blocks": blocks,
        "json": json.dumps(json_payload, ensure_ascii=False, indent=2),
        "html": html_payload,
        "markdown": markdown,
    }


def _render_markdown(
    pages: list[dict[str, Any]],
    assets: list[PlaygroundImageAsset],
    *,
    image_ref_prefix: str,
    relative_images: bool,
) -> str:
    by_page = _assets_by_page(assets)
    lines: list[str] = []
    for page_index, page in enumerate(pages):
        page_number = _safe_int(page.get("page_number"), page_index + 1)
        if lines:
            lines.append("")
        lines.append(f"# Page {page_number}")
        page_asset = _first_asset(by_page, page_index, "page")
        if page_asset is not None:
            lines.extend(["", f"![{_escape_markdown_alt(page_asset.alt)}]({_asset_ref(page_asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images)})"])

        crop_by_block = _crop_assets_by_block(by_page.get(page_index, []))
        crop_index = 0
        for block in page.get("blocks") or []:
            label = str(block.get("label") or "text").lower()
            text = str(block.get("text") or "").strip()
            manual_asset = _manual_asset_for_block(block, crop_by_block)
            if manual_asset is not None and not _is_image_block(block):
                lines.extend(["", f"![{_escape_markdown_alt(manual_asset.alt)}]({_asset_ref(manual_asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images)})"])
            if _is_image_block(block):
                if _is_embedded_in_table(block):
                    crop_index += 1
                    continue
                block_id = str(block.get("block_id") or "")
                asset = crop_by_block.get(block_id)
                if asset is None:
                    crop_assets = [item for item in by_page.get(page_index, []) if item.kind == "crop"]
                    asset = crop_assets[crop_index] if crop_index < len(crop_assets) else None
                crop_index += 1
                if asset is not None:
                    lines.extend(["", f"![{_escape_markdown_alt(asset.alt)}]({_asset_ref(asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images)})"])
                continue
            if not text:
                continue
            lines.append("")
            if label in {"title", "sectionheader", "section_header", "heading"}:
                lines.append(f"## {text}")
            elif label == "table":
                for asset in _embedded_table_assets(block, crop_by_block):
                    lines.extend(["", f"![{_escape_markdown_alt(asset.alt)}]({_asset_ref(asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images)})"])
                table_markdown = _markdown_table_from_rows(block)
                if table_markdown:
                    lines.append(table_markdown)
                else:
                    lines.extend(["**Table**", "", text])
            elif label == "code_block":
                lines.extend(["```text", text, "```"])
            elif label in {"equation_block", "chemical_block"}:
                lines.extend(["$$", text, "$$"])
            elif label in {"form", "table_of_contents", "bibliography", "complex_block", "blank_page"}:
                lines.extend([f"**{label.replace('_', ' ').title()}**", "", text])
            elif label in {"caption", "pageheader", "pagefooter", "header", "footer"}:
                lines.append(f"*{text}*")
            else:
                lines.append(text)
    return "\n".join(lines).strip() + "\n"


def _render_html(
    pages: list[dict[str, Any]],
    assets: list[PlaygroundImageAsset],
    *,
    image_ref_prefix: str,
    relative_images: bool,
) -> str:
    by_page = _assets_by_page(assets)
    parts = [
        "<!doctype html><html><head><meta charset=\"utf-8\">",
        "<title>Army-OCR Result</title>",
        "<style>body{font-family:Arial,sans-serif;line-height:1.55;margin:32px;color:#111827}img{max-width:100%;height:auto}figure{margin:18px 0}figcaption{color:#6b7280;font-size:13px}.page{break-after:page;margin-bottom:48px}.block-caption{color:#4b5563;font-style:italic}.block-structured{white-space:pre-wrap;background:#fff7ed;border-left:4px solid #c2410c;padding:12px}</style>",
        "</head><body>",
    ]
    for page_index, page in enumerate(pages):
        page_number = _safe_int(page.get("page_number"), page_index + 1)
        parts.append(f"<section class=\"page\" data-page=\"{page_number}\">")
        parts.append(f"<h1>Page {page_number}</h1>")
        page_asset = _first_asset(by_page, page_index, "page")
        if page_asset is not None:
            parts.append(_html_figure(page_asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images, css_class="page-image"))

        crop_by_block = _crop_assets_by_block(by_page.get(page_index, []))
        crop_index = 0
        for block in page.get("blocks") or []:
            label = str(block.get("label") or "text").lower()
            text = _html_escape(str(block.get("text") or "").strip()).replace("\n", "<br>")
            block_id = _html_escape(str(block.get("block_id") or ""))
            block_attr = f" data-block-id=\"{block_id}\"" if block_id else ""
            manual_asset = _manual_asset_for_block(block, crop_by_block)
            if manual_asset is not None and not _is_image_block(block):
                parts.append(_html_figure(manual_asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images, css_class="manual-image"))
            if _is_image_block(block):
                if _is_embedded_in_table(block):
                    crop_index += 1
                    continue
                asset = crop_by_block.get(str(block.get("block_id") or ""))
                if asset is None:
                    crop_assets = [item for item in by_page.get(page_index, []) if item.kind == "crop"]
                    asset = crop_assets[crop_index] if crop_index < len(crop_assets) else None
                crop_index += 1
                if asset is not None:
                    parts.append(_html_figure(asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images, css_class="block-image"))
                continue
            if not text:
                continue
            if label in {"title", "sectionheader", "section_header", "heading"}:
                parts.append(f"<h2 class=\"block block-{_html_escape(label)}\"{block_attr}>{text}</h2>")
            elif label == "table":
                figures = "".join(
                    _html_figure(asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images, css_class="table-image")
                    for asset in _embedded_table_assets(block, crop_by_block)
                )
                table_html = _html_table_from_rows(block)
                if table_html:
                    parts.append(f"<div class=\"block block-table table-with-media\"{block_attr}>{figures}{table_html}</div>")
                else:
                    parts.append(f"<div class=\"block block-table table-with-media\"{block_attr}>{figures}<pre class=\"block-structured\">{text}</pre></div>")
            elif label in {"table", "code_block", "equation_block", "chemical_block", "form", "complex_block"}:
                parts.append(f"<pre class=\"block block-structured block-{_html_escape(label)}\"{block_attr}>{text}</pre>")
            elif label in {"caption", "pageheader", "pagefooter", "header", "footer"}:
                parts.append(f"<p class=\"block block-caption block-{_html_escape(label)}\"{block_attr}>{text}</p>")
            else:
                parts.append(f"<p class=\"block block-{_html_escape(label)}\"{block_attr}>{text}</p>")
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def _render_blocks_text(pages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for page_index, page in enumerate(pages):
        page_number = _safe_int(page.get("page_number"), page_index + 1)
        lines.append(f"Page {page_number}")
        for order, block in enumerate(page.get("blocks") or [], start=1):
            label = str(block.get("label") or "text")
            bbox = block.get("bbox") or []
            text = str(block.get("text") or "").strip().replace("\n", " ")
            lines.append(f"  {order:02d}. {label} bbox={bbox} {text}")
    return "\n".join(lines)


def _asset_payload_for_view(
    asset: PlaygroundImageAsset,
    *,
    image_ref_prefix: str,
    relative_images: bool,
) -> dict[str, Any]:
    payload = asset.public_payload(image_ref_prefix)
    payload["ref"] = _asset_ref(asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images)
    return payload


def _asset_ref(asset: PlaygroundImageAsset, *, image_ref_prefix: str, relative_images: bool) -> str:
    if relative_images:
        return asset.relative_path
    return f"{image_ref_prefix.rstrip('/')}/{asset.name}"


def _download_readme(request_id: str, assets: list[PlaygroundImageAsset]) -> str:
    return (
        "Army-OCR export\n"
        f"request_id: {request_id}\n\n"
        "Files:\n"
        "- result.md: Markdown with relative image links.\n"
        "- result.html: HTML with relative image links.\n"
        "- result.json: OCR JSON plus ocr_assets.\n"
        "- images/: original page renders and cropped image blocks.\n\n"
        f"image_count: {len(assets)}\n"
    )


def _result_json(result: dict[str, Any]) -> dict[str, Any]:
    payload = result.get("json")
    return payload if isinstance(payload, dict) else {}


def _result_pages(result: dict[str, Any]) -> list[dict[str, Any]]:
    pages = _result_json(result).get("pages")
    if isinstance(pages, list):
        return [page for page in pages if isinstance(page, dict)]
    return []


def _page_asset_name(page_number: int, source_path: Path) -> str:
    suffix = source_path.suffix.lower()
    if suffix not in PAGE_IMAGE_SUFFIXES:
        suffix = ".png"
    return f"page-{page_number:04d}{suffix}"


def _is_image_block(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    label = str(block.get("label") or "").strip().lower()
    return label in IMAGE_BLOCK_LABELS


def _is_embedded_in_table(block: Any) -> bool:
    if not isinstance(block, dict):
        return False
    metadata = block.get("metadata")
    return isinstance(metadata, dict) and bool(metadata.get("embedded_in_table"))


def _embedded_table_assets(block: dict[str, Any], crop_by_block: dict[str, PlaygroundImageAsset]) -> list[PlaygroundImageAsset]:
    metadata = block.get("metadata")
    if not isinstance(metadata, dict):
        return []
    assets: list[PlaygroundImageAsset] = []
    for item in metadata.get("embedded_images") or []:
        if not isinstance(item, dict):
            continue
        asset = crop_by_block.get(str(item.get("block_id") or ""))
        if asset is not None:
            assets.append(asset)
    return assets


def _html_table_from_rows(block: dict[str, Any]) -> str:
    metadata = block.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    rows = metadata.get("table_rows")
    if not isinstance(rows, list):
        return ""
    rendered_rows: list[str] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        cells = [_html_escape(str(cell or "").strip()).replace("\n", "<br>") for cell in row]
        cells = [cell for cell in cells if cell]
        if len(cells) < 2:
            continue
        rendered_rows.append(f"<tr><th>{cells[0]}</th>{''.join(f'<td>{cell}</td>' for cell in cells[1:])}</tr>")
    if not rendered_rows:
        return ""
    return f"<table class=\"structured-table\"><tbody>{''.join(rendered_rows)}</tbody></table>"


def _markdown_table_from_rows(block: dict[str, Any]) -> str:
    metadata = block.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    rows = metadata.get("table_rows")
    if not isinstance(rows, list):
        return ""
    normalized: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        cells = [str(cell or "").replace("|", "\\|").strip() for cell in row]
        if any(cells):
            normalized.append(cells)
    if not normalized:
        return ""
    column_count = max(len(row) for row in normalized)
    if column_count < 2:
        return ""
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


def _bbox_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (int(round(float(item))) for item in value)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)


def _clamp_bbox(bbox: tuple[int, int, int, int], width: int, height: int) -> tuple[int, int, int, int] | None:
    x0, y0, x1, y1 = bbox
    left = max(0, min(width, x0))
    top = max(0, min(height, y0))
    right = max(0, min(width, x1))
    bottom = max(0, min(height, y1))
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


def _assets_by_page(assets: list[PlaygroundImageAsset]) -> dict[int, list[PlaygroundImageAsset]]:
    grouped: dict[int, list[PlaygroundImageAsset]] = {}
    for asset in assets:
        grouped.setdefault(asset.page_index, []).append(asset)
    return grouped


def _first_asset(grouped: dict[int, list[PlaygroundImageAsset]], page_index: int, kind: str) -> PlaygroundImageAsset | None:
    for asset in grouped.get(page_index, []):
        if asset.kind == kind:
            return asset
    return None


def _crop_assets_by_block(assets: list[PlaygroundImageAsset]) -> dict[str, PlaygroundImageAsset]:
    return {asset.block_id: asset for asset in assets if asset.kind in {"crop", "manual"} and asset.block_id}


def _manual_asset_for_block(
    block: dict[str, Any],
    crop_by_block: dict[str, PlaygroundImageAsset],
) -> PlaygroundImageAsset | None:
    block_id = str(block.get("block_id") or "")
    if not block_id:
        return None
    asset = crop_by_block.get(block_id)
    if asset is None or asset.kind != "manual":
        return None
    return asset


def _manual_image_asset(
    record: dict[str, Any],
    page_index: int,
    page_number: int,
    block: Any,
) -> PlaygroundImageAsset | None:
    if not isinstance(block, dict):
        return None
    metadata = block.get("metadata")
    if not isinstance(metadata, dict):
        return None
    edit_metadata = metadata.get(PLAYGROUND_EDIT_METADATA_KEY)
    if not isinstance(edit_metadata, dict):
        return None
    image_payload = edit_metadata.get("manual_image")
    if not isinstance(image_payload, dict):
        return None
    image_name = Path(str(image_payload.get("name") or "")).name
    if not image_name:
        return None
    manual_paths = record.get("manual_image_paths")
    if not isinstance(manual_paths, dict):
        return None
    source_path = manual_paths.get(image_name)
    if not source_path:
        return None
    return PlaygroundImageAsset(
        name=image_name,
        relative_path=f"images/{image_name}",
        page_index=page_index,
        page_number=page_number,
        kind="manual",
        source_path=Path(str(source_path)),
        bbox=None,
        alt=str(block.get("text") or image_payload.get("alt") or f"Page {page_number} image"),
        block_id=str(block.get("block_id") or ""),
    )


def _html_figure(
    asset: PlaygroundImageAsset,
    *,
    image_ref_prefix: str,
    relative_images: bool,
    css_class: str,
) -> str:
    src = _html_escape(_asset_ref(asset, image_ref_prefix=image_ref_prefix, relative_images=relative_images))
    alt = _html_escape(asset.alt or asset.name)
    caption = _html_escape(asset.alt or asset.name)
    return f"<figure class=\"{css_class}\"><img src=\"{src}\" alt=\"{alt}\"><figcaption>{caption}</figcaption></figure>"


def _escape_markdown_alt(value: str) -> str:
    return str(value or "image").replace("[", "(").replace("]", ")").replace("\n", " ")


def _html_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _progress_payload(result: dict[str, Any], processed_default: int) -> dict[str, Any]:
    progress = result.get("progress")
    if isinstance(progress, dict):
        return dict(progress)
    status = str(result.get("status") or "")
    total_pages = _safe_int(result.get("page_count"), processed_default)
    processed_pages = _safe_int(result.get("processed_page_count"), processed_default)
    percent = 100.0 if status == "complete" else 0.0
    if total_pages > 0:
        percent = round(min(processed_pages, total_pages) / total_pages * 100, 1)
    return {
        "status": status,
        "processed_pages": processed_pages,
        "total_pages": total_pages,
        "percent": percent,
    }
