from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from app.domain.types import BlockLabel, OCRBlock, PageLayout
from app.services.ocr_engine import OCREngine
from app.ocr.types import OCRDocumentResult, OCRPageArtifacts, PageImageArtifact, RenderedPdf
from app.services.artifacts import build_job_artifact_layout
from app.services.article_cluster import ArticleClusterer
from app.services.ocr_pipeline import segment_pages


def test_newspaper_segmentation_returns_multiple_article_candidates(tmp_path) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "newspaper_page.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))

    pdf_path = tmp_path / "fixture-news.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    data_dir = tmp_path / "data"
    layout = build_job_artifact_layout(data_dir, "job-002", pdf_path)
    layout.ensure()

    page_image_path = layout.page_image_path(1)
    Image.new("RGB", (1000, 1400), color="white").save(page_image_path)

    json_path = layout.ocr_json_path(1)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    layout.ocr_markdown_path(1).write_text("fixture markdown", encoding="utf-8")
    layout.ocr_html_path(1).write_text("<p>fixture html</p>", encoding="utf-8")
    layout.ocr_metadata_path(1).write_text("{}", encoding="utf-8")

    rendered_pdf = RenderedPdf(
        pdf_path=pdf_path,
        job_id=layout.job_id,
        source_key=layout.source_key,
        artifact_root=layout.document_dir,
        page_dir=layout.pages_dir,
        pages=(
            PageImageArtifact(
                page_no=1,
                image_path=page_image_path,
                width=1000,
                height=1400,
                source_pdf=pdf_path,
                dpi=200,
            ),
        ),
    )
    ocr_result = OCRDocumentResult(
        pdf_path=pdf_path,
        job_id=layout.job_id,
        source_key=layout.source_key,
        method="hf",
        model_id="datalab-to/chandra-ocr-2",
        artifact_root=layout.document_dir,
        pages=(
            OCRPageArtifacts(
                page_no=1,
                image_path=page_image_path,
                markdown_path=layout.ocr_markdown_path(1),
                html_path=layout.ocr_html_path(1),
                json_path=json_path,
                metadata_path=layout.ocr_metadata_path(1),
                raw_payload=payload,
                metadata={},
            ),
        ),
    )

    page_results = segment_pages(rendered_pdf, ocr_result, data_dir, "job-002")

    assert len(page_results) == 1
    page_result = page_results[0]
    assert page_result.raw_ocr_path == json_path
    assert len(page_result.columns) == 2
    assert len(page_result.articles) == 3

    left_articles = [article for article in page_result.articles if article.article_bbox[0] < 200]
    right_articles = [article for article in page_result.articles if article.article_bbox[0] > 400]

    assert len(left_articles) == 2
    assert len(right_articles) == 1
    assert all(article.article_image_path.exists() for article in page_result.articles)
    assert all(article.preliminary_blocks for article in page_result.articles)
    assert all(article.raw_ocr_path == json_path for article in page_result.articles)
    assert all(article.metadata["column_index"] in {0, 1} for article in page_result.articles)


