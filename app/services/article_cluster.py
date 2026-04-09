from __future__ import annotations

import html
import re
from typing import Any

from app.domain.types import ArticleCandidate, BlockLabel, CaptionCandidate, ImageCandidate, OCRBlock, PageLayout
from app.utils.geometry import bbox_area, bbox_center, bbox_distance, bbox_height, bbox_union, box_horizontal_overlap_ratio

_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
_BR_TAG_PATTERN = re.compile(r"<\s*br\s*/?\s*>", re.IGNORECASE)


class ArticleClusterer:
    def cluster_page(self, page: PageLayout) -> tuple[list[ArticleCandidate], list[OCRBlock]]:
        source_metadata_blocks = self._collect_source_metadata_blocks(page)
        source_metadata_ids = {block.block_id for block in source_metadata_blocks}
        textish = self._collect_text_blocks(page, excluded_ids=source_metadata_ids)
        images = [block for block in page.blocks if block.label == BlockLabel.IMAGE and not self._is_excluded_image(block, page)]
        columns = self._assign_columns(textish, page.width)
        column_bboxes = self._column_bboxes(columns)
        title_blocks = [
            block
            for block in textish
            if block.label == BlockLabel.TITLE and not self._is_auxiliary_title_marker(block.text)
        ]
        title_blocks = self._filter_embedded_title_blocks(title_blocks, textish, column_bboxes)
        title_groups = self._merge_adjacent_titles(title_blocks, context_blocks=page.blocks)

        if title_groups:
            articles, assigned_ids = self._cluster_with_titles(page, columns, title_groups)
        else:
            articles, assigned_ids = self._cluster_without_titles(page, columns)

        self._attach_images(articles, images)

        unassigned = [
            block
            for block in textish
            if block.block_id not in assigned_ids and not self._is_auxiliary_title_marker(block.text)
        ]
        unassigned = self._assign_cross_column_unassigned(articles, unassigned)

        articles = self._merge_image_led_fragments(articles, page)
        articles = self._merge_article_fragments(articles)
        articles = [self._rebuild_article(article) for article in articles]
        articles = [article for article in articles if self._article_has_meaningful_content(article)]
        articles = sorted(
            articles,
            key=lambda article: (
                article.article_bbox[1],
                article.column_index if article.column_index is not None else 0,
                article.article_bbox[0],
            ),
        )
        articles = [self._enrich_article_source_metadata(article, source_metadata_blocks, page) for article in articles]
        return articles, unassigned

    def _collect_text_blocks(self, page: PageLayout, *, excluded_ids: set[str] | None = None) -> list[OCRBlock]:
        excluded_ids = excluded_ids or set()
        return [
            block
            for block in page.blocks
            if block.label in {BlockLabel.TITLE, BlockLabel.TEXT, BlockLabel.CAPTION}
            and block.block_id not in excluded_ids
            and not self._is_noise_text(block, page)
        ]

    def _collect_source_metadata_blocks(self, page: PageLayout) -> list[OCRBlock]:
        blocks: list[OCRBlock] = []
        for block in page.blocks:
            if block.label not in {BlockLabel.TITLE, BlockLabel.TEXT, BlockLabel.CAPTION, BlockLabel.HEADER}:
                continue
            kind = self._metadata_block_kind(block.text)
            if kind is None:
                continue
            block.metadata["source_metadata_kind"] = kind
            blocks.append(block)
        return sorted(blocks, key=lambda block: (block.bbox[1], block.bbox[0], block.block_id))

    def _enrich_article_source_metadata(
        self,
        article: ArticleCandidate,
        source_metadata_blocks: list[OCRBlock],
        page: PageLayout,
    ) -> ArticleCandidate:
        if not source_metadata_blocks:
            return article

        title_bbox = article.title_bbox or article.article_bbox
        publication_candidates: list[tuple[float, OCRBlock]] = []
        issue_candidates: list[tuple[float, OCRBlock]] = []

        for block in source_metadata_blocks:
            score = self._score_source_metadata_block(block, title_bbox, page)
            if score == float("-inf"):
                continue
            kind = str(block.metadata.get("source_metadata_kind") or self._metadata_block_kind(block.text) or "")
            if kind == "publication":
                publication_candidates.append((score, block))
            elif kind == "issue":
                issue_candidates.append((score, block))

        source_metadata: dict[str, Any] = {}
        if publication_candidates:
            _, publication_block = max(
                publication_candidates,
                key=lambda item: (item[0], item[1].bbox[1], -item[1].bbox[0]),
            )
            source_metadata.update(
                {
                    "publication": self._normalize_text(publication_block.text),
                    "raw_publication_text": self._normalize_text(publication_block.text),
                    "publication_bbox": publication_block.bbox[:],
                }
            )

        issue_metadata = self._resolve_issue_metadata(title_bbox, issue_candidates)
        if issue_metadata:
            source_metadata.update(issue_metadata)

        if not source_metadata:
            return article

        return ArticleCandidate(
            page_number=article.page_number,
            column_index=article.column_index,
            title=article.title,
            body_text=article.body_text,
            title_bbox=article.title_bbox,
            article_bbox=article.article_bbox,
            confidence=article.confidence,
            layout_type=article.layout_type,
            blocks=article.blocks[:],
            images=article.images[:],
            metadata={**article.metadata, "source_metadata": source_metadata},
        )

    @staticmethod
    def _strip_markup_text(text: str) -> str:
        cleaned = html.unescape(str(text or "")).replace("\r", "\n")
        cleaned = _BR_TAG_PATTERN.sub("\n", cleaned)
        cleaned = _HTML_TAG_PATTERN.sub(" ", cleaned)
        cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
        cleaned = re.sub(r"\n[ \t]+", "\n", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    @staticmethod
    def _normalize_text(text: str) -> str:
        return " ".join(ArticleClusterer._strip_markup_text(text).replace("\n", " ").split())

    @classmethod
    def _metadata_block_kind(cls, text: str) -> str | None:
        normalized = cls._normalize_text(text)
        if not normalized:
            return None
        if cls._is_page_metadata_text(normalized):
            return "issue"
        if cls._looks_like_publication_header(normalized):
            return "publication"
        return None

    @staticmethod
    def _score_source_metadata_block(block: OCRBlock, title_bbox: list[int], page: PageLayout) -> float:
        vertical_limit = title_bbox[1] + max(60, int(page.height * 0.02))
        if block.bbox[1] > vertical_limit:
            return float("-inf")

        horizontal_overlap = box_horizontal_overlap_ratio(block.bbox, title_bbox)
        block_center_x, _ = bbox_center(block.bbox)
        title_center_x, _ = bbox_center(title_bbox)
        center_span = max(title_bbox[2] - title_bbox[0], block.bbox[2] - block.bbox[0], 1)
        center_alignment = 1.0 - (abs(block_center_x - title_center_x) / center_span)
        vertical_gap = max(title_bbox[1] - block.bbox[3], 0)
        max_gap = max(260, int(page.height * 0.16))
        if vertical_gap > max_gap and horizontal_overlap < 0.18:
            return float("-inf")

        return (horizontal_overlap * 4.0) + max(center_alignment, -1.0) - (vertical_gap / max(max_gap, 1))

    def _resolve_issue_metadata(
        self,
        title_bbox: list[int],
        issue_candidates: list[tuple[float, OCRBlock]],
    ) -> dict[str, Any] | None:
        if not issue_candidates:
            return None

        ranked = sorted(
            issue_candidates,
            key=lambda item: (
                -(item[0]),
                max(title_bbox[1] - item[1].bbox[3], 0),
                item[1].bbox[0],
            ),
        )
        anchor_block = ranked[0][1]
        anchor_gap = max(title_bbox[1] - anchor_block.bbox[3], 0)
        nearby_blocks = [
            block
            for _, block in ranked
            if max(title_bbox[1] - block.bbox[3], 0) <= anchor_gap + 140
        ]
        ordered_blocks = sorted(
            nearby_blocks,
            key=lambda block: (block.bbox[1], block.bbox[0], block.block_id),
        )[:3]

        best_payload: dict[str, Any] | None = None
        best_score = -1
        for start in range(len(ordered_blocks)):
            combined_text = ""
            combined_boxes: list[list[int]] = []
            for end in range(start, min(len(ordered_blocks), start + 3)):
                combined_text = f"{combined_text} {self._normalize_text(ordered_blocks[end].text)}".strip()
                combined_boxes.append(ordered_blocks[end].bbox)
                parsed = self._parse_issue_metadata(combined_text)
                if parsed is None:
                    continue
                completeness = (
                    int(bool(parsed.get("issue_date")))
                    + int(bool(parsed.get("issue_page")))
                    + int(bool(parsed.get("issue_section")))
                )
                if completeness > best_score:
                    best_payload = {
                        **parsed,
                        "raw_issue_text": combined_text,
                        "issue_bbox": bbox_union(combined_boxes),
                    }
                    best_score = completeness
        return best_payload

    @classmethod
    def _parse_issue_metadata(cls, text: str) -> dict[str, Any] | None:
        normalized = cls._normalize_text(text)
        if not normalized or len(normalized) > 96:
            return None

        match = re.search(
            r"(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일(?:\s*(?P<weekday>월요일|화요일|수요일|목요일|금요일|토요일|일요일))?",
            normalized,
        )
        if match is None:
            return None

        year = int(match.group("year"))
        month = int(match.group("month"))
        day = int(match.group("day"))
        weekday = (match.group("weekday") or "").strip() or None
        rest = normalized[match.end() :].strip()

        payload: dict[str, Any] = {
            "issue_date": f"{year:04d}-{month:02d}-{day:02d}",
            "issue_date_text": f"{year}년 {month}월 {day}일" + (f" {weekday}" if weekday else ""),
            "issue_weekday": weekday,
        }
        if not rest:
            return payload

        page_match = re.match(r"(?P<page>[A-Za-z]?\d{1,3})\s*면(?:\s*(?P<section>.+))?$", rest)
        if page_match is None:
            page_only_match = re.match(r"(?P<page>[A-Za-z]?\d{1,3})$", rest)
            if page_only_match is None:
                return payload
            page_value = page_only_match.group("page").strip()
            payload["issue_page"] = page_value
            payload["issue_page_label"] = f"{page_value}면"
            return payload

        page_value = page_match.group("page").strip()
        section = cls._normalize_text(page_match.group("section") or "") or None
        payload["issue_page"] = page_value
        payload["issue_page_label"] = f"{page_value}면"
        payload["issue_section"] = section
        return payload

    def _cluster_with_titles(
        self,
        page: PageLayout,
        columns: dict[int, list[OCRBlock]],
        title_groups: list[OCRBlock],
    ) -> tuple[list[ArticleCandidate], set[str]]:
        articles: list[ArticleCandidate] = []
        assigned_ids: set[str] = set()
        column_bboxes = self._column_bboxes(columns)

        title_infos = [
            {
                "block": title_block,
                "primary_column": int(title_block.metadata.get("column_index", 0)),
                "span_columns": self._title_span_columns(title_block, column_bboxes),
            }
            for title_block in sorted(title_groups, key=lambda block: (block.bbox[1], block.bbox[0]))
        ]
        all_blocks = sorted(
            [block for blocks in columns.values() for block in blocks if block.label != BlockLabel.TITLE],
            key=lambda block: (block.bbox[1], block.bbox[0]),
        )

        for index, title_info in enumerate(title_infos):
            title_block = title_info["block"]
            span_columns = title_info["span_columns"]
            next_title_top = self._next_competing_title_top(title_infos, index)
            candidate_blocks: list[OCRBlock] = [title_block]

            for block in all_blocks:
                if block.block_id == title_block.block_id:
                    continue
                if block.block_id in assigned_ids:
                    continue
                if block.bbox[1] < title_block.bbox[1]:
                    continue
                if next_title_top is not None and block.bbox[1] >= next_title_top:
                    continue
                if not self._block_matches_title_span(block, title_block, span_columns, column_bboxes):
                    continue
                candidate_blocks.append(block)

            candidate_blocks = self._trim_after_large_gap(
                sorted(candidate_blocks, key=lambda block: (block.bbox[1], block.bbox[0]))
            )
            if not candidate_blocks:
                continue

            primary_column = min(
                int(block.metadata.get("column_index", title_info["primary_column"])) for block in candidate_blocks
            )
            articles.append(
                self._create_article(
                    page_number=page.page_number,
                    column_index=primary_column,
                    blocks=candidate_blocks,
                    layout_type=f"column_{primary_column + 1}",
                )
            )
            for block in candidate_blocks:
                assigned_ids.add(block.block_id)
                assigned_ids.update(str(item) for item in block.metadata.get("merged_block_ids", []) if str(item).strip())

        return articles, assigned_ids

    def _cluster_without_titles(
        self,
        page: PageLayout,
        columns: dict[int, list[OCRBlock]],
    ) -> tuple[list[ArticleCandidate], set[str]]:
        articles: list[ArticleCandidate] = []
        assigned_ids: set[str] = set()

        for column_index, blocks in sorted(columns.items()):
            ordered = sorted(blocks, key=lambda block: (block.bbox[1], block.bbox[0]))
            if not ordered:
                continue
            segments = self._split_column_segments(ordered)
            for segment in segments:
                if not segment:
                    continue
                articles.append(
                    self._create_article(
                        page_number=page.page_number,
                        column_index=column_index,
                        blocks=segment,
                        layout_type=f"fallback_column_{column_index + 1}",
                    )
                )
                assigned_ids.update(block.block_id for block in segment)

        return articles, assigned_ids

    def _create_article(
        self,
        *,
        page_number: int,
        column_index: int | None,
        blocks: list[OCRBlock],
        layout_type: str,
    ) -> ArticleCandidate:
        article = ArticleCandidate(
            page_number=page_number,
            column_index=column_index,
            title="",
            body_text="",
            title_bbox=None,
            article_bbox=bbox_union([block.bbox for block in blocks]),
            confidence=self._confidence_for_blocks(blocks),
            layout_type=layout_type,
            blocks=blocks[:],
        )
        return self._rebuild_article(article)

    def _rebuild_article(self, article: ArticleCandidate) -> ArticleCandidate:
        ordered_blocks = self._sort_blocks(article.blocks)
        title_block = self._select_title_block(ordered_blocks, has_images=bool(article.images))
        body_blocks = self._body_blocks_for_article(ordered_blocks, title_block, has_images=bool(article.images))
        caption_candidates = self._caption_candidates_for_article(
            ordered_blocks,
            title_block,
            has_images=bool(article.images),
        )
        images = self._attach_captions_to_images(article.images, caption_candidates)
        title_text = self._article_title_text(ordered_blocks, title_block)
        title_bbox = title_block.bbox if title_block is not None else (ordered_blocks[0].bbox if ordered_blocks else None)
        all_boxes = [block.bbox for block in ordered_blocks] + [image.bbox for image in images]
        article_bbox = bbox_union(all_boxes) if all_boxes else article.article_bbox
        body_text = self._join_block_text(body_blocks)
        if not body_text:
            body_text = self._fallback_body_text_from_images(images)
        resolved_column = article.column_index
        if resolved_column is None and ordered_blocks:
            resolved_column = min(int(block.metadata.get("column_index", 0)) for block in ordered_blocks)

        return ArticleCandidate(
            page_number=article.page_number,
            column_index=resolved_column,
            title=title_text,
            body_text=body_text,
            title_bbox=title_bbox,
            article_bbox=article_bbox,
            confidence=self._confidence_for_blocks(ordered_blocks),
            layout_type=article.layout_type,
            blocks=ordered_blocks,
            images=images,
            metadata=article.metadata.copy(),
        )

    def _assign_columns(self, blocks: list[OCRBlock], page_width: int) -> dict[int, list[OCRBlock]]:
        tolerance = max(int(page_width * 0.05), 40)
        groups: list[dict[str, int | list[OCRBlock]]] = []
        seed_blocks = [block for block in blocks if block.label != BlockLabel.TITLE] or blocks
        for block in sorted(seed_blocks, key=lambda item: (item.bbox[0], item.bbox[1])):
            x0 = block.bbox[0]
            placed = False
            for group in groups:
                mean_x = int(group["mean_x"])
                if abs(x0 - mean_x) <= tolerance:
                    members = group["blocks"]
                    assert isinstance(members, list)
                    members.append(block)
                    group["mean_x"] = sum(item.bbox[0] for item in members) // len(members)
                    placed = True
                    break
            if not placed:
                groups.append({"mean_x": x0, "blocks": [block]})

        ordered_groups = sorted(groups, key=lambda item: int(item["mean_x"]))
        columns: dict[int, list[OCRBlock]] = {}
        for index, group in enumerate(ordered_groups):
            members = group["blocks"]
            assert isinstance(members, list)
            for block in members:
                block.metadata["column_index"] = index
            columns[index] = members

        for block in blocks:
            if block in seed_blocks:
                continue
            column_index = self._best_matching_column(block, columns)
            if column_index is None:
                column_index = len(columns)
                columns[column_index] = []
            block.metadata["column_index"] = column_index
            columns[column_index].append(block)
        return columns

    def _filter_embedded_title_blocks(
        self,
        title_blocks: list[OCRBlock],
        textish_blocks: list[OCRBlock],
        column_bboxes: dict[int, list[int]],
    ) -> list[OCRBlock]:
        if not title_blocks:
            return []

        ordered_titles = sorted(title_blocks, key=lambda block: (block.bbox[1], block.bbox[0]))
        body_blocks = [
            block
            for block in textish_blocks
            if block.label == BlockLabel.TEXT
            and block.text.strip()
            and not self._is_credit_text(block.text)
            and not self._is_non_article_title(block.text)
        ]

        kept_titles: list[OCRBlock] = []
        for block in ordered_titles:
            block.metadata.pop("embedded_title", None)
            if kept_titles and self._is_embedded_title_block(block, kept_titles, body_blocks, column_bboxes):
                block.metadata["embedded_title"] = True
                continue
            kept_titles.append(block)
        return kept_titles

    def _is_embedded_title_block(
        self,
        title_block: OCRBlock,
        earlier_titles: list[OCRBlock],
        body_blocks: list[OCRBlock],
        column_bboxes: dict[int, list[int]],
    ) -> bool:
        span_columns = self._title_span_columns(title_block, column_bboxes)
        related_earlier_titles = [
            block
            for block in earlier_titles
            if self._title_span_columns(block, column_bboxes).intersection(span_columns)
            or box_horizontal_overlap_ratio(block.bbox, title_block.bbox) >= 0.18
        ]
        if not related_earlier_titles:
            return False

        direct_body_blocks = [
            block
            for block in body_blocks
            if box_horizontal_overlap_ratio(block.bbox, title_block.bbox) >= 0.12
            or int(block.metadata.get("column_index", -1)) == int(title_block.metadata.get("column_index", -2))
        ]
        if self._body_continues_through_title(title_block, direct_body_blocks, max_preceding_gap=100, max_following_gap=120):
            pass
        else:
            span_body_blocks = [
                block
                for block in body_blocks
                if self._block_matches_title_span(block, title_block, span_columns, column_bboxes)
            ]
            latest_related_title_bottom = max(block.bbox[3] for block in related_earlier_titles)
            if title_block.bbox[1] - latest_related_title_bottom <= 320:
                return False
            if not self._body_continues_through_title(
                title_block,
                span_body_blocks,
                max_preceding_gap=110,
                max_following_gap=120,
            ):
                return False

        title_width = max(title_block.bbox[2] - title_block.bbox[0], 1)
        earlier_width = max(
            max(block.bbox[2] - block.bbox[0], 1)
            for block in related_earlier_titles
        )
        normalized_text = self._normalize_text(title_block.text)
        if title_width > earlier_width * 0.95 and len(normalized_text) > 110 and not normalized_text.startswith(("◆", "■")):
            return False
        return True

    @staticmethod
    def _body_continues_through_title(
        title_block: OCRBlock,
        body_blocks: list[OCRBlock],
        *,
        max_preceding_gap: int,
        max_following_gap: int,
    ) -> bool:
        if not body_blocks:
            return False

        preceding_blocks = [block for block in body_blocks if block.bbox[1] < title_block.bbox[1]]
        following_blocks = [block for block in body_blocks if block.bbox[3] > title_block.bbox[1]]
        if not preceding_blocks or not following_blocks:
            return False

        nearest_preceding = max(preceding_blocks, key=lambda block: block.bbox[3])
        nearest_following = min(following_blocks, key=lambda block: block.bbox[1])
        if title_block.bbox[1] - nearest_preceding.bbox[3] > max_preceding_gap:
            return False
        if nearest_following.bbox[1] - title_block.bbox[3] > max_following_gap:
            return False
        return True

    def _merge_adjacent_titles(
        self,
        title_blocks: list[OCRBlock],
        *,
        context_blocks: list[OCRBlock] | None = None,
    ) -> list[OCRBlock]:
        titles = sorted(
            [
                block
                for block in title_blocks
                if block.label == BlockLabel.TITLE and not self._is_auxiliary_title_marker(block.text)
            ],
            key=lambda block: (block.bbox[1], block.bbox[0]),
        )
        merged: list[OCRBlock] = []
        current: OCRBlock | None = None
        for block in titles:
            if current is None:
                current = block
                continue
            gap = block.bbox[1] - current.bbox[3]
            strong_overlap = box_horizontal_overlap_ratio(current.bbox, block.bbox) >= 0.5
            base_gap_limit = max(20, int(max(bbox_height(current.bbox), bbox_height(block.bbox)) * 0.8))
            if self._has_intervening_body_gap(context_blocks or title_blocks, current, block):
                current_width = max(current.bbox[2] - current.bbox[0], 1)
                block_width = max(block.bbox[2] - block.bbox[0], 1)
                wide_lead = current_width >= block_width * 2.0
                subordinate_like = block_width <= current_width * 0.45 or len(block.text.strip()) <= 48
                relaxed_gap_limit = max(base_gap_limit, 160)
                if wide_lead and subordinate_like:
                    relaxed_gap_limit = max(relaxed_gap_limit, 280)
            else:
                relaxed_gap_limit = base_gap_limit
            if gap <= relaxed_gap_limit and strong_overlap:
                merged_block_ids = set(str(item) for item in current.metadata.get("merged_block_ids", [current.block_id]))
                merged_block_ids.update(str(item) for item in block.metadata.get("merged_block_ids", [block.block_id]))
                current.text = f"{current.text} {block.text}".strip()
                current.bbox = bbox_union([current.bbox, block.bbox])
                current.confidence = max(current.confidence, block.confidence)
                current.metadata["column_index"] = min(
                    int(current.metadata.get("column_index", 0)),
                    int(block.metadata.get("column_index", 0)),
                )
                current.metadata["merged_block_ids"] = sorted(merged_block_ids)
            else:
                merged.append(current)
                current = block
        if current is not None:
            merged.append(current)
        return merged

    @staticmethod
    def _column_bboxes(columns: dict[int, list[OCRBlock]]) -> dict[int, list[int]]:
        return {
            index: bbox_union([block.bbox for block in members])
            for index, members in columns.items()
            if members
        }

    @staticmethod
    def _best_matching_column(block: OCRBlock, columns: dict[int, list[OCRBlock]]) -> int | None:
        column_bboxes = ArticleClusterer._column_bboxes(columns)
        if not column_bboxes:
            return None

        block_center_x, _ = bbox_center(block.bbox)
        best_index = None
        best_score = float("-inf")
        for index, column_bbox in column_bboxes.items():
            overlap = box_horizontal_overlap_ratio(block.bbox, column_bbox)
            column_center_x, _ = bbox_center(column_bbox)
            center_penalty = abs(block_center_x - column_center_x) / max(column_bbox[2] - column_bbox[0], 1)
            score = (overlap * 3.0) - center_penalty
            if best_index is None or score > best_score or (score == best_score and index < best_index):
                best_index = index
                best_score = score
        return best_index

    @staticmethod
    def _title_span_columns(title_block: OCRBlock, column_bboxes: dict[int, list[int]]) -> set[int]:
        if not column_bboxes:
            return {int(title_block.metadata.get("column_index", 0))}

        title_center_x, _ = bbox_center(title_block.bbox)
        matched = {
            index
            for index, column_bbox in column_bboxes.items()
            if box_horizontal_overlap_ratio(title_block.bbox, column_bbox) >= 0.18
            or column_bbox[0] <= title_center_x <= column_bbox[2]
        }
        if matched:
            return matched
        return {int(title_block.metadata.get("column_index", 0))}

    @staticmethod
    def _block_matches_title_span(
        block: OCRBlock,
        title_block: OCRBlock,
        span_columns: set[int],
        column_bboxes: dict[int, list[int]],
    ) -> bool:
        block_column = int(block.metadata.get("column_index", -1))
        if block_column in span_columns:
            return True
        if box_horizontal_overlap_ratio(title_block.bbox, block.bbox) >= 0.22:
            return True
        for index in span_columns:
            column_bbox = column_bboxes.get(index)
            if column_bbox is None:
                continue
            if box_horizontal_overlap_ratio(column_bbox, block.bbox) >= 0.22:
                return True
        return False

    @staticmethod
    def _next_competing_title_top(title_infos: list[dict[str, object]], current_index: int) -> int | None:
        current = title_infos[current_index]
        current_block = current["block"]
        current_span = current["span_columns"]

        boundaries: list[int] = []
        for other in title_infos[current_index + 1 :]:
            other_block = other["block"]
            other_span = other["span_columns"]
            if not isinstance(current_span, set) or not isinstance(other_span, set):
                continue
            if current_span.intersection(other_span) or box_horizontal_overlap_ratio(current_block.bbox, other_block.bbox) >= 0.18:
                boundaries.append(other_block.bbox[1])
        return min(boundaries) if boundaries else None

    @staticmethod
    def _has_intervening_body_gap(blocks: list[OCRBlock], current: OCRBlock, candidate: OCRBlock) -> bool:
        combined_bbox = bbox_union([current.bbox, candidate.bbox])
        for block in blocks:
            if block.block_id in {current.block_id, candidate.block_id}:
                continue
            if block.label == BlockLabel.TITLE:
                continue
            if block.label != BlockLabel.TEXT:
                continue
            if not block.text.strip():
                continue
            if ArticleClusterer._is_credit_text(block.text):
                continue
            if block.bbox[1] < current.bbox[3] or block.bbox[3] > candidate.bbox[1]:
                continue
            if box_horizontal_overlap_ratio(combined_bbox, block.bbox) >= 0.2:
                return False
        return True

    def _assign_cross_column_unassigned(self, articles: list[ArticleCandidate], unassigned: list[OCRBlock]) -> list[OCRBlock]:
        residual: list[OCRBlock] = []
        for block in unassigned:
            if self._is_auxiliary_title_marker(block.text):
                continue
            best = None
            best_distance = float("inf")
            for article in articles:
                distance = bbox_distance(article.article_bbox, block.bbox)
                overlap = box_horizontal_overlap_ratio(article.article_bbox, block.bbox)
                weighted = distance - (overlap * 100)
                if weighted < best_distance:
                    best_distance = weighted
                    best = article
            if best is not None:
                best.blocks.append(block)
                rebuilt = self._rebuild_article(best)
                best.title = rebuilt.title
                best.body_text = rebuilt.body_text
                best.title_bbox = rebuilt.title_bbox
                best.article_bbox = rebuilt.article_bbox
                best.confidence = rebuilt.confidence
                best.blocks = rebuilt.blocks
                best.images = rebuilt.images
                if best.column_index is None:
                    best.column_index = int(block.metadata.get("column_index", 0))
            else:
                residual.append(block)
        return residual

    def _attach_images(self, articles: list[ArticleCandidate], images: list[OCRBlock]) -> None:
        for image_block in images:
            best = None
            best_score = float("-inf")
            for article in articles:
                if self._is_metadata_only_stub(article):
                    continue
                score = self._score_image_for_article(image_block, article)
                if score > best_score:
                    best_score = score
                    best = article
            if best is None or best_score == float("-inf"):
                continue
            best.images.append(
                ImageCandidate(
                    block_id=image_block.block_id,
                    page_number=image_block.page_number,
                    bbox=image_block.bbox,
                    confidence=image_block.confidence,
                    metadata=image_block.metadata.copy(),
                )
            )
            rebuilt = self._rebuild_article(best)
            best.title = rebuilt.title
            best.body_text = rebuilt.body_text
            best.title_bbox = rebuilt.title_bbox
            best.article_bbox = rebuilt.article_bbox
            best.confidence = rebuilt.confidence
            best.blocks = rebuilt.blocks
            best.images = rebuilt.images

    @staticmethod
    def _score_image_for_article(image_block: OCRBlock, article: ArticleCandidate) -> float:
        overlap = box_horizontal_overlap_ratio(article.article_bbox, image_block.bbox)
        article_center_x, _ = bbox_center(article.article_bbox)
        image_center_x, _ = bbox_center(image_block.bbox)
        article_width = max(article.article_bbox[2] - article.article_bbox[0], 1)
        image_width = max(image_block.bbox[2] - image_block.bbox[0], 1)
        center_span = max(article_width, image_width, 1)
        center_alignment = 1.0 - (abs(article_center_x - image_center_x) / center_span)

        image_height = max(image_block.bbox[3] - image_block.bbox[1], 1)
        article_height = max(article.article_bbox[3] - article.article_bbox[1], 1)
        if image_block.bbox[3] <= article.article_bbox[1]:
            vertical_gap = article.article_bbox[1] - image_block.bbox[3]
            placement_bonus = 0.9
        elif image_block.bbox[1] >= article.article_bbox[3]:
            vertical_gap = image_block.bbox[1] - article.article_bbox[3]
            placement_bonus = -0.6
        else:
            vertical_gap = 0
            placement_bonus = 0.35

        max_gap = max(220, int(max(image_height, article_height) * 0.75))
        if vertical_gap > max_gap and overlap < 0.2:
            return float("-inf")

        title_bonus = 0.0
        if article.title_bbox is not None:
            title_overlap = box_horizontal_overlap_ratio(article.title_bbox, image_block.bbox)
            title_gap = image_block.bbox[1] - article.title_bbox[3]
            if title_overlap >= 0.35 and -40 <= title_gap <= max(260, int(image_height * 0.45)):
                title_bonus = 1.15

        content_bonus = 0.5 if article.body_text.strip() else 0.0
        return (
            (overlap * 4.5)
            + placement_bonus
            + title_bonus
            + max(center_alignment, -1.0)
            + content_bonus
            - (vertical_gap / max(max_gap, 1))
        )

    def _merge_image_led_fragments(self, articles: list[ArticleCandidate], page: PageLayout) -> list[ArticleCandidate]:
        if len(articles) <= 1:
            return articles

        ordered = sorted(
            articles,
            key=lambda article: (
                article.article_bbox[1],
                article.column_index if article.column_index is not None else 0,
                article.article_bbox[0],
            ),
        )
        merged: list[ArticleCandidate] = []
        consumed: set[int] = set()

        for index, article in enumerate(ordered):
            if index in consumed:
                continue
            current = article
            if self._is_image_led_article(current, page):
                for candidate_index in range(index + 1, len(ordered)):
                    if candidate_index in consumed:
                        continue
                    candidate = ordered[candidate_index]
                    if not self._should_merge_image_led(current, candidate, page):
                        if candidate.article_bbox[1] > current.article_bbox[3] + max(180, int(page.height * 0.06)):
                            break
                        continue
                    current = self._merge_two_articles(current, candidate, layout_type="image_led")
                    consumed.add(candidate_index)
            merged.append(current)

        return merged

    @staticmethod
    def _is_image_led_article(article: ArticleCandidate, page: PageLayout) -> bool:
        page_area = max(page.width * page.height, 1)
        for image in article.images:
            width = max(image.bbox[2] - image.bbox[0], 1)
            if width >= page.width * 0.55 or bbox_area(image.bbox) >= page_area * 0.05:
                return True
        return False

    @classmethod
    def _is_metadata_only_stub(cls, article: ArticleCandidate) -> bool:
        meaningful_blocks = [block for block in article.blocks if block.text.strip()]
        if not meaningful_blocks:
            return False
        if article.body_text.strip() or article.images:
            return False
        return all(cls._metadata_block_kind(block.text) is not None for block in meaningful_blocks)

    def _should_merge_image_led(self, base: ArticleCandidate, candidate: ArticleCandidate, page: PageLayout) -> bool:
        if candidate.images:
            return False
        if not self._is_image_led_article(base, page):
            return False

        dominant_bottom = max(image.bbox[3] for image in base.images) if base.images else base.article_bbox[3]
        dominant_bbox = max(base.images, key=lambda image: bbox_area(image.bbox)).bbox if base.images else base.article_bbox
        top_gap = candidate.article_bbox[1] - dominant_bottom
        horizontal_overlap = box_horizontal_overlap_ratio(dominant_bbox, candidate.article_bbox)
        title_length = len(candidate.title.strip())
        body_length = len(candidate.body_text.strip())

        if horizontal_overlap < 0.18:
            return False
        if top_gap > max(160, int(page.height * 0.045)):
            return False
        if title_length > 180 and body_length > 180:
            return False
        return True

    def _merge_two_articles(
        self,
        left: ArticleCandidate,
        right: ArticleCandidate,
        *,
        layout_type: str | None = None,
    ) -> ArticleCandidate:
        merged = ArticleCandidate(
            page_number=left.page_number,
            column_index=min(
                value for value in [left.column_index, right.column_index] if value is not None
            )
            if any(value is not None for value in [left.column_index, right.column_index])
            else None,
            title="",
            body_text="",
            title_bbox=None,
            article_bbox=bbox_union([left.article_bbox, right.article_bbox]),
            confidence=(left.confidence + right.confidence) / 2,
            layout_type=layout_type or left.layout_type,
            blocks=left.blocks[:] + right.blocks[:],
            images=left.images[:] + right.images[:],
            metadata={**left.metadata, **right.metadata},
        )
        return self._rebuild_article(merged)

    def _merge_article_fragments(self, articles: list[ArticleCandidate]) -> list[ArticleCandidate]:
        if len(articles) <= 1:
            return articles
        merged: list[ArticleCandidate] = []
        for article in sorted(
            articles,
            key=lambda item: (
                item.column_index if item.column_index is not None else 0,
                item.article_bbox[1],
                item.article_bbox[0],
            ),
        ):
            if not merged:
                merged.append(article)
                continue
            previous = merged[-1]
            same_column = previous.column_index == article.column_index
            close_gap = article.article_bbox[1] - previous.article_bbox[3] <= 50
            titleless = article.title.startswith("page_") or article.title.startswith("article_")
            tiny_body = len(article.body_text) <= 40
            if same_column and close_gap and (titleless or tiny_body):
                merged[-1] = self._merge_two_articles(previous, article)
            else:
                merged.append(article)
        return merged

    @staticmethod
    def _sort_blocks(blocks: list[OCRBlock]) -> list[OCRBlock]:
        return sorted(
            blocks,
            key=lambda block: (
                int(block.metadata.get("column_index", 0)),
                block.bbox[1],
                block.bbox[0],
            ),
        )

    def _select_title_block(self, blocks: list[OCRBlock], *, has_images: bool) -> OCRBlock | None:
        title_blocks = [
            block
            for block in blocks
            if block.label == BlockLabel.TITLE
            and block.text.strip()
            and not self._is_auxiliary_title_marker(block.text)
            and not self._is_non_article_title(block.text)
        ]
        if title_blocks:
            return sorted(title_blocks, key=lambda block: (block.bbox[1], block.bbox[0]))[0]

        caption_candidates = [
            block
            for block in blocks
            if block.label == BlockLabel.CAPTION
            and block.text.strip()
            and not self._is_credit_text(block.text)
            and (self._is_caption_title_candidate(block.text) or has_images)
        ]
        if has_images and caption_candidates:
            return sorted(caption_candidates, key=lambda block: (block.bbox[1], block.bbox[0]))[0]

        text_candidates = [
            block
            for block in blocks
            if block.label == BlockLabel.TEXT
            and not self._is_non_article_title(block.text)
            and self._is_short_title_candidate(block.text)
        ]
        if text_candidates:
            return sorted(text_candidates, key=lambda block: (block.bbox[1], block.bbox[0]))[0]

        return None

    def _body_blocks_for_article(
        self,
        blocks: list[OCRBlock],
        title_block: OCRBlock | None,
        *,
        has_images: bool,
    ) -> list[OCRBlock]:
        body_blocks: list[OCRBlock] = []
        for block in blocks:
            if title_block is not None and block.block_id == title_block.block_id:
                continue
            text = block.text.strip()
            if not text:
                continue
            if self._is_auxiliary_title_marker(text):
                continue
            if self._is_credit_text(text):
                continue
            if self._is_non_article_title(text):
                continue
            if has_images and block.label == BlockLabel.CAPTION:
                continue
            body_blocks.append(block)
        return body_blocks

    def _article_title_text(self, blocks: list[OCRBlock], title_block: OCRBlock | None) -> str:
        if title_block is not None and title_block.text.strip():
            text = self._strip_markup_text(title_block.text)
            if title_block.label == BlockLabel.CAPTION and len(text) > 80:
                return f"{text[:77].rstrip()}..."
            return text
        for block in blocks:
            text = self._strip_markup_text(block.text)
            if not text:
                continue
            if self._is_auxiliary_title_marker(text):
                continue
            if self._is_credit_text(text):
                continue
            if self._is_non_article_title(text):
                continue
            return text[:80] if text else f"article_{block.page_number}"
        return "Untitled"

    def _caption_candidates_for_article(
        self,
        blocks: list[OCRBlock],
        title_block: OCRBlock | None,
        *,
        has_images: bool,
    ) -> list[CaptionCandidate]:
        if not has_images:
            return []

        captions: list[CaptionCandidate] = []
        seen: set[tuple[str, int, int, int, int]] = set()
        for block in blocks:
            if block.label != BlockLabel.CAPTION:
                continue
            text = block.text.strip()
            if not text:
                continue
            if self._is_credit_text(text):
                continue
            cleaned = self._strip_trailing_credit(text)
            if not cleaned:
                continue
            key = (cleaned, *block.bbox)
            if key in seen:
                continue
            seen.add(key)
            captions.append(
                CaptionCandidate(
                    block_id=block.block_id,
                    page_number=block.page_number,
                    bbox=block.bbox[:],
                    text=cleaned,
                    confidence=block.confidence,
                    metadata={
                        **block.metadata,
                        "used_as_title": bool(title_block is not None and title_block.block_id == block.block_id),
                    },
                )
            )
        return sorted(captions, key=lambda item: (item.bbox[1], item.bbox[0], item.block_id))

    def _attach_captions_to_images(
        self,
        images: list[ImageCandidate],
        captions: list[CaptionCandidate],
    ) -> list[ImageCandidate]:
        if not images:
            return []
        if not captions:
            return [
                ImageCandidate(
                    block_id=image.block_id,
                    page_number=image.page_number,
                    bbox=image.bbox[:],
                    confidence=image.confidence,
                    metadata=image.metadata.copy(),
                    captions=[],
                )
                for image in images
            ]
        if len(images) == 1:
            image = images[0]
            return [
                ImageCandidate(
                    block_id=image.block_id,
                    page_number=image.page_number,
                    bbox=image.bbox[:],
                    confidence=image.confidence,
                    metadata=image.metadata.copy(),
                    captions=list(captions),
                )
            ]

        assignments: dict[int, list[CaptionCandidate]] = {index: [] for index in range(len(images))}
        for caption in captions:
            best_index = None
            best_score = float("-inf")
            for index, image in enumerate(images):
                score = self._score_caption_for_image(caption, image)
                if score > best_score:
                    best_score = score
                    best_index = index
            if best_index is None or best_score == float("-inf"):
                continue
            assignments[best_index].append(caption)

        attached: list[ImageCandidate] = []
        for index, image in enumerate(images):
            image_captions = sorted(assignments.get(index, []), key=lambda item: (item.bbox[1], item.bbox[0], item.block_id))
            attached.append(
                ImageCandidate(
                    block_id=image.block_id,
                    page_number=image.page_number,
                    bbox=image.bbox[:],
                    confidence=image.confidence,
                    metadata=image.metadata.copy(),
                    captions=image_captions,
                )
            )
        return attached

    @staticmethod
    def _score_caption_for_image(caption: CaptionCandidate, image: ImageCandidate) -> float:
        image_width = max(image.bbox[2] - image.bbox[0], 1)
        image_height = max(image.bbox[3] - image.bbox[1], 1)
        horizontal_padding = max(24, int(image_width * 0.18))
        if caption.bbox[2] < image.bbox[0] - horizontal_padding or caption.bbox[0] > image.bbox[2] + horizontal_padding:
            return float("-inf")

        overlap = box_horizontal_overlap_ratio(caption.bbox, image.bbox)
        if caption.bbox[1] >= image.bbox[3]:
            vertical_gap = caption.bbox[1] - image.bbox[3]
            placement_bonus = 0.45
        elif caption.bbox[3] <= image.bbox[1]:
            vertical_gap = image.bbox[1] - caption.bbox[3]
            placement_bonus = -0.2
        else:
            vertical_gap = 0
            placement_bonus = 0.2

        max_gap = max(160, int(image_height * 0.45))
        if vertical_gap > max_gap:
            return float("-inf")

        caption_center = (caption.bbox[0] + caption.bbox[2]) / 2
        image_center = (image.bbox[0] + image.bbox[2]) / 2
        alignment = 1.0 - (abs(caption_center - image_center) / image_width)
        return (overlap * 3.0) + placement_bonus + max(alignment, -1.0) - (vertical_gap / max(max_gap, 1))

    @staticmethod
    def _join_block_text(blocks: list[OCRBlock]) -> str:
        lines: list[str] = []
        for block in blocks:
            cleaned = ArticleClusterer._strip_trailing_credit(block.text.strip())
            if cleaned:
                lines.append(cleaned)
        return "\n".join(lines).strip()

    @staticmethod
    def _fallback_body_text_from_images(images: list[ImageCandidate]) -> str:
        lines: list[str] = []
        seen: set[str] = set()
        for image in images:
            for caption in image.captions:
                cleaned = ArticleClusterer._strip_trailing_credit(caption.text.strip())
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                lines.append(cleaned)
        return "\n".join(lines).strip()

    @staticmethod
    def _confidence_for_blocks(blocks: list[OCRBlock]) -> float:
        if not blocks:
            return 0.0
        return sum(block.confidence for block in blocks) / len(blocks)

    @staticmethod
    def _split_column_segments(blocks: list[OCRBlock]) -> list[list[OCRBlock]]:
        if not blocks:
            return []
        ordered = sorted(blocks, key=lambda block: (block.bbox[1], block.bbox[0]))
        if len(ordered) == 1:
            return [ordered]
        heights = [bbox_height(block.bbox) for block in ordered]
        baseline = sorted(heights)[len(heights) // 2] if heights else 24
        gap_threshold = max(int(baseline * 2.8), 70)
        segments: list[list[OCRBlock]] = [[ordered[0]]]
        for previous, current in zip(ordered, ordered[1:]):
            gap = current.bbox[1] - previous.bbox[3]
            if gap > gap_threshold:
                segments.append([current])
            else:
                segments[-1].append(current)
        return segments

    @staticmethod
    def _trim_after_large_gap(blocks: list[OCRBlock]) -> list[OCRBlock]:
        if len(blocks) <= 2:
            return blocks
        heights = [bbox_height(block.bbox) for block in blocks]
        baseline = sorted(heights)[len(heights) // 2] if heights else 24
        gap_threshold = max(int(baseline * 3.2), 90)
        trimmed = [blocks[0]]
        for previous, current in zip(blocks, blocks[1:]):
            gap = current.bbox[1] - previous.bbox[3]
            if gap > gap_threshold and len(trimmed) >= 2 and current.label != BlockLabel.CAPTION:
                break
            trimmed.append(current)
        return trimmed

    def _is_noise_text(self, block: OCRBlock, page: PageLayout) -> bool:
        if block.label in {BlockLabel.HEADER, BlockLabel.FOOTER, BlockLabel.ADVERTISEMENT}:
            return True

        text = block.text.strip()
        if not text:
            return True
        if len(text) <= 2 and not any(ch.isalnum() for ch in text):
            return True
        if self._is_credit_text(text):
            return True
        if block.bbox[1] <= page.height * 0.14 and (
            self._is_page_metadata_text(text) or self._looks_like_publication_header(text)
        ):
            return True
        return False

    @staticmethod
    def _is_page_metadata_text(text: str) -> bool:
        normalized = ArticleClusterer._normalize_text(text)
        if not normalized or len(normalized) > 96:
            return False
        if normalized.startswith("면 "):
            return True
        if re.fullmatch(r"[A-Za-z]?\d{1,3}\s*면(?:\s+\S.*)?", normalized):
            return True
        date_match = re.search(r"\d{4}\s*년\s*\d{1,2}\s*월\s*\d{1,2}\s*일", normalized)
        if date_match is None:
            return False
        remainder = normalized[date_match.end() :].strip()
        if not remainder:
            return True
        if re.fullmatch(r"(월요일|화요일|수요일|목요일|금요일|토요일|일요일)", remainder):
            return True
        if re.fullmatch(r"(월요일|화요일|수요일|목요일|금요일|토요일|일요일)\s+[A-Za-z]?\d{1,3}", remainder):
            return True
        if re.fullmatch(r"(월요일|화요일|수요일|목요일|금요일|토요일|일요일)\s+[A-Za-z]?\d{1,3}\s*면(?:\s+\S.*)?", remainder):
            return True
        if re.fullmatch(r"[A-Za-z]?\d{1,3}", remainder):
            return True
        if re.fullmatch(r"[A-Za-z]?\d{1,3}\s*면(?:\s+\S.*)?", remainder):
            return True
        return False

    @staticmethod
    def _looks_like_publication_header(text: str) -> bool:
        normalized = ArticleClusterer._normalize_text(text)
        lowered = normalized.lower()
        if ".com" in lowered or ".co.kr" in lowered:
            return True
        if any(ch.isdigit() for ch in normalized) or "년" in normalized or "면" in normalized:
            return False
        if len(normalized) > 24:
            return False
        known_publication_like = {
            "한겨레",
            "머니투데이",
            "아시아투데이",
            "한국경제",
            "한국일보",
            "경향신문",
            "국민일보",
            "서울신문",
            "세계일보",
            "문화일보",
            "내일신문",
            "동아일보",
            "조선일보",
            "중앙일보",
        }
        if normalized in known_publication_like:
            return True
        return re.search(r"(일보|신문|경제|헤럴드|타임즈|저널|뉴스|투데이)$", normalized) is not None

    @classmethod
    def _is_non_article_title(cls, text: str) -> bool:
        normalized = cls._normalize_text(text)
        if not normalized:
            return True
        if cls._is_auxiliary_title_marker(normalized):
            return True
        if cls._looks_like_publication_header(normalized):
            return True
        if cls._is_page_metadata_text(normalized):
            return True
        return False

    @staticmethod
    def _is_credit_text(text: str) -> bool:
        normalized = ArticleClusterer._normalize_text(text)
        lowered = normalized.lower()
        if re.fullmatch(r"\[[^\]]+\]", normalized):
            return True
        if normalized in {"연합뉴스", "뉴시스", "뉴스1"}:
            return True
        if ".com" in lowered and len(normalized) <= 40:
            return True
        if len(normalized) <= 60 and any(token in normalized for token in [" 기자", " 특파원", " correspondent", " reporter"]):
            return True
        if "@" in normalized:
            return True
        return False

    @staticmethod
    def _strip_trailing_credit(text: str) -> str:
        normalized = ArticleClusterer._normalize_text(text)
        if not normalized:
            return ""
        patterns = [
            r"\s+[가-힣A-Za-z]{1,20}=[가-힣A-Za-z]{2,20}\s+(?:기자|특파원)\s*$",
            r"\s+[가-힣A-Za-z]{2,20}\s+(?:기자|특파원)\s*$",
        ]
        stripped = normalized
        for pattern in patterns:
            stripped = re.sub(pattern, "", stripped)
        return stripped.strip()

    @staticmethod
    def _is_caption_title_candidate(text: str) -> bool:
        normalized = ArticleClusterer._normalize_text(text)
        if not normalized:
            return False
        if len(normalized) > 64:
            return False
        if normalized.endswith((".", "다.", "했다.", "있다.", "였다.")):
            return False
        return True

    @staticmethod
    def _is_short_title_candidate(text: str) -> bool:
        normalized = ArticleClusterer._normalize_text(text)
        if not normalized:
            return False
        if len(normalized) > 72:
            return False
        if normalized.endswith((".", "다.", "했다.", "있다.", "였다.")):
            return False
        return True

    @staticmethod
    def _is_auxiliary_title_marker(text: str) -> bool:
        normalized = ArticleClusterer._normalize_text(text)
        if not normalized:
            return False
        return bool(re.search(r"^[▶▸▷►]\s*관련\s*기사\b", normalized)) or (
            "관련기사" in normalized and len(normalized) <= 20
        )

    @staticmethod
    def _article_has_meaningful_content(article: ArticleCandidate) -> bool:
        if ArticleClusterer._is_non_article_title(article.title) and not article.body_text.strip():
            return False
        if (
            not article.body_text.strip()
            and len(article.images) == 1
            and bbox_area(article.images[0].bbox) < 70000
            and not article.images[0].captions
        ):
            return False
        if article.body_text.strip():
            return True
        if article.images and article.title.strip():
            return True
        return False

    @staticmethod
    def _is_excluded_image(block: OCRBlock, page: PageLayout) -> bool:
        area = bbox_area(block.bbox)
        page_area = page.width * page.height
        if area < page_area * 0.002:
            return True
        width = max(block.bbox[2] - block.bbox[0], 1)
        height = max(block.bbox[3] - block.bbox[1], 1)
        aspect_ratio = width / height
        if aspect_ratio > 8 or aspect_ratio < 0.125:
            return True
        if block.bbox[1] < page.height * 0.04 or block.bbox[3] > page.height * 0.96:
            if width > page.width * 0.6 and height < page.height * 0.12:
                return True
        if block.metadata.get("layout_label") in {"logo", "advertisement", "ad"}:
            return True
        return False
