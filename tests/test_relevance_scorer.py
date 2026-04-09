from __future__ import annotations

import json

from app.core.config import Settings
from app.domain.types import ArticleCandidate
from app.services import relevance_scorer as scorer_module


def _candidate(*, title: str, body_text: str) -> ArticleCandidate:
    return ArticleCandidate(
        page_number=1,
        column_index=0,
        title=title,
        body_text=body_text,
        title_bbox=[10, 10, 200, 60],
        article_bbox=[10, 10, 400, 400],
        confidence=0.9,
        layout_type="article",
    )


def test_relevance_scorer_parses_llm_corrections(monkeypatch) -> None:
    settings = Settings(
        llm_base_url="http://llm.test/v1",
        llm_model="gpt-oss-20b",
        llm_timeout_sec=5.0,
    )
    scorer = scorer_module.NationalAssemblyRelevanceScorer(settings)

    class DummyResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "articles": [
                                        {
                                            "article_order": 1,
                                            "score": 0.91,
                                            "label": "high",
                                            "reason": "국회 보고와 자료제출 맥락이 직접 보입니다.",
                                            "matched_keywords": ["국회", "자료제출"],
                                            "corrected_title": "정리된 제목",
                                            "corrected_body_text": "첫 문장.\n둘째 문장.",
                                        }
                                    ]
                                },
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }

    monkeypatch.setattr(scorer_module.httpx, "post", lambda *args, **kwargs: DummyResponse())

    result = scorer.score_page_articles(
        pdf_name="demo.pdf",
        page_number=1,
        articles=[_candidate(title="원본 제목", body_text="첫 문장.\n표 셀\n둘째 문장.")],
    )

    assessment = result.assessments[1]
    assert result.source == "llm"
    assert assessment.source == "llm"
    assert assessment.corrected_title == "정리된 제목"
    assert assessment.corrected_body_text == "첫 문장.\n둘째 문장."
    assert assessment.correction_source == "llm"
    assert assessment.correction_model == "gpt-oss-20b"


def test_relevance_scorer_heuristic_fallback_leaves_corrections_empty() -> None:
    settings = Settings(
        llm_base_url=None,
        llm_model="gpt-oss-20b",
        llm_timeout_sec=5.0,
    )
    scorer = scorer_module.NationalAssemblyRelevanceScorer(settings)

    result = scorer.score_page_articles(
        pdf_name="demo.pdf",
        page_number=1,
        articles=[_candidate(title="일반 기사", body_text="군 훈련 소식입니다.")],
    )

    assessment = result.assessments[1]
    assert result.source == "heuristic"
    assert assessment.source == "heuristic"
    assert assessment.corrected_title is None
    assert assessment.corrected_body_text is None
    assert assessment.correction_source is None
    assert assessment.correction_model is None