def test_article_clusterer_ignores_page_headers_when_selecting_title(tmp_path) -> None:
    page = PageLayout(
        page_number=1,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=[
            OCRBlock(
                block_id="header-1",
                page_number=1,
                label=BlockLabel.HEADER,
                bbox=[46, 239, 165, 263],
                text="문화일보",
            ),
            OCRBlock(
                block_id="header-2",
                page_number=1,
                label=BlockLabel.HEADER,
                bbox=[593, 240, 961, 258],
                text="2026년 2월 23일 월요일 008면 외교안보",
            ),
            OCRBlock(
                block_id="title-1",
                page_number=1,
                label=BlockLabel.TITLE,
                bbox=[82, 270, 920, 301],
                text="韓, 美와 훈련 축소 추진... 전작권 전환 ‘졸속 검증’ 우려",
            ),
            OCRBlock(
                block_id="text-1",
                page_number=1,
                label=BlockLabel.TEXT,
                bbox=[60, 334, 333, 355],
                text="전문가 “정부, 中·北 눈치보기”",
            ),
            OCRBlock(
                block_id="text-2",
                page_number=1,
                label=BlockLabel.TEXT,
                bbox=[55, 374, 340, 593],
                text="왼쪽 본문",
            ),
            OCRBlock(
                block_id="text-3",
                page_number=1,
                label=BlockLabel.TEXT,
                bbox=[653, 334, 952, 553],
                text="오른쪽 본문",
            ),
        ],
        raw_vl={},
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "韓, 美와 훈련 축소 추진... 전작권 전환 ‘졸속 검증’ 우려"
    assert article.title_bbox == [82, 270, 920, 301]
    assert article.article_bbox[1] == 270
    assert article.article_bbox[3] >= 553
    assert "문화일보" not in article.body_text


def test_article_clusterer_keeps_chandra_text_blocks_as_body_for_multi_column_article(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "header", "bbox": [52, 730, 365, 821], "content": "내일신문"},
            {"label": "header", "bbox": [1349, 730, 1659, 821], "content": "내일신문"},
            {"label": "header", "bbox": [513, 737, 1300, 800], "content": "2026년 2월 23일 월요일 001면 종합"},
            {"label": "header", "bbox": [1622, 814, 2435, 877], "content": "2026년 2월 23일 월요일 002면 정치"},
            {"label": "image", "bbox": [84, 832, 1277, 2477], "content": ""},
            {"label": "title", "bbox": [1374, 895, 2413, 993], "content": "한·브, 전략적 동반자관계 격상"},
            {
                "label": "text",
                "bbox": [1374, 1042, 2135, 1176],
                "content": "이 대통령, 롤라 브라질 대통령과 정상회담\n‘4개년 행동계획’ 채택, 10개 MOU 체결",
            },
            {
                "label": "text",
                "bbox": [1374, 1242, 1870, 1646],
                "content": "이재명 대통령은 23일 루이스 이나시우 롤라 다 시우바 브라질 대통령과 정상회담을 갖고 양국 관계를 ‘전략적 동반자관계’로 격상하기로 했다.",
            },
            {
                "label": "text",
                "bbox": [1890, 1242, 2413, 1544],
                "content": "중소기업 협력 MOU를 통해선 대기업 중심이던 양국 간 교역과 투자를 중소기업까지 확산시키고, 보건 분야 규제 협력을 통해 K-화장품 등 한국 제품의 브라질 진출을 확대하기로 했다.",
            },
            {
                "label": "text",
                "bbox": [1890, 1544, 2413, 1895],
                "content": "농업 분야에서는 식량안보와 디지털 농업, 농업기술 협력을 포함한 3건의 MOU를 체결했다.",
            },
            {
                "label": "text",
                "bbox": [1374, 1646, 1870, 2046],
                "content": "이 대통령은 이날 롤라 대통령과 함께 공동언론발표를 하며 오늘은 역사적인 날로 기록될 것이라고 말했다.",
            },
            {
                "label": "text",
                "bbox": [1890, 1895, 2413, 2197],
                "content": "우주·항공·방산 등 미래 산업 분야 협력도 확대하기로 했다.",
            },
            {
                "label": "text",
                "bbox": [1374, 2046, 1870, 2246],
                "content": "이어 이러한 굳건한 협력관계를 토대로 양국 관계를 전략적 동반자관계로 격상시키기로 했다고 밝혔다.",
            },
            {
                "label": "text",
                "bbox": [1890, 2197, 2413, 2548],
                "content": "또한 글로벌 사우스 지역의 주요 국가로서 기후변화 대응 등 국제 현안에 대해서도 긴밀히 협력하기로 했다.",
            },
            {
                "label": "text",
                "bbox": [1374, 2246, 1870, 2495],
                "content": "양국 정상은 특히 호혜적 경제협력 확대 필요성에 공감했다.",
            },
            {
                "label": "caption",
                "bbox": [79, 2488, 1282, 2597],
                "content": "분향하는 롤라 브라질 대통령 내외",
            },
            {
                "label": "text",
                "bbox": [1374, 2495, 1870, 2744],
                "content": "이번 정상회담을 계기로 양국은 총 10건의 양해각서(MOU)를 체결했다.",
            },
            {"label": "text", "bbox": [1890, 2548, 2413, 2695], "content": "양 정상은 개인적 유대감을 공유하고 있다."},
            {"label": "caption", "bbox": [1180, 2600, 1282, 2642], "content": "연합뉴스"},
            {"label": "caption", "bbox": [2046, 2698, 2413, 2744], "content": "김형선 기자 egoh@naeil.com"},
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=10, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=10,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "한·브, 전략적 동반자관계 격상"
    assert article.title_bbox == [1374, 895, 2413, 993]
    assert len(article.images) == 1
    assert [caption.text for caption in article.images[0].captions] == ["분향하는 롤라 브라질 대통령 내외"]
    assert "중소기업 협력 MOU" in article.body_text
    assert "우주·항공·방산 등 미래 산업 분야 협력" in article.body_text
    assert "이번 정상회담을 계기로 양국은 총 10건의 양해각서" in article.body_text
    assert "분향하는 롤라 브라질 대통령 내외" not in article.body_text
    assert "연합뉴스" not in article.body_text


def test_article_clusterer_merges_photo_caption_and_body_into_single_article(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "header", "bbox": [55, 1119, 528, 1218], "content": "헤럴드 경제\nheraldbiz.com"},
            {"label": "header", "bbox": [1622, 1126, 2440, 1193], "content": "2026년 2월 23일 월요일 001면 종합"},
            {"label": "image", "bbox": [77, 1225, 2413, 2235], "content": ""},
            {
                "label": "text",
                "bbox": [739, 2239, 2423, 2362],
                "content": "이재명 대통령과 루이스 이나시우 룰라 다시우바 브라질 대통령이 23일 청와대에서 열린 정상회담에 앞서 국빈 방한 공식환영식에 참가하고 있다.",
            },
            {"label": "caption", "bbox": [69, 2270, 704, 2330], "content": "李大統領 “룰라 대통령은 영원한 동지”"},
            {"label": "text", "bbox": [2316, 2323, 2423, 2362], "content": "[뉴스시스]"},
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=11, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=11,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "李大統領 “룰라 대통령은 영원한 동지”"
    assert len(article.images) == 1
    assert [caption.text for caption in article.images[0].captions] == ["李大統領 “룰라 대통령은 영원한 동지”"]
    assert "이재명 대통령과 루이스 이나시우 룰라 다시우바 브라질 대통령" in article.body_text
    assert "[뉴스시스]" not in article.body_text


def test_article_clusterer_ignores_publication_title_blocks_mislabeled_as_article_titles(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "title", "bbox": [893, 102, 1188, 186], "content": "문화일보"},
            {"label": "text", "bbox": [945, 189, 1580, 249], "content": "2026년 2월 23일 월요일 012"},
            {"label": "text", "bbox": [945, 253, 1158, 312], "content": "면 World"},
            {"label": "title", "bbox": [945, 344, 1525, 502], "content": "中 차세대 핵잠 진수 확인\n미국과 전력 격차 좁히기"},
            {
                "label": "text",
                "bbox": [915, 621, 1560, 1021],
                "content": "■ 중국 차세대 공격형 원자력추진잠수함(SSN) 095형(사진)이 진수된 모습이 처음으로 포착됐다.",
            },
            {
                "label": "text",
                "bbox": [915, 1028, 1560, 2944],
                "content": "22일 홍콩 사우스차이나모닝포스트(SCMP)는 영국 군사전문지 제인스와 해군 전문매체 네이벌 뉴스를 인용해 포착된 위성 사진을 보도했다.",
            },
            {"label": "image", "bbox": [923, 3028, 1545, 3351], "content": ""},
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=12, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=12,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "中 차세대 핵잠 진수 확인\n미국과 전력 격차 좁히기"
    assert "문화일보" not in article.body_text
    assert "2026년 2월 23일 월요일 012" not in article.body_text
    assert "면 World" not in article.body_text
    assert len(article.images) == 1


def test_article_clusterer_drops_publication_only_article_and_keeps_real_title(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "title", "bbox": [856, 418, 1163, 495], "content": "머니투데이"},
            {"label": "text", "bbox": [952, 502, 1562, 621], "content": "2026년 1월 2일 금요일 014면 the300"},
            {"label": "title", "bbox": [925, 660, 1543, 860], "content": "李, 4~7일 국빈 방중\n“한중 협력 진전 기대”"},
            {"label": "title", "bbox": [1002, 912, 1463, 979], "content": "양국 ‘외교장관’ 통화"},
            {
                "label": "text",
                "bbox": [893, 1039, 1572, 1284],
                "content": "이재명 대통령의 중국 국빈방문을 앞두고 한중 외교장관이 전화통화했다.",
            },
            {
                "label": "text",
                "bbox": [893, 1288, 1572, 1663],
                "content": "조현 외교부 장관은 왕이 중국 외교부장과 통화하고 국빈 방중을 논의했다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=27, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=27,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "李, 4~7일 국빈 방중\n“한중 협력 진전 기대” 양국 ‘외교장관’ 통화"
    assert "이재명 대통령의 중국 국빈방문" in article.body_text
    assert "머니투데이" not in article.title


def test_article_clusterer_uses_caption_as_title_when_only_publication_title_exists(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "title", "bbox": [474, 1239, 831, 1326], "content": "한국경제"},
            {"label": "text", "bbox": [1228, 1242, 2004, 1312], "content": "2026년 1월 2일 금요일 A23면 비즈"},
            {"label": "image", "bbox": [518, 1358, 1942, 2039], "content": ""},
            {
                "label": "caption",
                "bbox": [511, 2046, 1964, 2214],
                "content": "하나은행, 군 장병과 새해 첫 출발 나라사랑카드 3기 사업자로 선정된 하나은행이 군 장병들과 새해 일출을 함께 보는 행사를 열었다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=5, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=5,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title.startswith("하나은행, 군 장병과 새해 첫 출발")
    assert "한국경제" not in article.title
    assert "하나은행, 군 장병과 새해 첫 출발" in article.body_text
    assert len(article.images) == 1


def test_article_clusterer_drops_metadata_only_publication_stub(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "title", "bbox": [434, 558, 652, 646], "content": "한겨레"},
            {"label": "text", "bbox": [484, 646, 1242, 702], "content": "2026년 1월 2일 금요일 019면 사람"},
            {"label": "title", "bbox": [451, 723, 541, 783], "content": "알림"},
            {
                "label": "text",
                "bbox": [449, 825, 1218, 1404],
                "content": "◇ 하나은행은 새해를 맞아 군 장병들을 격려했다고 밝혔다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=2, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=2,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    assert articles[0].title == "알림"
    assert "2026년 1월 2일 금요일 019면 사람" not in articles[0].title
    assert "한겨레" not in articles[0].body_text


def test_article_clusterer_merges_wide_lead_title_and_ignores_related_article_marker(tmp_path) -> None:
    raw_vl = {
        "width": 3509,
        "height": 2480,
        "parsing_res_list": [
            {"label": "header", "bbox": [151, 392, 456, 474], "content": "문화일보"},
            {"label": "header", "bbox": [2442, 397, 3379, 454], "content": "2026년 2월 13일 금요일 008면 외교안보"},
            {"label": "title", "bbox": [298, 501, 3232, 692], "content": "또 날아간 ‘별’ ... ‘내란 폭풍’ 휩쓸린 軍"},
            {"label": "title", "bbox": [246, 905, 867, 977], "content": "강동길 해군총장 직무배제"},
            {
                "label": "text",
                "bbox": [246, 1104, 867, 1359],
                "content": "지작사령관 수사의뢰 이어\n李정부 임명 대장 또 징계\n지선 의식, 내란 단죄 기조",
            },
            {"label": "title", "bbox": [182, 1473, 474, 1528], "content": "▶관련기사 1면"},
            {
                "label": "text",
                "bbox": [182, 1545, 940, 2056],
                "content": "■ 국방부가 13일 ‘12·3 비상계엄’ 연루 의혹을 내세워 강동길 해군참모총장을 직무 배제한 것을 놓고 정치권에서는 내란 청산에 대한 정부의 강력한 의지를 드러낸 것이라는 분석이 나온다.",
            },
            {"label": "text", "bbox": [972, 873, 1512, 935], "content": "것은 극히 이례적인 일이다."},
            {
                "label": "text",
                "bbox": [972, 947, 1733, 1533],
                "content": "정빛나 국방부 대변인은 이날 오전 브리핑에서 강 총장에 대한 직무 배제와 관련해 향후 징계 절차를 진행하고 결과에 따라 인사 조치를 시행할 예정이라고 밝혔다.",
            },
            {
                "label": "text",
                "bbox": [972, 1545, 1733, 2056],
                "content": "국방부 관계자는 합참 군사지원본부장은 계엄과장 직속 라인이라며 계엄사령부를 구성할 때 합참 차장이 지원해 달라고 하니 담당과장에게 지원하라고 한 등의 협의가 있어 징계를 의뢰했다고 설명했다.",
            },
            {
                "label": "text",
                "bbox": [1765, 873, 2530, 1233],
                "content": "해군참모총장 임명 이후 뒤늦게 비상계엄 연루 의혹이 확인됐다는 의미로 풀이된다. 강 총장의 직무 배제로 당분간 해군 참모차장이 총장 직무대리를 수행한다.",
            },
            {
                "label": "text",
                "bbox": [1765, 1245, 2530, 2056],
                "content": "국방부는 전날에도 비상계엄 연루 협의가 제기된 주 사령관을 직무에서 배제하고 수사 의뢰했다. 주 사령관 역시 새 정부 출범 이후인 지난해 9월 대장으로 진급하며 지상작전사령관에 취임했다.",
            },
            {"label": "text", "bbox": [2555, 873, 3309, 935], "content": "로 110명에 대해선 수사도 의뢰했다."},
            {
                "label": "text",
                "bbox": [2555, 947, 3362, 1756],
                "content": "국방부와 TF의 연이은 고강도 징계 조치를 놓고 정치권에서는 내란 청산 드라이브를 이어가는 것이라는 해석이 나온다.",
            },
            {"label": "text", "bbox": [2555, 1768, 3362, 1982], "content": "이와 함께 여권은 3대 특별검사에 이은 2차 종합특검을 통해 내란 정국을 지방선거까지 끌고 간다는 복안이다."},
            {"label": "text", "bbox": [2758, 1994, 3362, 2053], "content": "정충신 선임기자·나윤석 기자"},
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=1, page_width=3509, page_height=2480, raw_vl=raw_vl)
    page = PageLayout(
        page_number=1,
        width=3509,
        height=2480,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "또 날아간 ‘별’ ... ‘내란 폭풍’ 휩쓸린 軍 강동길 해군총장 직무배제"
    assert "지작사령관 수사의뢰 이어" in article.body_text
    assert "국방부 관계자는 합참 군사지원본부장은 계엄과장 직속 라인" in article.body_text
    assert "▶관련기사 1면" not in article.title
    assert "▶관련기사 1면" not in article.body_text


def test_article_clusterer_assigns_header_metadata_and_keeps_image_with_real_article(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "title", "bbox": [434, 558, 652, 646], "content": "한겨레"},
            {"label": "title", "bbox": [1290, 558, 1587, 646], "content": "한국일보"},
            {"label": "text", "bbox": [484, 646, 1242, 702], "content": "2026년 1월 2일 금요일 019면 사람"},
            {"label": "text", "bbox": [1317, 646, 2046, 761], "content": "2026년 1월 2일 금요일 A19면 경제"},
            {"label": "title", "bbox": [451, 723, 541, 783], "content": "알림"},
            {"label": "image", "bbox": [1322, 814, 2014, 1221], "content": ""},
            {
                "label": "text",
                "bbox": [449, 825, 1218, 1404],
                "content": "◇ 하나은행은 새해를 맞아 경기도 파주 소재 육군 1사단에 위치한 도라전망대를 방문해 신년첫 해돋이를 맞이하고 군장병들을 격려했다고 1일 밝혔다.",
            },
            {"label": "title", "bbox": [1322, 1295, 2019, 1361], "content": "하나銀, 파주서 새해 軍 위문 행사"},
            {
                "label": "text",
                "bbox": [1317, 1435, 2026, 1895],
                "content": "하나은행은 1일 이호성 행정 등 본점 임직원 100여 명이 경기 파주시 육군 1사단 도라전망대를 방문해 군장병들을 격려했다고 밝혔다.",
            },
            {
                "label": "text",
                "bbox": [1317, 1902, 2026, 2358],
                "content": "이번 행사는 2026년부터 나라사랑카드 신규 사업자로 선정된 하나은행이 장병들에게 감사의 마음을 전하기 위해 마련됐다.",
            },
            {"label": "text", "bbox": [1803, 2835, 2026, 2891], "content": "전유진 기자"},
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=2, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=2,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 2
    assert all(article.title != "Untitled" for article in articles)

    left, right = articles
    assert left.title == "알림"
    assert left.metadata["source_metadata"]["publication"] == "한겨레"
    assert left.metadata["source_metadata"]["issue_date"] == "2026-01-02"
    assert left.metadata["source_metadata"]["issue_page_label"] == "019면"
    assert left.metadata["source_metadata"]["issue_section"] == "사람"

    assert right.title == "하나銀, 파주서 새해 軍 위문 행사"
    assert right.metadata["source_metadata"]["publication"] == "한국일보"
    assert right.metadata["source_metadata"]["issue_date"] == "2026-01-02"
    assert right.metadata["source_metadata"]["issue_page_label"] == "A19면"
    assert right.metadata["source_metadata"]["issue_section"] == "경제"
    assert len(right.images) == 1
    assert right.images[0].bbox == [1322, 814, 2014, 1221]


def test_article_clusterer_parses_html_wrapped_page_headers_as_source_metadata(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "header", "bbox": [252, 926, 548, 1007], "content": "<p>문화일보</p>"},
            {"label": "header", "bbox": [1329, 929, 2229, 989], "content": "<p>2026년 2월 13일 금요일 008면 외교안보</p>"},
            {"label": "title", "bbox": [339, 1045, 2117, 1157], "content": "<h2>정조대왕급 이지스함 3번함은 ‘대호김종서함’</h2>"},
            {"label": "title", "bbox": [292, 1319, 1200, 1403], "content": "<h3>HD현대중 건조… 내년말 인도</h3>"},
            {
                "label": "text",
                "bbox": [282, 1508, 1207, 2158],
                "content": "<p>■ 정조대왕급 이지스함(KDX-III 배치-II)의 마지막 3번함 함명은 북방영토를 개척한 ‘대호김종서함’으로 명명됐다.</p>",
            },
            {
                "label": "text",
                "bbox": [1262, 1319, 2204, 1779],
                "content": "<p>최신 이지스 구축함의 강력한 전투능력과 기동성, 자주국방의 의지 등을 고려했다고 해군은 설명했다.</p>",
            },
            {
                "label": "text",
                "bbox": [1262, 1796, 2204, 2445],
                "content": "<p>대호김종서함은 길이 170m, 폭 21m, 경하수 약 8200t으로 최신 이지스 전투체계를 탑재한다.</p>",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=4, page_width=2480, page_height=3509, raw_vl=raw_vl)

    assert blocks[0].text == "문화일보"
    assert blocks[1].text == "2026년 2월 13일 금요일 008면 외교안보"
    assert blocks[2].text == "정조대왕급 이지스함 3번함은 ‘대호김종서함’"

    page = PageLayout(
        page_number=4,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1

    article = articles[0]
    metadata = article.metadata["source_metadata"]

    assert "<" not in article.title
    assert "<" not in article.body_text
    assert metadata["publication"] == "문화일보"
    assert metadata["raw_publication_text"] == "문화일보"
    assert metadata["issue_date"] == "2026-02-13"
    assert metadata["issue_page_label"] == "008면"
    assert metadata["issue_section"] == "외교안보"
    assert metadata["raw_issue_text"] == "2026년 2월 13일 금요일 008면 외교안보"


def test_article_clusterer_drops_small_bodyless_logo_article(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "title", "bbox": [821, 586, 1304, 674], "content": "아시아투데이"},
            {"label": "text", "bbox": [880, 674, 1644, 733], "content": "2026년 1월 2일 금요일 012면 국제"},
            {"label": "title", "bbox": [875, 768, 1597, 842], "content": "태국-캄 '2차 휴전' 이행 첫발"},
            {"label": "title", "bbox": [875, 870, 1592, 944], "content": "800km 국경 갈등 불씨는 여전"},
            {"label": "image", "bbox": [868, 1028, 1163, 1172], "content": ""},
            {
                "label": "text",
                "bbox": [1188, 1021, 1612, 1207],
                "content": "[하노이=정리나 특파원] 태국 정부의 캄보디아 군인 석방은 지난 27일 맺어진 휴전 합의의 핵심 조건이었다.",
            },
            {
                "label": "text",
                "bbox": [861, 1214, 1612, 1793],
                "content": "당시 태국은 72시간 동안 정전이 유지될 경우에만 포로를 송환하겠다는 조건을 내걸었다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=41, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=41,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    assert articles[0].title == "태국-캄 '2차 휴전' 이행 첫발 800km 국경 갈등 불씨는 여전"


def test_article_clusterer_merges_embedded_summary_title_into_wide_lead_article(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "header", "bbox": [496, 137, 796, 214], "content": "머니투데이"},
            {"label": "header", "bbox": [1208, 137, 1969, 200], "content": "2026년 1월 2일 금요일 024면 사회"},
            {"label": "title", "bbox": [689, 235, 1753, 551], "content": "이번엔 제주 해군기지\n또 중국인이 드론촬영"},
            {
                "label": "text",
                "bbox": [533, 677, 1208, 1172],
                "content": "MTonly 중국 국적의 30대 남성이 제주 해군기지를 드론으로 무단촬영한 혐의로 경찰에 적발돼 검찰에 넘겨진 사실이 뒤늦게 확인됐다.",
            },
            {
                "label": "text",
                "bbox": [533, 1176, 1208, 2060],
                "content": "제주경찰청 안보수사과는 군사기지법 위반 혐의로 중국 국적 30대 남성 A씨를 지난해 10월 14일 불구속 송치했다고 1일 밝혔다.",
            },
            {"label": "image", "bbox": [1252, 681, 1912, 1467], "content": ""},
            {
                "label": "caption",
                "bbox": [1247, 1484, 1920, 1586],
                "content": "지난 2월 제주 서귀포시 강정동 해군기지에서 함선이 이동하고 있다.",
            },
            {"label": "text", "bbox": [1567, 1586, 1920, 1635], "content": "/서귀포(제주)=뉴스시스"},
            {
                "label": "title",
                "bbox": [1096, 1674, 1850, 1940],
                "content": "지난 8월 30대 남성 긴급체포·압수\n현재 안보인력, ‘北 대공수사’ 집중\n잇단 무단촬영 방첩수사 확대 필요",
            },
            {
                "label": "text",
                "bbox": [1247, 1997, 1920, 2372],
                "content": "국인의 정보수집 활동에 대응할 방첩수사 역량을 강화해야 한다는 지적이 나온다.",
            },
            {
                "label": "text",
                "bbox": [1247, 2379, 1920, 2944],
                "content": "특히 간첩수사 대상을 북한에 한정하지 않고 외국까지 확대하는 내용의 간첩법 개정안이 빠르면 올해 초 국회를 통과할 것이라는 전망이 나온다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=10, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=10,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1

    lead = articles[0]

    assert lead.title == "이번엔 제주 해군기지\n또 중국인이 드론촬영"
    assert "MTonly 중국 국적의 30대 남성이 제주 해군기지를 드론으로 무단촬영" in lead.body_text
    assert "제주경찰청 안보수사과는 군사기지법 위반 혐의" in lead.body_text
    assert "지난 8월 30대 남성 긴급체포·압수" in lead.body_text
    assert "잇단 무단촬영 방첩수사 확대 필요" in lead.body_text
    assert "국인의 정보수집 활동에 대응할 방첩수사 역량을 강화" in lead.body_text
    assert len(lead.images) == 1
    assert lead.images[0].bbox == [1252, 681, 1912, 1467]
    assert [caption.text for caption in lead.images[0].captions] == ["지난 2월 제주 서귀포시 강정동 해군기지에서 함선이 이동하고 있다."]


def test_article_clusterer_keeps_inline_intertitles_inside_single_column_article(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "header", "bbox": [55, 1021, 362, 1109], "content": "내일신문"},
            {"label": "header", "bbox": [1530, 1028, 2435, 1088], "content": "2026년 2월 24일 화요일 022면 오피니언"},
            {"label": "text", "bbox": [77, 1147, 193, 1200], "content": "한반도"},
            {"label": "title", "bbox": [660, 1165, 1942, 1253], "content": "강한 안보, 긴장 낮추고 위기관리 능력 키운다"},
            {"label": "image", "bbox": [82, 1326, 627, 1583], "content": ""},
            {
                "label": "text",
                "bbox": [660, 1354, 1220, 1488],
                "content": "장해제란 한쪽은 무장을 유지한 채 다른 한쪽만 무장을 해제하는 구한말의 군대해산 같은 상황이다.",
            },
            {
                "label": "text",
                "bbox": [1247, 1400, 1808, 1723],
                "content": "훈련 역시 마찬가지다. 훈련의 목적이 무력시위가 아니라 억제력 유지와 대비 태세 점검이라면 불필요한 긴장을 유발하지 않는 범위에서 실시하는 것이 군사적 상식에 부합한다.",
            },
            {
                "label": "text",
                "bbox": [1828, 1354, 2416, 1677],
                "content": "집트, 요르단과 이스라엘은 오랜 갈등의 누적속에서도 평화협정을 유지해왔다. 이러한 경험은 군사적 대치상황에서도 관리 가능한 평화체제가 가능함을 보여준다.",
            },
            {
                "label": "caption",
                "bbox": [303, 1446, 531, 1565],
                "content": "이병록 덕파통일안보연구소장 정치학박사",
            },
            {
                "label": "text",
                "bbox": [77, 1681, 635, 1997],
                "content": "이재명 대통령은 2025년 8.15 경축사와 2026년 신년기자회견 등을 통해 여러차례 '9.19 군사합의'의 조기 복원을 제시했다.",
            },
            {"label": "title", "bbox": [1828, 1723, 2284, 1765], "content": "9.19 군사합의는 성숙한 안보의 길"},
            {
                "label": "text",
                "bbox": [1828, 1769, 2416, 2133],
                "content": "변화는 한번의 결단으로 완성되지 않는다. 작은 조정과 제도적 관리가 쌓이면서 구조적 안정으로 이어진다.",
            },
            {"label": "title", "bbox": [660, 1818, 1123, 1860], "content": "군사합의, 안보의 안정성 높이는 방식"},
            {
                "label": "text",
                "bbox": [660, 1860, 1220, 2133],
                "content": "그럼에도 군사합의 당시 합작 정문 앞에서 예비역 장성이 감시초소 철수에 반대하는 장면이 있었다.",
            },
            {
                "label": "text",
                "bbox": [1247, 1997, 1808, 2365],
                "content": "냉전 시기, 유럽에서도 군사적 대결 속에서 신뢰를 축적하려는 노력이 이어졌다.",
            },
            {
                "label": "text",
                "bbox": [1828, 2133, 2416, 2456],
                "content": "강한 안보란 강경한 수사나 보복의지 과시가 아니다. 위기를 조기에 통제하고 우발적 충돌을 예방하는 능력이다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=7, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=7,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == "강한 안보, 긴장 낮추고 위기관리 능력 키운다"
    assert "9.19 군사합의는 성숙한 안보의 길" in article.body_text
    assert "군사합의, 안보의 안정성 높이는 방식" in article.body_text
    assert "변화는 한번의 결단으로 완성되지 않는다." in article.body_text
    assert len(article.images) == 1


def test_article_clusterer_merges_deck_and_section_headers_into_feature_article(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "header", "bbox": [565, 98, 937, 193], "content": "세계일보"},
            {"label": "header", "bbox": [1138, 109, 1902, 168], "content": "2026년 1월 2일 금요일 020면 종합"},
            {
                "label": "title",
                "bbox": [583, 204, 1820, 428],
                "content": "獨 안보 파트너십 vs 韓 검증된 기술\n加 ‘60조원 잠수함 수주전쟁’ 승자는",
            },
            {"label": "image", "bbox": [593, 526, 895, 674], "content": ""},
            {"label": "image", "bbox": [1247, 530, 1850, 958], "content": ""},
            {"label": "caption", "bbox": [709, 607, 895, 660], "content": "박수찬의 軍"},
            {"label": "title", "bbox": [625, 691, 1208, 747], "content": "캐나다 수출 길 뚫나… K방산 핫이슈"},
            {
                "label": "text",
                "bbox": [585, 835, 1208, 937],
                "content": "“올해 K방산의 핵심 이슈는 캐나다 잠수함 수출 여부다.”",
            },
            {
                "label": "text",
                "bbox": [585, 940, 1208, 1351],
                "content": "최근 기자와 만난 방산업계 관계자는 2026년 K방산에서 가장 중요한 것으로 캐나다 해상 순찰 잠수함 사업을 꼽았다.",
            },
            {
                "label": "caption",
                "bbox": [1245, 968, 1867, 1063],
                "content": "한국 해군 도산안창호급 잠수함 안무함이 성능시험을 위해 수면 위로 부상한 채 항해하고 있다.",
            },
            {"label": "title", "bbox": [1245, 1140, 1850, 1414], "content": "獨, 방산협력·제트기 구매 등 당근책\nEU무기조달 ‘세이프’ 加 동참도 견인\n韓, 3000t급 운용 능력 신뢰도 강점\n정부 차원 패키지 인센티브 지원을"},
            {
                "label": "text",
                "bbox": [585, 1354, 1208, 1660],
                "content": "폴란드 잠수함 사업에서 스웨덴에 밀렸던 한국이 수주한다면 선진국 시장에 진출할 기회를 다시 얻게 된다.",
            },
            {
                "label": "text",
                "bbox": [1245, 1509, 1867, 1607],
                "content": "연계도 한층 강화하는 계기가 될 것이라는 점을 캐나다 정부에 강조할 수 있다.",
            },
            {"label": "title", "bbox": [1277, 1614, 1644, 1660], "content": "◆한국, 포괄적 전략 필요"},
            {"label": "title", "bbox": [620, 1667, 1138, 1712], "content": "◆독일, 정부 차원 패키지 전략 접근"},
            {
                "label": "text",
                "bbox": [1245, 1667, 1867, 2074],
                "content": "다만 세이프가 수주 여부에 결정적인 요소는 아니라는 지적도 나온다.",
            },
            {
                "label": "text",
                "bbox": [585, 1716, 1208, 1969],
                "content": "독일 측은 일반적인 무기 판매가 아닌 국가 간 전략적 협력 차원으로 접근하고 있다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=15, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=15,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 1
    article = articles[0]
    assert article.title == (
        "獨 안보 파트너십 vs 韓 검증된 기술\n"
        "加 ‘60조원 잠수함 수주전쟁’ 승자는 캐나다 수출 길 뚫나… K방산 핫이슈"
    )
    assert "◆한국, 포괄적 전략 필요" in article.body_text
    assert "◆독일, 정부 차원 패키지 전략 접근" in article.body_text
    assert "다만 세이프가 수주 여부에 결정적인 요소는 아니라는 지적도 나온다." in article.body_text
    assert len(article.images) == 2


def test_article_clusterer_merges_stacked_section_headers_before_body(tmp_path) -> None:
    raw_vl = {
        "width": 2480,
        "height": 3509,
        "parsing_res_list": [
            {"label": "header", "bbox": [60, 628, 280, 709], "content": "한겨레"},
            {"label": "header", "bbox": [1649, 635, 2438, 691], "content": "2026년 1월 2일 금요일 001면 종합"},
            {
                "label": "title",
                "bbox": [79, 740, 2041, 860],
                "content": "“한·중 정상회담때 한반도 비핵화 실질적 논의”",
            },
            {
                "label": "title",
                "bbox": [107, 993, 826, 1056],
                "content": "이 대통령 4일 방중...위성락 실장 인터뷰",
            },
            {
                "label": "title",
                "bbox": [82, 1105, 776, 1253],
                "content": "“비핵화 안되면 중국도 이득 안돼\n핵 잠수함은 북한 위협 대비 차원”",
            },
            {
                "label": "text",
                "bbox": [102, 1333, 833, 1860],
                "content": "위성락 국가안보실장이 한중 정상회담에서 내실 있고 실질적인 논의가 있을 것이라고 말했다.",
            },
            {
                "label": "text",
                "bbox": [875, 1390, 1610, 1860],
                "content": "오는 4~7일 이 대통령의 방중 기간 중국과 한반도 비핵화에 대한 논의를 본격적으로 진행하겠다는 뜻이다.",
            },
            {"label": "image", "bbox": [878, 986, 1173, 1347], "content": ""},
            {
                "label": "title",
                "bbox": [546, 2049, 2234, 2126],
                "content": "“남북 핵비대칭, 사활적 문제...한·중 정상회담서 핵잠 설명할 것”",
            },
            {
                "label": "title",
                "bbox": [635, 2193, 898, 2235],
                "content": "위성락 안보실장 인터뷰",
            },
            {
                "label": "text",
                "bbox": [546, 2288, 990, 2509],
                "content": "위 실장은 이번 방중에 맞춰 경제와 관련한 여러 협력 문서를 마련하고 있다고 말했다.",
            },
            {
                "label": "text",
                "bbox": [1478, 2221, 1927, 2579],
                "content": "동맹파는 남북관계를 소홀히 다룬다고 보는 경우가 있는데 절대 소홀히 하지 않는다고 강조했다.",
            },
        ],
    }

    engine = OCREngine()
    blocks = engine._merge_blocks(page_number=22, page_width=2480, page_height=3509, raw_vl=raw_vl)
    page = PageLayout(
        page_number=22,
        width=2480,
        height=3509,
        image_path=tmp_path / "page.png",
        blocks=blocks,
        raw_vl=raw_vl,
        raw_structure={},
        raw_fallback_ocr={},
    )

    articles, unassigned = ArticleClusterer().cluster_page(page)

    assert not unassigned
    assert len(articles) == 2

    lead = articles[0]
    follow = articles[1]

    assert lead.title == (
        "“한·중 정상회담때 한반도 비핵화 실질적 논의” "
        "이 대통령 4일 방중...위성락 실장 인터뷰 "
        "“비핵화 안되면 중국도 이득 안돼\n핵 잠수함은 북한 위협 대비 차원”"
    )
    assert "위성락 국가안보실장이 한중 정상회담에서 내실 있고 실질적인 논의가 있을 것이라고 말했다." in lead.body_text
    assert "오는 4~7일 이 대통령의 방중 기간 중국과 한반도 비핵화에 대한 논의를 본격적으로 진행하겠다는 뜻이다." in lead.body_text
    assert len(lead.images) == 1
    assert lead.images[0].bbox == [878, 986, 1173, 1347]

    assert follow.title == "“남북 핵비대칭, 사활적 문제...한·중 정상회담서 핵잠 설명할 것” 위성락 안보실장 인터뷰"
    assert "경제와 관련한 여러 협력 문서를 마련" in follow.body_text
    assert "절대 소홀히 하지 않는다고 강조했다." in follow.body_text
