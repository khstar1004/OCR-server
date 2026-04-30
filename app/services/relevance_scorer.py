from __future__ import annotations

from dataclasses import dataclass
import html
import json
import re
from typing import Any, Sequence

import httpx

from app.core.config import Settings, get_settings
from app.domain.types import ArticleCandidate
from app.services.runtime_config import runtime_config_value


@dataclass(frozen=True, slots=True)
class RelevanceAssessment:
    article_order: int
    score: float
    reason: str
    label: str
    source: str
    model: str | None = None
    corrected_title: str | None = None
    corrected_body_text: str | None = None
    correction_source: str | None = None
    correction_model: str | None = None


@dataclass(frozen=True, slots=True)
class PageRelevanceResult:
    assessments: dict[int, RelevanceAssessment]
    source: str
    model: str | None = None


class NationalAssemblyRelevanceScorer:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.api_key = self._clean_text(self.settings.llm_api_key)
        self.base_url = ""
        self.model_name = "gpt-oss-20b"
        self.timeout_sec = 20.0
        self._refresh_runtime_settings()

    def score_page_articles(
        self,
        *,
        pdf_name: str,
        page_number: int,
        articles: Sequence[ArticleCandidate],
    ) -> PageRelevanceResult:
        if not articles:
            return PageRelevanceResult(assessments={}, source="none", model=None)

        self._refresh_runtime_settings()
        prepared = [self._prepare_article(idx, article) for idx, article in enumerate(articles, start=1)]
        if not self.base_url:
            assessments = {item["article_order"]: self._heuristic_assessment(item) for item in prepared}
            return PageRelevanceResult(assessments=assessments, source="heuristic", model=None)

        try:
            parsed = self._call_llm(pdf_name=pdf_name, page_number=page_number, articles=prepared)
        except Exception:
            assessments = {item["article_order"]: self._heuristic_assessment(item) for item in prepared}
            return PageRelevanceResult(assessments=assessments, source="heuristic", model=None)

        assessments: dict[int, RelevanceAssessment] = {}
        used_heuristic = False
        for item in prepared:
            payload = parsed.get(item["article_order"])
            if payload is None:
                assessments[item["article_order"]] = self._heuristic_assessment(item)
                used_heuristic = True
                continue
            assessments[item["article_order"]] = self._assessment_from_payload(item, payload)

        llm_used = any(assessment.source == "llm" for assessment in assessments.values())
        if llm_used and used_heuristic:
            source = "mixed"
        elif llm_used:
            source = "llm"
        else:
            source = "heuristic"
        model = self.model_name if llm_used else None
        return PageRelevanceResult(assessments=assessments, source=source, model=model)

    def _refresh_runtime_settings(self) -> None:
        base_url = runtime_config_value("llm_base_url", self.settings.llm_base_url or "", self.settings)
        model_name = runtime_config_value("llm_model", self.settings.llm_model or "gpt-oss-20b", self.settings)
        timeout_sec = runtime_config_value("llm_timeout_sec", self.settings.llm_timeout_sec or 20.0, self.settings)
        self.base_url = self._normalize_base_url(str(base_url or ""))
        self.model_name = self._clean_text(str(model_name or "gpt-oss-20b")) or "gpt-oss-20b"
        try:
            self.timeout_sec = max(float(timeout_sec or 0.0), 5.0)
        except (TypeError, ValueError):
            self.timeout_sec = max(float(self.settings.llm_timeout_sec or 20.0), 5.0)

    def _call_llm(
        self,
        *,
        pdf_name: str,
        page_number: int,
        articles: Sequence[dict[str, Any]],
    ) -> dict[int, dict[str, Any]]:
        response = httpx.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json={
                "model": self.model_name,
                "messages": [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": self._user_prompt(pdf_name=pdf_name, page_number=page_number, articles=articles)},
                ],
                "temperature": 0,
                "max_tokens": 2400,
            },
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload = response.json()
        content = self._extract_content(payload)
        parsed = self._parse_json(content)
        return self._extract_article_payloads(parsed)

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a precise Korean newsroom post-editor and analyst. "
            "For each OCR-extracted news article, score National Assembly relevance and clean OCR spillover. "
            "Remove unrelated neighboring article text, tables, captions, credits, headers, footers, and duplicates when they clearly do not belong to the article. "
            "Do not invent facts. Preserve uncertain wording instead of guessing. "
            "Answer in Korean and return JSON only."
        )

    @staticmethod
    def _user_prompt(*, pdf_name: str, page_number: int, articles: Sequence[dict[str, Any]]) -> str:
        article_lines: list[str] = []
        for article in articles:
            article_lines.append(
                "\n".join(
                    [
                        f"article_order: {article['article_order']}",
                        f"title: {article['title']}",
                        f"body: {article['body']}",
                    ]
                )
            )
        article_block = "\n\n".join(article_lines)
        return (
            "다음 기사들이 육군 관련 뉴스 중에서 국회 업무와 얼마나 관련되는지 평가해줘.\n"
            "국회 업무는 국회에서 오는 요청에 대해 수치, 처리 경과, 결과, 기록, 현황, 통계, 자료제출, 질의답변, 보고를 응답하는 일이다.\n"
            "다음 키워드가 보이면 높은 관련성으로 판단하되, 기사 맥락을 우선해라: 국회, 국방위, 국방위원회, 국정감사, 자료제출, 질의, 답변, 보고, 처리 경과, 결과, 기록, 통계, 현황, 사고, 사망, 부상, 청문회.\n"
            "군사작전, 훈련, 장비 소개만 있고 국회 요청이나 보고 맥락이 없으면 낮게 평가해라.\n"
            "또한 OCR에 섞여 들어간 주변 기사 문장, 표 셀, 사진 설명, 기자명, 헤더/푸터, 중복 문장을 제거해서 corrected_title, corrected_body_text를 만들어라.\n"
            "제목/본문이 이미 깨끗하면 최소 정규화만 한 원문을 유지해라.\n"
            "추측으로 없는 사실을 보충하지 마라.\n"
            "각 기사에 대해 score(0.0~1.0), label(high/medium/low), reason(한 문장), matched_keywords(배열), corrected_title, corrected_body_text를 JSON만 반환해라.\n"
            '응답 형식: {"articles":[{"article_order":1,"score":0.82,"label":"high","reason":"...","matched_keywords":["국회","국방위"],"corrected_title":"...","corrected_body_text":"..."}]}\n\n'
            f"pdf_file: {pdf_name}\n"
            f"page_number: {page_number}\n\n"
            f"{article_block}"
        )

    @staticmethod
    def _extract_content(payload: Any) -> str:
        if isinstance(payload, dict):
            choices = payload.get("choices") or []
            if choices:
                first = choices[0] if isinstance(choices[0], dict) else {}
                message = first.get("message") or {}
                if isinstance(message, dict):
                    content = message.get("content")
                    if content is not None:
                        return str(content)
                content = first.get("text")
                if content is not None:
                    return str(content)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        candidate = text
        if not candidate.startswith("{"):
            start = candidate.find("{")
            end = candidate.rfind("}")
            if start >= 0 and end > start:
                candidate = candidate[start : end + 1]
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _extract_article_payloads(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
        items = payload.get("articles")
        if isinstance(items, list):
            result: dict[int, dict[str, Any]] = {}
            for item in items:
                if not isinstance(item, dict):
                    continue
                article_order = NationalAssemblyRelevanceScorer._coerce_int(
                    item.get("article_order") or item.get("index") or item.get("article_no")
                )
                if article_order is None:
                    continue
                result[article_order] = item
            return result

        if any(key in payload for key in {"score", "relevance_score", "similarity"}):
            article_order = NationalAssemblyRelevanceScorer._coerce_int(
                payload.get("article_order") or payload.get("index") or 1
            )
            if article_order is not None:
                return {article_order: payload}
        return {}

    def _assessment_from_payload(self, article: dict[str, Any], payload: dict[str, Any]) -> RelevanceAssessment:
        score = self._normalize_score(payload.get("score") or payload.get("relevance_score") or payload.get("similarity"))
        if score is None:
            return self._heuristic_assessment(article)
        reason = self._clean_text(payload.get("reason") or payload.get("explanation") or payload.get("summary"))
        if not reason:
            reason = "국회 업무와의 관련성을 LLM이 판단했습니다."
        label = self._normalize_label(payload.get("label") or payload.get("decision"), score)
        corrected_title = self._normalize_corrected_title(
            payload.get("corrected_title") or payload.get("edited_title") or payload.get("normalized_title"),
            fallback=article["title"],
        )
        corrected_body = self._normalize_corrected_body(
            payload.get("corrected_body_text")
            or payload.get("corrected_body")
            or payload.get("edited_body")
            or payload.get("normalized_body"),
            fallback=article["body"],
        )
        return RelevanceAssessment(
            article_order=article["article_order"],
            score=score,
            reason=reason,
            label=label,
            source="llm",
            model=self.model_name,
            corrected_title=corrected_title,
            corrected_body_text=corrected_body,
            correction_source="llm",
            correction_model=self.model_name,
        )

    def _heuristic_assessment(self, article: dict[str, Any]) -> RelevanceAssessment:
        text = f"{article['title']}\n{article['body']}"
        normalized = self._normalize_for_match(text)
        direct_keywords = [
            "국회",
            "국방위",
            "국방위원회",
            "국정감사",
            "자료제출",
            "질의",
            "답변",
            "보고",
            "청문회",
        ]
        admin_keywords = [
            "처리경과",
            "처리 경과",
            "결과",
            "기록",
            "현황",
            "통계",
            "수치",
            "예산",
        ]
        incident_keywords = [
            "사고",
            "사망",
            "부상",
            "실종",
            "훈련사고",
            "안전사고",
        ]
        matched_direct = self._find_keywords(normalized, direct_keywords)
        matched_admin = self._find_keywords(normalized, admin_keywords)
        matched_incident = self._find_keywords(normalized, incident_keywords)

        score = 0.05
        score += min(0.24, 0.18 * len(matched_direct))
        score += min(0.20, 0.10 * len(matched_admin))
        score += min(0.18, 0.09 * len(matched_incident))
        if "육군" in normalized or "군" in normalized:
            score += 0.04
        if matched_direct and any(keyword in matched_direct for keyword in {"국회", "국방위", "국방위원회", "국정감사"}):
            score += 0.12
        score = max(0.0, min(score, 0.98))
        label = self._label_for_score(score)
        reason = self._heuristic_reason(matched_direct, matched_admin, matched_incident, score)
        return RelevanceAssessment(
            article_order=article["article_order"],
            score=score,
            reason=reason,
            label=label,
            source="heuristic",
            model=None,
        )

    @staticmethod
    def _heuristic_reason(
        matched_direct: list[str],
        matched_admin: list[str],
        matched_incident: list[str],
        score: float,
    ) -> str:
        if matched_direct:
            keywords = ", ".join(matched_direct[:4])
            if matched_admin or matched_incident:
                return f"국회 관련 키워드({keywords})와 보고/사건 맥락이 함께 보여 국회 업무 연관성이 높습니다."
            return f"국회 관련 키워드({keywords})가 직접 보여 국회 업무와 연결됩니다."
        if matched_incident:
            keywords = ", ".join(matched_incident[:4])
            return f"사고/사망 관련 키워드({keywords})가 보여 국회 질의나 보고 가능성이 있습니다."
        if matched_admin:
            keywords = ", ".join(matched_admin[:4])
            if score >= 0.4:
                return f"처리 경과/결과/기록 키워드({keywords})가 보여 국회 응답 자료와 연결됩니다."
            return f"국회 요청 맥락은 약하지만 처리 경과/결과 키워드({keywords})가 일부 보입니다."
        return "국회 요청, 보고, 자료제출 맥락이 약해 일반 군사 뉴스에 가깝습니다."

    @staticmethod
    def _prepare_article(article_order: int, article: ArticleCandidate) -> dict[str, Any]:
        title = NationalAssemblyRelevanceScorer._clean_text(article.title, limit=220)
        body = NationalAssemblyRelevanceScorer._clean_text(
            article.body_text,
            limit=1800,
            preserve_newlines=True,
        )
        if not body:
            body = title
        return {
            "article_order": article_order,
            "title": title,
            "body": body,
        }

    @staticmethod
    def _clean_text(value: Any, *, limit: int | None = None, preserve_newlines: bool = False) -> str:
        if value is None:
            return ""
        text = html.unescape(str(value)).replace("\r", "\n")
        if preserve_newlines:
            normalized_lines = [re.sub(r"\s+", " ", line).strip() for line in text.split("\n")]
            text = "\n".join(line for line in normalized_lines if line)
        else:
            text = text.replace("\n", " ")
            text = re.sub(r"\s+", " ", text).strip()
        if limit is not None:
            text = text[:limit].strip()
        return text

    @staticmethod
    def _normalize_corrected_title(value: Any, *, fallback: str) -> str:
        normalized = NationalAssemblyRelevanceScorer._clean_text(value, limit=220)
        if normalized:
            return normalized
        return NationalAssemblyRelevanceScorer._clean_text(fallback, limit=220)

    @staticmethod
    def _normalize_corrected_body(value: Any, *, fallback: str) -> str:
        normalized = NationalAssemblyRelevanceScorer._clean_text(
            value,
            limit=2000,
            preserve_newlines=True,
        )
        if normalized:
            return normalized
        return NationalAssemblyRelevanceScorer._clean_text(
            fallback,
            limit=2000,
            preserve_newlines=True,
        )

    @staticmethod
    def _normalize_for_match(value: str) -> str:
        return re.sub(r"\s+", "", html.unescape(value))

    @staticmethod
    def _find_keywords(text: str, keywords: Sequence[str]) -> list[str]:
        matches: list[str] = []
        for keyword in keywords:
            normalized = re.sub(r"\s+", "", keyword)
            if normalized and normalized in text and keyword not in matches:
                matches.append(keyword)
        return matches

    @staticmethod
    def _normalize_score(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            score = float(str(value).strip())
        except (TypeError, ValueError):
            return None
        if score > 1.0 and score <= 100.0:
            score /= 100.0
        return max(0.0, min(score, 1.0))

    @staticmethod
    def _normalize_label(value: Any, score: float) -> str:
        if isinstance(value, str):
            label = value.strip().lower()
            if label in {"high", "medium", "low"}:
                return label
        return NationalAssemblyRelevanceScorer._label_for_score(score)

    @staticmethod
    def _label_for_score(score: float) -> str:
        if score >= 0.75:
            return "high"
        if score >= 0.4:
            return "medium"
        return "low"

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _normalize_base_url(value: str | None) -> str:
        if not value:
            return ""
        base = value.strip().rstrip("/")
        if not base:
            return ""
        if base.endswith("/models"):
            base = base[: -len("/models")]
        if "/v1" not in base:
            base = f"{base}/v1"
        return base.rstrip("/")
