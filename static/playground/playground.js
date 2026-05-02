const state = {
  file: null,
  files: [],
  fileEntries: [],
  fileUrl: "",
  result: null,
  batchResults: [],
  pageIndex: 0,
  activeResult: "blocks",
  processTimer: null,
  currentRequestId: "",
  currentFileName: "",
  hasVisibleResult: false,
  autoFollowLatestPage: true,
  thumbsCollapsed: false,
  linkedBlockHoverKey: "",
  linkedBlockPinnedKey: "",
  historyLoaded: false,
  historyItems: [],
  editTarget: null,
  editImageData: null,
  removeManualImage: false,
};

const linkedBlockKeys = new WeakMap();

const EDIT_LABELS = [
  "title",
  "text",
  "table",
  "image",
  "caption",
  "header",
  "footer",
  "footnote",
  "list_group",
  "code_block",
  "equation_block",
  "form",
  "table_of_contents",
  "bibliography",
  "complex_block",
  "handwriting",
  "reference",
];
const IMAGE_LABELS = new Set(["image", "figure", "photo", "picture", "illustration", "chart", "graphic", "diagram"]);

const els = {
  configForm: document.getElementById("configPane"),
  resultsPane: document.getElementById("resultsPane"),
  runtimeSettingsPane: document.getElementById("runtimeSettingsPane"),
  historyPane: document.getElementById("historyPane"),
  runtimeSettingsForm: document.getElementById("runtimeSettingsForm"),
  runtimeSettingsFields: document.getElementById("runtimeSettingsFields"),
  reloadRuntimeSettings: document.getElementById("reloadRuntimeSettings"),
  reloadHistory: document.getElementById("reloadHistory"),
  historyList: document.getElementById("historyList"),
  historySearch: document.getElementById("historySearch"),
  historyStatusText: document.getElementById("historyStatusText"),
  settingsStatusText: document.getElementById("settingsStatusText"),
  uploadStage: document.getElementById("uploadStage"),
  processingStage: document.getElementById("processingStage"),
  previewStage: document.getElementById("previewStage"),
  documentPane: document.querySelector(".document-pane"),
  documentToolbar: document.getElementById("documentToolbar"),
  progressPanel: document.getElementById("progressPanel"),
  progressTitle: document.getElementById("progressTitle"),
  progressLabel: document.getElementById("progressLabel"),
  progressBar: document.getElementById("progressBar"),
  progressTrack: document.querySelector(".progress-track"),
  progressDetail: document.getElementById("progressDetail"),
  dropZone: document.getElementById("dropZone"),
  fileInput: document.getElementById("fileInput"),
  fileNameLabel: document.getElementById("fileNameLabel"),
  fileHelpLabel: document.getElementById("fileHelpLabel"),
  clearFileButton: document.getElementById("clearFileButton"),
  fileList: document.getElementById("fileList"),
  fileUrlInput: document.getElementById("fileUrlInput"),
  useUrlButton: document.getElementById("useUrlButton"),
  convertButton: document.getElementById("convertButton"),
  statusText: document.getElementById("statusText"),
  prevPage: document.getElementById("prevPage"),
  nextPage: document.getElementById("nextPage"),
  pageLabel: document.getElementById("pageLabel"),
  toggleThumbs: document.getElementById("toggleThumbs"),
  thumbStrip: document.getElementById("thumbStrip"),
  pageImage: document.getElementById("pageImage"),
  overlayLayer: document.getElementById("overlayLayer"),
  blocksView: document.getElementById("blocksView"),
  jsonView: document.getElementById("jsonView"),
  htmlView: document.getElementById("htmlView"),
  markdownView: document.getElementById("markdownView"),
  renderHtml: document.getElementById("renderHtml"),
  copyButton: document.getElementById("copyButton"),
  downloadButton: document.getElementById("downloadButton"),
  updateSettingsButton: document.getElementById("updateSettingsButton"),
  ocrRuntimeState: document.getElementById("ocrRuntimeState"),
  ocrConcurrencyState: document.getElementById("ocrConcurrencyState"),
  blockEditModal: document.getElementById("blockEditModal"),
  blockEditForm: document.getElementById("blockEditForm"),
  closeBlockEdit: document.getElementById("closeBlockEdit"),
  cancelBlockEdit: document.getElementById("cancelBlockEdit"),
  saveBlockEdit: document.getElementById("saveBlockEdit"),
  editBlockEyebrow: document.getElementById("editBlockEyebrow"),
  editBlockTitle: document.getElementById("editBlockTitle"),
  editBlockLabel: document.getElementById("editBlockLabel"),
  editBlockText: document.getElementById("editBlockText"),
  editTableField: document.getElementById("editTableField"),
  editTableText: document.getElementById("editTableText"),
  editImageField: document.getElementById("editImageField"),
  editImageDrop: document.getElementById("editImageDrop"),
  editImageInput: document.getElementById("editImageInput"),
  editImagePreview: document.getElementById("editImagePreview"),
  clearEditImage: document.getElementById("clearEditImage"),
  editBlockStatus: document.getElementById("editBlockStatus"),
};

function setStatus(text, isError = false) {
  els.statusText.textContent = text;
  els.statusText.classList.toggle("error", isError);
}

function setResourceState(name, text, tone = "neutral") {
  const node = document.querySelector(`[data-resource-state="${name}"]`);
  if (!node) {
    return;
  }
  node.textContent = text;
  node.dataset.tone = tone;
}

async function loadResources() {
  try {
    const response = await fetch("api/resources", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("resource check failed");
    }
    const payload = await response.json();
    Object.entries(payload.links || {}).forEach(([name, item]) => {
      const link = document.querySelector(`[data-resource-link="${name}"]`);
      if (link && item.url) {
        link.href = item.url;
      }
    });
    const ready = Boolean(payload.health && payload.health.ocr_service_ready);
    setResourceState("docs", "열기", "neutral");
    setResourceState("api_guide", "열기", "neutral");
    setResourceState("api_reference", "열기", "neutral");
    setResourceState("openapi", "열기", "neutral");
    setResourceState("api_capabilities", "정상", "ok");
    setResourceState("ocr_health", ready ? "정상" : "시작 중", ready ? "ok" : "warn");
    setResourceState("admin", "로그인", "neutral");
    applyRuntimeDefaults(payload.capabilities?.features || {});
    if (els.ocrRuntimeState) {
      els.ocrRuntimeState.textContent = ready ? "준비됨" : "시작 중";
      els.ocrRuntimeState.dataset.tone = ready ? "ok" : "warn";
    }
    if (els.ocrConcurrencyState) {
      const limit = payload.health?.max_concurrent_ocr_requests ?? payload.capabilities?.features?.max_concurrent_ocr_requests ?? "-";
      els.ocrConcurrencyState.textContent = String(limit);
    }
  } catch (error) {
    setResourceState("api_capabilities", "확인 실패", "error");
    setResourceState("ocr_health", "확인 실패", "error");
    if (els.ocrRuntimeState) {
      els.ocrRuntimeState.textContent = "확인 실패";
      els.ocrRuntimeState.dataset.tone = "error";
    }
  }
}

function showPane(id) {
  document.querySelectorAll(".pane-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.pane === id);
  });
  els.configForm.hidden = id !== "configPane";
  els.resultsPane.hidden = id !== "resultsPane";
  if (els.historyPane) {
    els.historyPane.hidden = id !== "historyPane";
    if (id === "historyPane" && !state.historyLoaded) {
      loadHistory();
    }
  }
  if (els.runtimeSettingsPane) {
    els.runtimeSettingsPane.hidden = id !== "runtimeSettingsPane";
    if (id === "runtimeSettingsPane" && !els.runtimeSettingsFields.dataset.loaded) {
      loadRuntimeSettings();
    }
  }
}

function setActiveResult(name) {
  state.activeResult = name;
  document.querySelectorAll(".result-tab").forEach((button) => {
    button.classList.toggle("active", button.dataset.result === name);
  });
  els.blocksView.hidden = name !== "blocks";
  els.jsonView.hidden = name !== "json";
  els.htmlView.hidden = name !== "html";
  els.markdownView.hidden = name !== "markdown";
  if (name === "html") {
    renderHtmlView();
  }
}

function selectedMode() {
  const checked = els.configForm.querySelector("input[name='mode']:checked");
  return checked ? checked.value : "balanced";
}

function syncModeCards() {
  document.querySelectorAll(".mode-card").forEach((card) => {
    const input = card.querySelector("input[type='radio']");
    card.classList.toggle("selected", input.checked);
  });
}

function setFile(file) {
  setFiles(file ? [file] : []);
}

function setFiles(files) {
  const selected = Array.from(files || []).filter(Boolean);
  state.files = selected;
  state.file = selected[0] || null;
  state.fileEntries = selected.map((file) => ({
    file,
    status: "ready",
    detail: "대기 중",
    requestId: "",
  }));
  state.fileUrl = "";
  els.fileUrlInput.value = "";
  const count = selected.length;
  const totalSize = selected.reduce((sum, item) => sum + Number(item.size || 0), 0);
  const label = count > 1 ? `${count}개 파일 선택됨` : selected[0]?.name || "";
  els.dropZone.querySelector("strong").textContent = label || "파일을 놓거나 클릭";
  if (els.fileNameLabel) {
    els.fileNameLabel.textContent = label || "선택된 파일 없음";
  }
  if (els.fileHelpLabel) {
    els.fileHelpLabel.textContent = count
      ? `총 ${readableFileSize(totalSize)} · 파일별 상태를 아래에서 확인하세요.`
      : "파일을 선택하면 여기 표시됩니다.";
  }
  els.dropZone.classList.toggle("has-file", count > 0);
  renderFileList();
  setStatus(count ? `${count}개 파일이 선택됐습니다.` : "");
}

function clearInput() {
  state.file = null;
  state.files = [];
  state.fileEntries = [];
  state.fileUrl = "";
  els.fileInput.value = "";
  els.fileUrlInput.value = "";
  els.dropZone.querySelector("strong").textContent = "파일을 놓거나 클릭";
  if (els.fileNameLabel) {
    els.fileNameLabel.textContent = "선택된 파일 없음";
  }
  if (els.fileHelpLabel) {
    els.fileHelpLabel.textContent = "파일을 선택하면 여기 표시됩니다.";
  }
  els.dropZone.classList.remove("has-file");
  renderFileList();
  setStatus("");
}

function renderFileList() {
  if (!els.fileList) {
    return;
  }
  els.fileList.replaceChildren();
  if (!state.fileEntries.length) {
    els.fileList.hidden = true;
    return;
  }
  els.fileList.hidden = false;
  state.fileEntries.forEach((entry, index) => {
    const item = document.createElement("div");
    item.className = "file-list-item";
    item.dataset.status = entry.status;

    const badge = document.createElement("span");
    badge.className = "file-status-dot";
    badge.setAttribute("aria-hidden", "true");
    item.appendChild(badge);

    const text = document.createElement("div");
    text.className = "file-list-text";
    const name = document.createElement("strong");
    name.textContent = entry.file.name;
    const detail = document.createElement("span");
    detail.textContent = `${readableFileSize(entry.file.size)} · ${entry.detail}`;
    text.append(name, detail);
    item.appendChild(text);

    const status = document.createElement("em");
    status.textContent = fileStatusText(entry.status, index);
    item.appendChild(status);
    els.fileList.appendChild(item);
  });
}

function updateFileEntry(index, changes) {
  if (index == null || !state.fileEntries[index]) {
    return;
  }
  state.fileEntries[index] = {
    ...state.fileEntries[index],
    ...changes,
  };
  renderFileList();
}

function fileStatusText(status, index) {
  if (status === "processing") {
    return "읽는 중";
  }
  if (status === "complete") {
    return "완료";
  }
  if (status === "failed") {
    return "실패";
  }
  return `${index + 1}`;
}

function readableFileSize(size) {
  if (!Number.isFinite(size) || size <= 0) {
    return "크기 정보 없음";
  }
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  const digits = value >= 10 || index === 0 ? 0 : 1;
  return `${value.toFixed(digits)} ${units[index]}`;
}

function buildFormData(fileOverride = null) {
  const data = new FormData(els.configForm);
  data.set("mode", selectedMode());
  if (fileOverride) {
    data.set("file", fileOverride, fileOverride.name);
  } else if (state.files.length > 0) {
    data.set("file", state.files[0], state.files[0].name);
  } else if (state.fileUrl || els.fileUrlInput.value.trim()) {
    data.set("file_url", state.fileUrl || els.fileUrlInput.value.trim());
  }
  return data;
}

function applyRuntimeDefaults(features) {
  const maxPages = Number(features.default_max_pages || 0);
  const input = els.configForm.querySelector("input[name='max_pages']");
  if (input && maxPages > 0 && (!input.value || input.value === "10")) {
    input.value = String(maxPages);
  }
}

async function convertDocument(event) {
  if (event) {
    event.preventDefault();
  }
  if (!state.files.length && !state.fileUrl && !els.fileUrlInput.value.trim()) {
    setStatus("파일을 선택하거나 URL을 입력하세요.", true);
    return;
  }

  setStatus("문서를 읽는 중입니다...");
  state.result = null;
  state.pageIndex = 0;
  state.currentRequestId = "";
  state.currentFileName = "";
  state.batchResults = [];
  state.hasVisibleResult = false;
  state.autoFollowLatestPage = true;
  els.previewStage.hidden = true;
  els.documentToolbar.hidden = true;
  els.thumbStrip.hidden = true;
  if (state.fileEntries.length) {
    state.fileEntries = state.fileEntries.map((entry) => ({
      ...entry,
      status: "ready",
      detail: "대기 중",
      requestId: "",
    }));
    renderFileList();
  }
  resetProgress();
  startProcessingFeedback();
  els.convertButton.disabled = true;
  els.updateSettingsButton.disabled = true;
  try {
    const payload = state.files.length
      ? await convertSelectedFiles()
      : await convertSingleInput({ file: null, fileIndex: null, totalFiles: 1 });
    finishProcessingFeedback(true);
    updateProgress(payload, payload.request_id || state.currentRequestId);
    setStatus(`완료: ${payload.page_count || 0}쪽, 점수 ${payload.parse_quality_score ?? "n/a"}`);
    showPane("resultsPane");
    refreshHistoryAfterConversion();
  } catch (error) {
    finishProcessingFeedback(false);
    if (!state.result) {
      els.uploadStage.hidden = false;
    }
    setStatus(error.message || "문서 읽기에 실패했습니다.", true);
    if (state.currentRequestId) {
      refreshHistoryAfterConversion();
    }
  } finally {
    els.convertButton.disabled = false;
    els.updateSettingsButton.disabled = false;
  }
}

async function convertSelectedFiles() {
  const totalFiles = state.files.length;
  const results = [];
  for (let index = 0; index < totalFiles; index += 1) {
    const file = state.files[index];
    state.currentFileName = file.name;
    updateFileEntry(index, { status: "processing", detail: "업로드 준비 중" });
    setStatus(`${index + 1}/${totalFiles} ${file.name} 읽는 중...`);
    try {
      const payload = await convertSingleInput({ file, fileIndex: index, totalFiles });
      results.push(payload);
      updateFileEntry(index, {
        status: "complete",
        detail: `${payload.page_count || 0}쪽 완료`,
        requestId: payload.request_id || "",
      });
    } catch (error) {
      updateFileEntry(index, {
        status: "failed",
        detail: error.message || "읽기 실패",
      });
      throw error;
    }
  }
  state.batchResults = results;
  const merged = mergeBatchResults(results);
  applyConversionPayload(merged, { final: true });
  return merged;
}

async function convertSingleInput({ file, fileIndex = null, totalFiles = 1 }) {
  const response = await fetch("api/convert/start", {
    method: "POST",
    body: buildFormData(file),
  });
  const submitted = await response.json();
  if (!response.ok || !submitted.success || !submitted.request_id) {
    throw new Error(submitted.detail || submitted.error || "OCR conversion failed");
  }
  state.currentRequestId = submitted.request_id;
  if (fileIndex != null) {
    updateFileEntry(fileIndex, {
      status: "processing",
      detail: `요청 ${submitted.request_id.slice(0, 8)} 등록됨`,
      requestId: submitted.request_id,
    });
  }
  setStatus(`요청 등록됨: ${submitted.request_id.slice(0, 8)} 처리 상태를 확인합니다...`);
  const payload = submitted.status === "complete" && submitted.views
    ? submitted
    : await pollConversion(submitted.request_id, submitted.result_url, { fileIndex, totalFiles });
  applyConversionPayload(payload, { final: true });
  return payload;
}

async function pollConversion(requestId, resultUrl, context = {}) {
  const url = resultUrl || `api/convert/${encodeURIComponent(requestId)}`;
  const startedAt = Date.now();
  for (let attempt = 0; attempt < 1200; attempt += 1) {
    await delay(attempt === 0 ? 500 : 1500);
    const response = await fetch(url, { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "OCR result check failed");
    }
    if (context.fileIndex != null) {
      const progress = payload.progress || {};
      const processed = progress.processed_pages ?? payload.processed_page_count ?? (payload.pages || []).length;
      const total = progress.total_pages ?? payload.page_count ?? 0;
      updateFileEntry(context.fileIndex, {
        status: "processing",
        detail: total ? `${processed}/${total}쪽 완료` : `${processed}쪽 완료`,
      });
    }
    updateProgress(payload, requestId, Date.now() - startedAt);
    if (Array.isArray(payload.pages) && payload.pages.length > 0) {
      applyConversionPayload(payload, { final: payload.status === "complete" });
    }
    if (payload.status === "complete") {
      if (!payload.success) {
        throw new Error(payload.error || "OCR conversion failed");
      }
      if (context.fileIndex != null) {
        updateFileEntry(context.fileIndex, {
          status: "complete",
          detail: `${payload.page_count || payload.pages?.length || 0}쪽 완료`,
        });
      }
      setStatus(`완료: ${payload.page_count || payload.pages?.length || 0}쪽, 점수 ${payload.parse_quality_score ?? "n/a"}`);
      return payload;
    }
    if (payload.status === "failed" || payload.success === false) {
      throw new Error(payload.error || "OCR conversion failed");
    }
    const progress = payload.progress || {};
    const processed = progress.processed_pages ?? payload.processed_page_count ?? (payload.pages || []).length ?? 0;
    const total = progress.total_pages ?? payload.page_count ?? 0;
    const progressText = total ? `${processed}/${total}쪽 표시 중` : `${processed}쪽 표시 중`;
    const seconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    const filePrefix = state.currentFileName ? `${state.currentFileName} · ` : "";
    setStatus(`${filePrefix}${progressText} · 요청 ${requestId.slice(0, 8)} / 약 ${seconds}초 경과`);
  }
  throw new Error("처리 시간이 너무 깁니다. 잠시 뒤 결과 조회를 다시 시도하세요.");
}

function mergeBatchResults(results) {
  if (results.length === 1) {
    return results[0];
  }
  const pages = [];
  const assets = [];
  const downloads = [];
  const markdown = [];
  const html = [];
  const blocks = [];
  results.forEach((result, fileIndex) => {
    const fileName = result.metadata?.source_file || state.fileEntries[fileIndex]?.file?.name || `파일 ${fileIndex + 1}`;
    if (result.download_url) {
      downloads.push({ file_name: fileName, url: result.download_url });
    }
    markdown.push(`# ${fileName}\n\n${result.views?.markdown || ""}`.trim());
    html.push(`<section data-file="${escapeHtml(fileName)}"><h1>${escapeHtml(fileName)}</h1>${result.views?.html || ""}</section>`);
    blocks.push(`File ${fileIndex + 1}: ${fileName}\n${result.views?.blocks || ""}`.trim());
    (result.pages || []).forEach((page) => {
      pages.push({
        ...page,
        page_index: pages.length,
        source_file: fileName,
        source_file_index: fileIndex,
      });
    });
    (result.assets || []).forEach((asset) => {
      assets.push({
        ...asset,
        source_file: fileName,
        source_file_index: fileIndex,
      });
    });
  });
  const scores = results
    .map((result) => Number(result.parse_quality_score))
    .filter((value) => Number.isFinite(value));
  const averageScore = scores.length
    ? Math.round((scores.reduce((sum, value) => sum + value, 0) / scores.length) * 10000) / 10000
    : null;
  const compactJson = {
    status: "complete",
    file_count: results.length,
    page_count: pages.length,
    files: results.map((result, index) => ({
      file_name: result.metadata?.source_file || state.fileEntries[index]?.file?.name || `파일 ${index + 1}`,
      request_id: result.request_id,
      page_count: result.page_count,
      parse_quality_score: result.parse_quality_score,
      download_url: result.download_url,
    })),
    pages,
    assets,
  };
  return {
    success: results.every((result) => result.success !== false),
    status: "complete",
    request_id: "batch",
    page_count: pages.length,
    processed_page_count: pages.length,
    progress: {
      status: "complete",
      processed_pages: pages.length,
      total_pages: pages.length,
      percent: 100,
    },
    parse_quality_score: averageScore,
    metadata: {
      source_file: `${results.length}개 파일`,
      batch: true,
      file_count: results.length,
    },
    pages,
    assets,
    views: {
      blocks: blocks.filter(Boolean).join("\n\n"),
      json: JSON.stringify(compactJson, null, 2),
      html: `<!doctype html><html><head><meta charset="utf-8"><title>Army-OCR Batch Result</title></head><body>${html.join("")}</body></html>`,
      markdown: markdown.filter(Boolean).join("\n\n---\n\n"),
    },
    download_url: null,
    batch_downloads: downloads,
    error: null,
  };
}

function delay(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function normalizeLinkedBlockKeys(payload) {
  if (!payload || !Array.isArray(payload.pages)) {
    return payload;
  }
  const fallbackSource = payload.metadata?.source_file || "";
  payload.pages.forEach((page, pageIndex) => {
    if (!page || typeof page !== "object") {
      return;
    }
    const blocks = Array.isArray(page.blocks) ? page.blocks : [];
    blocks.forEach((block, blockIndex) => {
      if (!block || typeof block !== "object") {
        return;
      }
      linkedBlockKeys.set(
        block,
        rawBlockDomKey(page, block, blockIndex, {
          fallbackPageIndex: pageIndex,
          fallbackSource,
        }),
      );
    });
  });
  return payload;
}

function applyConversionPayload(payload, { final = false } = {}) {
  const previousPage = currentPage();
  const previousPageNumber = previousPage ? previousPage.page_number : null;
  const previousRequestId = state.result?.request_id || "";
  const normalizedPayload = normalizeLinkedBlockKeys(payload);
  const nextRequestId = normalizedPayload?.request_id || "";
  if (previousRequestId && nextRequestId && previousRequestId !== nextRequestId) {
    clearLinkedBlockSelection();
    state.autoFollowLatestPage = true;
  }
  state.result = normalizedPayload;
  const pages = Array.isArray(normalizedPayload?.pages) ? normalizedPayload.pages : [];
  if (pages.length > 0) {
    if (state.autoFollowLatestPage) {
      state.pageIndex = pages.length - 1;
    } else {
      const samePageIndex = previousPageNumber == null
        ? -1
        : pages.findIndex((page) => page.page_number === previousPageNumber);
      state.pageIndex = samePageIndex >= 0
        ? samePageIndex
        : Math.min(state.pageIndex, pages.length - 1);
    }
    renderResult({ final: final || normalizedPayload?.status === "complete" });
    if (!state.hasVisibleResult) {
      state.hasVisibleResult = true;
      showPane("resultsPane");
    }
  }
}

function renderResult({ final = false } = {}) {
  if (!state.result) {
    return;
  }
  const pages = Array.isArray(state.result.pages) ? state.result.pages : [];
  els.uploadStage.hidden = true;
  els.processingStage.hidden = pages.length > 0 || final;
  els.previewStage.hidden = pages.length === 0;
  els.documentToolbar.hidden = pages.length === 0;
  els.thumbStrip.hidden = pages.length === 0;
  if (els.toggleThumbs) {
    els.toggleThumbs.hidden = true;
  }
  syncThumbStripState();
  if (pages.length === 0) {
    return;
  }
  const hasBatchDownloads = Array.isArray(state.result.batch_downloads) && state.result.batch_downloads.length > 0;
  els.downloadButton.disabled = !state.result.download_url && !hasBatchDownloads;
  els.downloadButton.textContent = hasBatchDownloads ? "파일별 ZIP" : "이미지 포함 ZIP";
  renderThumbs();
  renderSelectedPage();
  setActiveResult(state.activeResult);
}

function renderSelectedPage() {
  renderPage();
  renderPageScopedResults();
  syncLinkedBlockHighlights();
}

function renderPageScopedResults() {
  if (!state.result) {
    return;
  }
  const views = pageScopedResultViews();
  els.jsonView.textContent = views.json;
  els.markdownView.textContent = views.markdown;
  renderBlocks();
  renderHtmlView();
}

function renderBlocks() {
  els.blocksView.replaceChildren();
  const page = currentPage();
  if (!page) {
    const card = document.createElement("article");
    card.className = "block-card";
    card.innerHTML = '<div class="block-type">결과 없음</div><div class="block-text">표시할 페이지 결과가 없습니다.</div>';
    els.blocksView.appendChild(card);
    return;
  }

  const pageHeader = document.createElement("div");
  pageHeader.className = "block-card page-summary";
  pageHeader.innerHTML = `<div class="block-type">${escapeHtml(pageTitle(page))}</div><div class="block-text">${escapeHtml(page.width)} x ${escapeHtml(page.height)}</div>`;
  els.blocksView.appendChild(pageHeader);

  const blocks = Array.isArray(page.blocks) ? page.blocks : [];
  if (!blocks.length) {
    const empty = document.createElement("article");
    empty.className = "block-card";
    empty.innerHTML = '<div class="block-type">영역 없음</div><div class="block-text">이 쪽에서 표시할 영역을 찾지 못했습니다.</div>';
    els.blocksView.appendChild(empty);
    return;
  }

  blocks.forEach((block, index) => {
    const label = String(block.label || "text").toLowerCase();
    const blockKey = blockDomKey(page, block, index);
    const card = document.createElement("article");
    card.className = `block-card ${label}`;
    card.tabIndex = 0;
    card.dataset.blockKey = blockKey;
    card.dataset.blockRole = "result";
    card.setAttribute("aria-label", `${blockLabelText(label)} ${index + 1} 위치 보기`);

    const head = document.createElement("div");
    head.className = "block-card-head";
    const type = document.createElement("div");
    type.className = "block-type";
    type.textContent = `${blockLabelText(label)} ${index + 1}`;
    head.appendChild(type);

    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "block-edit-button";
    editButton.innerHTML = [
      '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">',
      '<path d="M4 20h4.4L19.7 8.7a2.1 2.1 0 0 0 0-3l-1.4-1.4a2.1 2.1 0 0 0-3 0L4 15.6V20Z"></path>',
      '<path d="M13.8 5.8l4.4 4.4"></path>',
      '</svg>',
    ].join("");
    editButton.title = `${blockLabelText(label)} ${index + 1} 수정`;
    editButton.setAttribute("aria-label", editButton.title);
    editButton.addEventListener("click", (event) => {
      event.stopPropagation();
      openBlockEditor({ pageIndex: state.pageIndex, blockIndex: index });
    });
    head.appendChild(editButton);
    card.appendChild(head);

    const asset = findBlockAsset(page, block);
    if (asset && asset.url) {
      const image = document.createElement("img");
      image.className = "block-image-preview";
      image.src = asset.url;
      image.alt = asset.alt || blockLabelText(label);
      card.appendChild(image);
    }

    if (label === "table") {
      const table = renderBlockTable(block);
      if (table) {
        card.appendChild(table);
      } else {
        card.appendChild(renderBlockText(block));
      }
    } else {
      card.appendChild(renderBlockText(block));
    }
    els.blocksView.appendChild(card);
  });
}

function renderHtmlView() {
  if (!state.result) {
    return;
  }
  els.htmlView.replaceChildren();
  const html = pageScopedResultViews().html;
  if (els.renderHtml.checked) {
    const iframe = document.createElement("iframe");
    iframe.setAttribute("sandbox", "");
    iframe.srcdoc = html;
    els.htmlView.appendChild(iframe);
  } else {
    const pre = document.createElement("pre");
    pre.className = "html-source";
    pre.textContent = html;
    els.htmlView.appendChild(pre);
  }
}

function renderPage() {
  const page = currentPage();
  if (!page) {
    els.pageImage.removeAttribute("src");
    els.overlayLayer.replaceChildren();
    return;
  }
  const totalPages = resultTotalPages();
  const currentNumber = state.pageIndex + 1;
  const sourceName = page.source_file || state.result?.metadata?.source_file || "";
  els.pageLabel.innerHTML = [
    '<span class="page-label-main">',
    `<strong>${escapeHtml(currentNumber)}</strong>`,
    "<span>/</span>",
    `<strong>${escapeHtml(totalPages || currentNumber)}</strong>`,
    "<em>쪽</em>",
    "</span>",
    sourceName ? `<span class="page-label-source">${escapeHtml(sourceName)}</span>` : "",
  ].join("");
  els.pageLabel.title = sourceName
    ? `${sourceName} · 현재 ${currentNumber} / 전체 ${totalPages || currentNumber}쪽`
    : `현재 ${currentNumber} / 전체 ${totalPages || currentNumber}쪽`;
  els.prevPage.disabled = state.pageIndex <= 0;
  els.nextPage.disabled = state.pageIndex >= (state.result.pages || []).length - 1;
  els.pageImage.src = page.image_url || "";
  els.overlayLayer.replaceChildren();
  updateThumbSelection();

  const width = Number(page.width || 0);
  const height = Number(page.height || 0);
  if (!width || !height) {
    return;
  }
  (page.blocks || []).forEach((block, index) => {
    const bbox = Array.isArray(block.bbox) ? block.bbox : null;
    if (!bbox || bbox.length !== 4) {
      return;
    }
    const [x0, y0, x1, y1] = bbox.map(Number);
    if (!(x1 > x0 && y1 > y0)) {
      return;
    }
    const label = String(block.label || "text").toLowerCase();
    const blockKey = blockDomKey(page, block, index);
    const box = document.createElement("div");
    box.className = `bbox ${label}`;
    box.tabIndex = 0;
    box.dataset.blockKey = blockKey;
    box.dataset.blockRole = "overlay";
    box.title = `${blockLabelText(label)} ${index + 1}: ${blockDisplayText(block).replace(/\s+/g, " ").slice(0, 120)}`;
    box.style.left = `${(x0 / width) * 100}%`;
    box.style.top = `${(y0 / height) * 100}%`;
    box.style.width = `${((x1 - x0) / width) * 100}%`;
    box.style.height = `${((y1 - y0) / height) * 100}%`;
    const caption = document.createElement("span");
    caption.textContent = blockLabelText(label);
    box.appendChild(caption);
    els.overlayLayer.appendChild(box);
  });
}

function renderThumbs() {
  els.thumbStrip.replaceChildren();
  const toggleButton = document.createElement("button");
  toggleButton.type = "button";
  toggleButton.className = "thumb-collapse-toggle";
  toggleButton.title = state.thumbsCollapsed ? "작은 페이지 펼치기" : "작은 페이지 숨기기";
  toggleButton.setAttribute("aria-label", toggleButton.title);
  toggleButton.setAttribute("aria-expanded", String(!state.thumbsCollapsed));
  toggleButton.innerHTML = '<span aria-hidden="true"></span>';
  toggleButton.addEventListener("click", () => {
    state.thumbsCollapsed = !state.thumbsCollapsed;
    syncThumbStripState();
  });

  const prevButton = document.createElement("button");
  prevButton.type = "button";
  prevButton.className = "thumb-nav thumb-nav-prev";
  prevButton.title = "이전 작은 페이지";
  prevButton.setAttribute("aria-label", "이전 작은 페이지");
  prevButton.textContent = "<";

  const viewport = document.createElement("div");
  viewport.className = "thumb-strip-viewport";

  const track = document.createElement("div");
  track.className = "thumb-strip-track";

  const nextButton = document.createElement("button");
  nextButton.type = "button";
  nextButton.className = "thumb-nav thumb-nav-next";
  nextButton.title = "다음 작은 페이지";
  nextButton.setAttribute("aria-label", "다음 작은 페이지");
  nextButton.textContent = ">";

  const scrollThumbs = (direction) => {
    const step = Math.max(220, Math.floor(viewport.clientWidth * 0.72));
    viewport.scrollBy({ left: direction * step, behavior: "smooth" });
  };
  prevButton.addEventListener("click", () => scrollThumbs(-1));
  nextButton.addEventListener("click", () => scrollThumbs(1));

  (state.result.pages || []).forEach((page, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "thumb-page";
    button.classList.toggle("active", index === state.pageIndex);
    button.title = `${page.page_number}쪽`;
    const image = document.createElement("img");
    image.src = page.image_url || "";
    image.alt = `${page.page_number}쪽`;
    button.appendChild(image);
    button.addEventListener("click", () => {
      state.autoFollowLatestPage = false;
      state.pageIndex = index;
      renderSelectedPage();
    });
    track.appendChild(button);
  });
  viewport.appendChild(track);
  els.thumbStrip.append(toggleButton, prevButton, viewport, nextButton);
  syncThumbStripState();
}

function updateThumbSelection() {
  els.thumbStrip.querySelectorAll(".thumb-page").forEach((button, index) => {
    const active = index === state.pageIndex;
    button.classList.toggle("active", active);
    if (active) {
      button.scrollIntoView({ block: "nearest", inline: "center" });
    }
  });
}

function syncThumbStripState() {
  if (!els.thumbStrip) {
    return;
  }
  els.thumbStrip.classList.toggle("is-collapsed", state.thumbsCollapsed);
  els.documentPane?.classList.toggle("thumbs-collapsed", state.thumbsCollapsed);
  const toggleButton = els.thumbStrip.querySelector(".thumb-collapse-toggle");
  if (toggleButton) {
    toggleButton.title = state.thumbsCollapsed ? "작은 페이지 펼치기" : "작은 페이지 숨기기";
    toggleButton.setAttribute("aria-label", toggleButton.title);
    toggleButton.setAttribute("aria-expanded", String(!state.thumbsCollapsed));
  }
}

function currentPage() {
  if (!state.result || !Array.isArray(state.result.pages)) {
    return null;
  }
  return state.result.pages[state.pageIndex] || null;
}

function resultTotalPages(payload = state.result) {
  if (!payload || typeof payload !== "object") {
    return 0;
  }
  const progress = payload.progress || {};
  const pages = Array.isArray(payload.pages) ? payload.pages : [];
  const value = Number(progress.total_pages ?? payload.page_count ?? Math.max(pages.length, 0));
  return Number.isFinite(value) && value > 0 ? value : pages.length;
}

function findBlockAsset(page, block) {
  const blockId = String(block.block_id || "");
  const crops = pageAssets(page).filter((asset) => asset.kind === "crop" || asset.kind === "manual");
  if (blockId) {
    const byId = crops.find((asset) => asset.block_id === blockId);
    if (byId) {
      return byId;
    }
  }
  const bbox = JSON.stringify(block?.bbox || []);
  return crops.find((asset) => JSON.stringify(asset.bbox || []) === bbox) || null;
}

function pageAssets(page) {
  if (!page) {
    return [];
  }
  const localAssets = Array.isArray(page.assets) ? page.assets : [];
  if (localAssets.length) {
    return localAssets;
  }
  const pageIndex = Number(page.page_index ?? state.pageIndex);
  return (state.result?.assets || []).filter((asset) => Number(asset.page_index) === pageIndex);
}

function pageTitle(page) {
  const pageNumber = page?.page_number ?? state.pageIndex + 1;
  const source = page?.source_file || state.result?.metadata?.source_file || "";
  return source ? `${source} · ${pageNumber}쪽` : `${pageNumber}쪽`;
}

function blockDomKey(page, block, index) {
  if (block && typeof block === "object") {
    const cachedKey = linkedBlockKeys.get(block);
    if (cachedKey) {
      return cachedKey;
    }
  }
  return rawBlockDomKey(page, block, index);
}

function rawBlockDomKey(page, block, index, { fallbackPageIndex = state.pageIndex, fallbackSource = state.result?.metadata?.source_file || "" } = {}) {
  const pageIndex = page?.page_index ?? fallbackPageIndex;
  const pageNumber = page?.page_number ?? Number(pageIndex) + 1;
  const pageKey = [
    page?.source_file || fallbackSource || "",
    pageIndex,
    pageNumber,
  ].join("|");
  const blockId = String(block?.block_id || "").trim();
  if (blockId) {
    return `${pageKey}|id:${blockId}`;
  }
  const bbox = Array.isArray(block?.bbox) ? block.bbox.map((value) => Number(value)).join(",") : "";
  const label = String(block?.label || "text").toLowerCase();
  const text = String(block?.text || "").replace(/\s+/g, " ").trim().slice(0, 80);
  return `${pageKey}|seq:${index}|${label}|${bbox || "no-bbox"}|${text}`;
}

function activeLinkedBlockKey() {
  return state.linkedBlockHoverKey || state.linkedBlockPinnedKey || "";
}

function syncLinkedBlockHighlights({ scrollOverlay = false, scrollResult = false } = {}) {
  const activeKey = activeLinkedBlockKey();
  let activeOverlay = null;
  let activeResult = null;
  document.querySelectorAll("[data-block-key]").forEach((node) => {
    const isActive = Boolean(activeKey) && node.dataset.blockKey === activeKey;
    const isPinned = Boolean(state.linkedBlockPinnedKey) && node.dataset.blockKey === state.linkedBlockPinnedKey;
    node.classList.toggle("is-linked-active", isActive);
    node.classList.toggle("is-linked-pinned", isPinned);
    if (isActive && node.dataset.blockRole === "overlay") {
      activeOverlay = node;
    }
    if (isActive && node.dataset.blockRole === "result") {
      activeResult = node;
    }
  });
  if (scrollOverlay && activeOverlay) {
    activeOverlay.scrollIntoView({ block: "center", inline: "center", behavior: "smooth" });
  }
  if (scrollResult && activeResult) {
    activeResult.scrollIntoView({ block: "center", inline: "nearest", behavior: "smooth" });
  }
}

function setLinkedBlockHover(key) {
  if (state.linkedBlockHoverKey === key) {
    return;
  }
  state.linkedBlockHoverKey = key;
  syncLinkedBlockHighlights();
}

function clearLinkedBlockHover(key) {
  if (key && state.linkedBlockHoverKey !== key) {
    return;
  }
  state.linkedBlockHoverKey = "";
  syncLinkedBlockHighlights();
}

function clearLinkedBlockSelection() {
  state.linkedBlockHoverKey = "";
  state.linkedBlockPinnedKey = "";
  syncLinkedBlockHighlights();
}

function pinLinkedBlock(key, role) {
  if (!key) {
    return;
  }
  state.linkedBlockPinnedKey = key;
  state.linkedBlockHoverKey = key;
  syncLinkedBlockHighlights({
    scrollOverlay: role === "result",
    scrollResult: role === "overlay",
  });
}

function closestLinkedBlock(target) {
  return target && typeof target.closest === "function" ? target.closest("[data-block-key]") : null;
}

function blockDisplayText(block) {
  const text = String(block?.text || "").trim();
  return text || `위치=${JSON.stringify(block?.bbox || [])}`;
}

function renderBlockText(block) {
  const text = document.createElement("div");
  text.className = "block-text";
  text.textContent = blockDisplayText(block);
  return text;
}

function renderBlockTable(block) {
  const rows = tableRowsFromBlock(block);
  if (!rows.length) {
    return null;
  }
  const frame = document.createElement("div");
  frame.className = "block-table-scroll";
  const table = document.createElement("table");
  table.className = "block-table-rendered";
  rows.forEach((row) => {
    const tr = document.createElement("tr");
    row.forEach((cell, cellIndex) => {
      const tagName = cellIndex === 0 ? "th" : "td";
      const cellEl = document.createElement(tagName);
      if (cellIndex === 0) {
        cellEl.scope = "row";
      }
      cellEl.textContent = cell;
      tr.appendChild(cellEl);
    });
    table.appendChild(tr);
  });
  frame.appendChild(table);
  return frame;
}

function tableRowsFromBlock(block) {
  const structuredCandidates = [
    block?.rows,
    block?.table_rows,
    block?.table?.rows,
    block?.table?.body,
    block?.metadata?.rows,
    block?.metadata?.table_rows,
  ];
  for (const candidate of structuredCandidates) {
    const rows = normalizeTableRows(candidate);
    if (rows.length) {
      return rows;
    }
  }

  const cellCandidates = [
    block?.cells,
    block?.table?.cells,
    block?.metadata?.cells,
    block?.metadata?.table_cells,
  ];
  for (const candidate of cellCandidates) {
    const rows = tableRowsFromFlatCells(candidate);
    if (rows.length) {
      return rows;
    }
  }

  return parseTableTextRows(block?.html || block?.text || "");
}

function normalizeTableRows(candidate) {
  if (!Array.isArray(candidate) || !candidate.length) {
    return [];
  }
  const rows = candidate.map((row) => normalizeTableRow(row)).filter((row) => row.length);
  return normalizeTableShape(rows);
}

function normalizeTableRow(row) {
  if (Array.isArray(row)) {
    return row.map(tableCellText).filter((cell) => cell.length);
  }
  if (!row || typeof row !== "object") {
    return [tableCellText(row)].filter((cell) => cell.length);
  }
  for (const key of ["cells", "columns", "values", "items"]) {
    if (Array.isArray(row[key])) {
      return row[key].map(tableCellText).filter((cell) => cell.length);
    }
  }
  const label = tableCellText(row.header ?? row.label ?? row.name ?? row.key);
  const value = tableCellText(row.value ?? row.text ?? row.content);
  return [label, value].filter((cell) => cell.length);
}

function tableRowsFromFlatCells(candidate) {
  if (!Array.isArray(candidate) || !candidate.length) {
    return [];
  }
  const matrix = new Map();
  candidate.forEach((cell, index) => {
    if (!cell || typeof cell !== "object") {
      return;
    }
    const rowIndex = Number(cell.row ?? cell.row_index ?? cell.r ?? cell.y ?? 0);
    const colIndex = Number(cell.col ?? cell.column ?? cell.col_index ?? cell.c ?? cell.x ?? index);
    const text = tableCellText(cell);
    if (!text) {
      return;
    }
    const row = Number.isFinite(rowIndex) ? rowIndex : 0;
    const col = Number.isFinite(colIndex) ? colIndex : index;
    if (!matrix.has(row)) {
      matrix.set(row, new Map());
    }
    matrix.get(row).set(col, text);
  });
  const rows = Array.from(matrix.entries())
    .sort((a, b) => a[0] - b[0])
    .map(([, cols]) => Array.from(cols.entries()).sort((a, b) => a[0] - b[0]).map(([, text]) => text));
  return normalizeTableShape(rows);
}

function tableCellText(value) {
  if (value == null) {
    return "";
  }
  if (Array.isArray(value)) {
    return value.map(tableCellText).filter(Boolean).join(" ");
  }
  if (typeof value === "object") {
    for (const key of ["text", "value", "content", "html", "markdown", "label", "name", "key"]) {
      const text = tableCellText(value[key]);
      if (text) {
        return text;
      }
    }
    return "";
  }
  return String(value).replace(/\s+/g, " ").trim();
}

function parseTableTextRows(value) {
  const raw = String(value || "").replace(/\r\n?/g, "\n").trim();
  if (!raw) {
    return [];
  }
  const htmlRows = tableRowsFromHtml(raw);
  if (htmlRows.length) {
    return htmlRows;
  }
  const lines = raw.split("\n").map((line) => line.trim()).filter(Boolean);
  if (!lines.length) {
    return [];
  }

  const delimitedRows = lines
    .map(splitDelimitedTableLine)
    .filter((row) => row.length > 1);
  if (delimitedRows.length === lines.length || delimitedRows.length >= 2) {
    return normalizeTableShape(delimitedRows);
  }

  const groups = raw
    .split(/\n\s*\n/)
    .map((group) => group.split("\n").map((line) => line.trim()).filter(Boolean))
    .filter((group) => group.length);
  if (groups.length >= 2 && groups.every((group) => group.length >= 2)) {
    return normalizeTableShape(groups.map((group) => [group[0], group.slice(1).join("\n")]));
  }

  const columnarRows = rowsFromColumnarGroups(groups);
  if (columnarRows.length >= 2) {
    return normalizeTableShape(columnarRows);
  }

  const keyValueRows = rowsFromKeyValueGroups(groups);
  if (keyValueRows.length >= 2) {
    return normalizeTableShape(keyValueRows);
  }

  if (lines.length >= 2 && lines.length % 2 === 0 && lines.length <= 24) {
    const pairedRows = [];
    for (let index = 0; index < lines.length; index += 2) {
      pairedRows.push([lines[index], lines[index + 1]]);
    }
    return normalizeTableShape(pairedRows);
  }
  return [];
}

function rowsFromColumnarGroups(groups) {
  if (!Array.isArray(groups) || groups.length < 2) {
    return [];
  }
  const header = Array.isArray(groups[0]) ? groups[0].filter(Boolean) : [];
  const columnCount = header.length;
  if (columnCount < 3) {
    return [];
  }

  const rows = [header];
  let currentRow = null;
  const pushCurrent = () => {
    if (!currentRow) {
      return;
    }
    rows.push(currentRow);
    currentRow = null;
  };

  groups.slice(1).forEach((group) => {
    if (!Array.isArray(group) || !group.length) {
      return;
    }
    if (group.length >= 2) {
      pushCurrent();
      currentRow = group.slice(0, columnCount);
      if (group.length > columnCount) {
        currentRow[columnCount - 1] = group.slice(columnCount - 1).join("\n");
      }
      return;
    }
    const only = String(group[0] || "").trim();
    if (!only || /^\[\s*펼치기\s*·\s*접기\s*\]$/.test(only)) {
      return;
    }
    if (!currentRow) {
      currentRow = [only];
      return;
    }
    while (currentRow.length < columnCount - 1) {
      currentRow.push("");
    }
    currentRow[columnCount - 1] = currentRow[columnCount - 1]
      ? `${currentRow[columnCount - 1]}\n${only}`
      : only;
  });
  pushCurrent();
  return rows;
}

function rowsFromKeyValueGroups(groups) {
  if (!Array.isArray(groups) || groups.length < 2) {
    return [];
  }
  const rows = [];
  groups.forEach((group, index) => {
    if (!Array.isArray(group) || !group.length) {
      return;
    }
    if (group.length >= 2) {
      rows.push([group[0], group.slice(1).join("\n")]);
      return;
    }
    const only = String(group[0] || "").trim();
    if (!only || /^\[\s*펼치기\s*·\s*접기\s*\]$/.test(only)) {
      return;
    }
    const delimited = splitDelimitedTableLine(only);
    if (delimited.length > 1) {
      rows.push(delimited);
      return;
    }
    const previous = rows[rows.length - 1];
    const nextGroup = groups[index + 1];
    if (previous && ((previous[0] === "" && previous[1]) || (previous[1] && Array.isArray(nextGroup) && nextGroup.length === 1))) {
      rows.push(["", only]);
      return;
    }
    rows.push([only, ""]);
  });
  return rows.filter((row) => row.length > 1);
}

function tableRowsFromHtml(value) {
  if (!/<table[\s>]/i.test(value)) {
    return [];
  }
  const template = document.createElement("template");
  template.innerHTML = value;
  const table = template.content.querySelector("table");
  if (!table) {
    return [];
  }
  const rows = Array.from(table.querySelectorAll("tr")).map((tr) => (
    Array.from(tr.querySelectorAll("th,td")).map((cell) => tableCellText(cell.textContent))
  ));
  return normalizeTableShape(rows);
}

function splitDelimitedTableLine(line) {
  if (line.includes("|")) {
    const cells = line.replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
    if (cells.length > 1 && !cells.every((cell) => /^:?-{3,}:?$/.test(cell))) {
      return cells.filter(Boolean);
    }
  }
  if (line.includes("\t")) {
    const cells = line.split("\t").map((cell) => cell.trim()).filter(Boolean);
    if (cells.length > 1) {
      return cells;
    }
  }
  const spaced = line.split(/\s{2,}/).map((cell) => cell.trim()).filter(Boolean);
  return spaced.length > 1 ? spaced : [];
}

function normalizeTableShape(rows) {
  const cleanedRows = rows
    .map((row) => row.map((cell) => String(cell || "").trim()).filter((cell) => cell.length))
    .filter((row) => row.length);
  if (!cleanedRows.length) {
    return [];
  }
  const columnCount = Math.max(...cleanedRows.map((row) => row.length));
  if (columnCount <= 1) {
    return [];
  }
  return cleanedRows.map((row) => {
    const padded = row.slice();
    while (padded.length < columnCount) {
      padded.push("");
    }
    return padded;
  });
}

function populateEditLabelOptions() {
  if (!els.editBlockLabel || els.editBlockLabel.children.length) {
    return;
  }
  EDIT_LABELS.forEach((label) => {
    const option = document.createElement("option");
    option.value = label;
    option.textContent = blockLabelText(label);
    els.editBlockLabel.appendChild(option);
  });
}

function ensureEditLabelOption(label) {
  if (!els.editBlockLabel) {
    return;
  }
  const value = String(label || "text").toLowerCase();
  if (Array.from(els.editBlockLabel.options).some((option) => option.value === value)) {
    return;
  }
  const option = document.createElement("option");
  option.value = value;
  option.textContent = blockLabelText(value);
  els.editBlockLabel.appendChild(option);
}

function openBlockEditor({ pageIndex, blockIndex }) {
  const page = state.result?.pages?.[pageIndex];
  const block = page?.blocks?.[blockIndex];
  if (!page || !block || !els.blockEditModal) {
    return;
  }
  populateEditLabelOptions();
  const label = String(block.label || "text").toLowerCase();
  ensureEditLabelOption(label);
  state.editTarget = { pageIndex, blockIndex };
  state.editImageData = null;
  state.removeManualImage = false;
  els.editBlockEyebrow.textContent = pageTitle(page);
  els.editBlockTitle.textContent = `${blockLabelText(label)} ${blockIndex + 1}`;
  els.editBlockLabel.value = label;
  els.editBlockText.value = String(block.text || "");
  const tableRows = tableRowsFromBlock(block);
  els.editTableText.value = tableRows.length ? tableRowsToText(tableRows) : String(block.text || "");
  setEditStatus("");
  const asset = findBlockAsset(page, block);
  setEditImagePreview(asset?.url || "");
  syncEditDialogMode();
  els.blockEditModal.hidden = false;
  els.editBlockText.focus();
}

function closeBlockEditor() {
  if (!els.blockEditModal) {
    return;
  }
  els.blockEditModal.hidden = true;
  state.editTarget = null;
  state.editImageData = null;
  state.removeManualImage = false;
  setEditStatus("");
  if (els.editImageInput) {
    els.editImageInput.value = "";
  }
}

function syncEditDialogMode() {
  const label = String(els.editBlockLabel?.value || "text").toLowerCase();
  if (els.editTableField) {
    els.editTableField.hidden = label !== "table";
  }
}

function setEditStatus(text, isError = false) {
  if (!els.editBlockStatus) {
    return;
  }
  els.editBlockStatus.textContent = text;
  els.editBlockStatus.classList.toggle("error", isError);
}

function tableRowsToText(rows) {
  return (rows || [])
    .map((row) => row.map((cell) => String(cell || "").trim()).join("\t"))
    .join("\n");
}

function parseEditTableRows(value) {
  const rows = parseTableTextRows(value);
  if (rows.length) {
    return rows;
  }
  return String(value || "")
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => line.split(/\t|,/).map((cell) => cell.trim()).filter(Boolean))
    .filter((row) => row.length > 1);
}

async function saveBlockEdit(event) {
  event.preventDefault();
  const target = state.editTarget;
  const requestId = state.result?.request_id;
  if (!target || !requestId) {
    setEditStatus("수정할 영역을 찾지 못했습니다.", true);
    return;
  }
  const label = String(els.editBlockLabel.value || "text").toLowerCase();
  let text = els.editBlockText.value;
  const body = { label, text };
  if (label === "table") {
    const tableRows = parseEditTableRows(els.editTableText.value);
    body.table_rows = tableRows;
    if (tableRows.length) {
      text = tableRowsToText(tableRows);
      body.text = text;
    }
  }
  if (state.editImageData) {
    body.image = state.editImageData;
  }
  if (state.removeManualImage) {
    body.remove_manual_image = true;
  }
  els.saveBlockEdit.disabled = true;
  setEditStatus("저장 중입니다...");
  try {
    const response = await fetch(
      `api/convert/${encodeURIComponent(requestId)}/blocks/${target.pageIndex}/${target.blockIndex}`,
      {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "save failed");
    }
    state.autoFollowLatestPage = false;
    const activeResult = state.activeResult;
    applyConversionPayload(payload, { final: payload.status === "complete" });
    state.pageIndex = Math.min(target.pageIndex, Math.max(0, (state.result?.pages || []).length - 1));
    renderSelectedPage();
    setActiveResult(activeResult);
    closeBlockEditor();
    setStatus("수정 내용을 저장했습니다.");
    refreshHistoryAfterConversion();
  } catch (error) {
    setEditStatus(error.message || "저장에 실패했습니다.", true);
  } finally {
    els.saveBlockEdit.disabled = false;
  }
}

function setEditImagePreview(src) {
  if (!els.editImagePreview) {
    return;
  }
  if (src) {
    els.editImagePreview.src = src;
    els.editImagePreview.hidden = false;
    return;
  }
  els.editImagePreview.removeAttribute("src");
  els.editImagePreview.hidden = true;
}

async function readEditImageFile(file) {
  if (!file || !file.type.startsWith("image/")) {
    setEditStatus("이미지 파일만 넣을 수 있습니다.", true);
    return;
  }
  const dataUrl = await fileToDataUrl(file);
  state.editImageData = {
    data_url: dataUrl,
    file_name: file.name || "manual-image.png",
    media_type: file.type || "image/png",
  };
  state.removeManualImage = false;
  setEditImagePreview(dataUrl);
  setEditStatus("이미지를 교체 대상으로 올렸습니다.");
}

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("file read failed"));
    reader.readAsDataURL(file);
  });
}

function clearEditImage() {
  state.editImageData = null;
  state.removeManualImage = true;
  setEditImagePreview("");
  if (els.editImageInput) {
    els.editImageInput.value = "";
  }
  setEditStatus("저장하면 직접 넣은 이미지가 제거됩니다.");
}

function imageFileFromTransfer(dataTransfer) {
  if (!dataTransfer) {
    return null;
  }
  const files = Array.from(dataTransfer.files || []);
  const fromFiles = files.find((file) => file.type.startsWith("image/"));
  if (fromFiles) {
    return fromFiles;
  }
  const items = Array.from(dataTransfer.items || []);
  const imageItem = items.find((item) => item.kind === "file" && item.type.startsWith("image/"));
  return imageItem ? imageItem.getAsFile() : null;
}

function pageScopedResultViews() {
  if (!state.result) {
    return { blocks: "", json: "", html: "", markdown: "" };
  }
  const page = currentPage();
  if (!page) {
    const views = state.result.views || {};
    return {
      blocks: views.blocks || "",
      json: views.json || JSON.stringify(state.result, null, 2),
      html: views.html || "",
      markdown: views.markdown || "",
    };
  }
  return {
    blocks: renderPageBlocksText(page),
    json: renderPageJson(page),
    html: renderPageHtml(page),
    markdown: renderPageMarkdown(page),
  };
}

function renderPageBlocksText(page) {
  const lines = [
    pageTitle(page),
    `${page.width || 0} x ${page.height || 0}`,
  ];
  (page.blocks || []).forEach((block, index) => {
    const label = blockLabelText(String(block.label || "text").toLowerCase());
    lines.push(`${index + 1}. ${label} ${blockDisplayText(block).replace(/\s+/g, " ")}`);
  });
  return lines.join("\n");
}

function renderPageJson(page) {
  const payload = {
    request_id: state.result.request_id,
    status: state.result.status,
    source_file: page.source_file || state.result.metadata?.source_file || null,
    page_index: page.page_index ?? state.pageIndex,
    page_number: page.page_number ?? state.pageIndex + 1,
    page_count: state.result.page_count,
    processed_page_count: state.result.processed_page_count,
    progress: state.result.progress || null,
    page: {
      page_index: page.page_index ?? state.pageIndex,
      page_number: page.page_number ?? state.pageIndex + 1,
      width: page.width || 0,
      height: page.height || 0,
      image_url: page.image_url || null,
      blocks: Array.isArray(page.blocks) ? page.blocks : [],
      articles: Array.isArray(page.articles) ? page.articles : [],
      assets: pageAssets(page),
    },
  };
  return JSON.stringify(payload, null, 2);
}

function renderPageMarkdown(page) {
  const lines = [`# ${pageTitle(page)}`];
  (page.blocks || []).forEach((block) => {
    const label = String(block.label || "text").toLowerCase();
    const text = String(block.text || "").trim();
    const tableMarkdown = label === "table" ? renderMarkdownTable(block) : "";
    const asset = findBlockAsset(page, block);
    if (asset && asset.url) {
      lines.push("", `![${markdownAlt(asset.alt || blockLabelText(label))}](${asset.url})`);
      if (IMAGE_LABELS.has(label)) {
        return;
      }
    }
    if (!text && !tableMarkdown) {
      return;
    }
    lines.push("");
    if (["title", "sectionheader", "section_header", "heading"].includes(label)) {
      lines.push(`## ${text}`);
    } else if (label === "table") {
      lines.push(tableMarkdown || text);
    } else if (label === "code_block") {
      lines.push("```text", text, "```");
    } else if (["equation_block", "chemical_block"].includes(label)) {
      lines.push("$$", text, "$$");
    } else if (["form", "table_of_contents", "bibliography", "complex_block", "blank_page"].includes(label)) {
      lines.push(`**${blockLabelText(label)}**`, "", text);
    } else if (["caption", "footnote", "pageheader", "pagefooter", "header", "footer"].includes(label)) {
      lines.push(`*${text}*`);
    } else {
      lines.push(text);
    }
  });
  return `${lines.join("\n").trim()}\n`;
}

function renderPageHtml(page) {
  const title = pageTitle(page);
  const parts = [
    '<!doctype html><html><head><meta charset="utf-8">',
    `<title>${escapeHtml(title)}</title>`,
    '<style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.55;margin:28px;color:#111}img{max-width:100%;height:auto}figure{margin:18px 0}figcaption{color:#6e6e73;font-size:13px}.page{margin-bottom:32px}.block-caption{color:#5f6368;font-style:italic}.block-structured{white-space:pre-wrap;background:#fff7ed;border-left:4px solid #c2410c;padding:12px}.block-table-wrap{overflow-x:auto;margin:16px 0}.block-table{border-collapse:collapse;min-width:320px}.block-table th,.block-table td{border:1px solid #d8dee9;padding:8px 10px;text-align:left;vertical-align:top}.block-table th{background:#f8fafc;font-weight:700}</style>',
    "</head><body>",
    `<section class="page" data-page="${escapeHtml(page.page_number ?? state.pageIndex + 1)}">`,
    `<h1>${escapeHtml(title)}</h1>`,
  ];
  (page.blocks || []).forEach((block) => {
    const label = String(block.label || "text").toLowerCase();
    const asset = findBlockAsset(page, block);
    if (asset && asset.url) {
      parts.push("<figure>");
      parts.push(`<img src="${escapeHtml(asset.url)}" alt="${escapeHtml(asset.alt || blockLabelText(label))}">`);
      if (asset.alt) {
        parts.push(`<figcaption>${escapeHtml(asset.alt)}</figcaption>`);
      }
      parts.push("</figure>");
      if (IMAGE_LABELS.has(label)) {
        return;
      }
    }
    const text = String(block.text || "").trim();
    const blockId = escapeHtml(block.block_id || "");
    const blockAttr = blockId ? ` data-block-id="${blockId}"` : "";
    const tableHtml = label === "table" ? renderHtmlTable(block, blockAttr) : "";
    if (!text && !tableHtml) {
      return;
    }
    const htmlText = escapeHtml(text).replace(/\n/g, "<br>");
    if (["title", "sectionheader", "section_header", "heading"].includes(label)) {
      parts.push(`<h2 class="block block-${escapeHtml(label)}"${blockAttr}>${htmlText}</h2>`);
    } else if (label === "table") {
      parts.push(tableHtml || `<p class="block block-${escapeHtml(label)}"${blockAttr}>${htmlText}</p>`);
    } else if (["code_block", "equation_block", "chemical_block", "form", "complex_block"].includes(label)) {
      parts.push(`<pre class="block block-structured block-${escapeHtml(label)}"${blockAttr}>${htmlText}</pre>`);
    } else if (["caption", "footnote", "pageheader", "pagefooter", "header", "footer"].includes(label)) {
      parts.push(`<p class="block block-caption block-${escapeHtml(label)}"${blockAttr}>${htmlText}</p>`);
    } else {
      parts.push(`<p class="block block-${escapeHtml(label)}"${blockAttr}>${htmlText}</p>`);
    }
  });
  parts.push("</section></body></html>");
  return parts.join("");
}

function renderMarkdownTable(block) {
  const rows = tableRowsFromBlock(block);
  if (!rows.length) {
    return "";
  }
  const escapeCell = (value) => String(value || "").replace(/\|/g, "\\|").replace(/\s+/g, " ").trim();
  const columnCount = Math.max(...rows.map((row) => row.length));
  const normalizedRows = rows.map((row) => {
    const padded = row.map(escapeCell);
    while (padded.length < columnCount) {
      padded.push("");
    }
    return padded;
  });
  const header = normalizedRows[0];
  const body = normalizedRows.slice(1);
  return [
    `| ${header.join(" | ")} |`,
    `| ${Array(columnCount).fill("---").join(" | ")} |`,
    ...body.map((row) => `| ${row.join(" | ")} |`),
  ].join("\n");
}

function renderHtmlTable(block, blockAttr = "") {
  const rows = tableRowsFromBlock(block);
  if (!rows.length) {
    return "";
  }
  const body = rows.map((row) => {
    const cells = row.map((cell, index) => {
      const tag = index === 0 ? "th" : "td";
      const scope = index === 0 ? ' scope="row"' : "";
      return `<${tag}${scope}>${escapeHtml(cell)}</${tag}>`;
    }).join("");
    return `<tr>${cells}</tr>`;
  }).join("");
  return `<div class="block block-table-wrap"${blockAttr}><table class="block-table"><tbody>${body}</tbody></table></div>`;
}

function markdownAlt(value) {
  return String(value || "").replace(/[\\[\]()]/g, "\\$&").replace(/\s+/g, " ").trim();
}

function activeText() {
  if (!state.result) {
    return "";
  }
  const views = pageScopedResultViews();
  if (state.activeResult === "json") {
    return views.json;
  }
  if (state.activeResult === "html") {
    return views.html;
  }
  if (state.activeResult === "markdown") {
    return views.markdown;
  }
  return views.blocks;
}

async function copyActiveText() {
  const text = activeText();
  if (!text) {
    return;
  }
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
    } else {
      fallbackCopy(text);
    }
    setStatus("결과를 복사했습니다.");
  } catch (error) {
    fallbackCopy(text);
    setStatus("결과를 복사했습니다.");
  }
}

function downloadResult() {
  if (!state.result) {
    return;
  }
  if (Array.isArray(state.result.batch_downloads) && state.result.batch_downloads.length) {
    state.result.batch_downloads.forEach((item, index) => {
      window.setTimeout(() => {
        const link = document.createElement("a");
        link.href = item.url;
        link.download = "";
        document.body.appendChild(link);
        link.click();
        link.remove();
      }, index * 250);
    });
    setStatus("파일별 ZIP 다운로드를 시작했습니다.");
    return;
  }
  if (!state.result.download_url) {
    setStatus("다운로드할 ZIP이 없습니다.", true);
    return;
  }
  window.location.href = state.result.download_url;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function fallbackCopy(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.left = "-9999px";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
}

function blockLabelText(label) {
  const labels = {
    text: "본문",
    title: "제목",
    image: "그림",
    picture: "그림",
    caption: "설명",
    footnote: "각주",
    table: "표",
    table_cell: "표 셀",
    equation_block: "수식",
    formula: "수식",
    list_group: "목록",
    list: "목록",
    code_block: "코드",
    form: "양식",
    table_of_contents: "목차",
    chemical_block: "화학식",
    bibliography: "참고문헌",
    blank_page: "빈 페이지",
    complex_block: "복합 영역",
    handwriting: "필기",
    text_inline_math: "본문 수식",
    reference: "참조",
    line: "줄",
    span: "구간",
    char: "문자",
    document: "문서",
    page: "페이지",
    header: "머리말",
    footer: "꼬리말",
    advertisement: "광고",
  };
  return labels[label] || label;
}

function setSettingsStatus(text, isError = false) {
  if (!els.settingsStatusText) {
    return;
  }
  els.settingsStatusText.textContent = text;
  els.settingsStatusText.classList.toggle("error", isError);
}

async function loadRuntimeSettings() {
  if (!els.runtimeSettingsFields) {
    return;
  }
  setSettingsStatus("설정을 불러오는 중입니다...");
  try {
    const response = await fetch("api/runtime-settings", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "settings load failed");
    }
    renderRuntimeSettings(payload);
    setSettingsStatus(`저장 위치: ${payload.path || "-"}`);
  } catch (error) {
    setSettingsStatus(error.message || "설정을 불러오지 못했습니다.", true);
  }
}

function renderRuntimeSettings(payload) {
  els.runtimeSettingsFields.replaceChildren();
  const groups = new Map();
  (payload.specs || []).forEach((spec) => {
    const group = spec.group || "other";
    if (!groups.has(group)) {
      groups.set(group, []);
    }
    groups.get(group).push(spec);
  });

  groups.forEach((items, group) => {
    const section = document.createElement("section");
    section.className = "runtime-settings-group";
    const title = document.createElement("h3");
    title.textContent = runtimeGroupLabel(group);
    section.appendChild(title);

    items.forEach((spec) => {
      section.appendChild(runtimeSettingField(spec));
    });
    els.runtimeSettingsFields.appendChild(section);
  });
  els.runtimeSettingsFields.dataset.loaded = "true";
}

function runtimeSettingField(spec) {
  const label = document.createElement("label");
  label.className = "runtime-setting-field";
  label.dataset.key = spec.key;

  const head = document.createElement("span");
  head.className = "runtime-setting-label";
  head.textContent = spec.label || spec.key;
  label.appendChild(head);

  const input = spec.choices && spec.choices.length ? document.createElement("select") : document.createElement("input");
  input.name = spec.key;
  input.dataset.valueType = spec.type || "string";
  input.dataset.originalValue = stringifySettingValue(spec.value);
  if (input.tagName === "SELECT") {
    spec.choices.forEach((choice) => {
      const option = document.createElement("option");
      option.value = choice;
      option.textContent = choice;
      input.appendChild(option);
    });
  } else if (spec.type === "bool") {
    input.type = "checkbox";
    input.checked = Boolean(spec.value);
  } else if (spec.type === "int" || spec.type === "float") {
    input.type = "number";
    if (spec.type === "float") {
      input.step = "0.01";
    }
    if (spec.min !== null && spec.min !== undefined) {
      input.min = String(spec.min);
    }
    if (spec.max !== null && spec.max !== undefined) {
      input.max = String(spec.max);
    }
  } else {
    input.type = "text";
  }
  if (spec.type !== "bool") {
    input.value = stringifySettingValue(spec.value);
  }
  label.appendChild(input);

  const detail = document.createElement("small");
  const source = spec.has_override ? "UI 저장값" : "환경변수 기본값";
  const restart = spec.restart_required ? " / 재시작 필요" : " / 새 요청부터 반영";
  detail.textContent = `${spec.description || ""} (${source}${restart})`;
  label.appendChild(detail);
  return label;
}

async function saveRuntimeSettings(event) {
  event.preventDefault();
  const values = {};
  els.runtimeSettingsForm.querySelectorAll("[name]").forEach((input) => {
    const type = input.dataset.valueType || "string";
    if (type === "bool") {
      values[input.name] = input.checked;
    } else if (type === "int") {
      values[input.name] = input.value === "" ? null : Number.parseInt(input.value, 10);
    } else if (type === "float") {
      values[input.name] = input.value === "" ? null : Number.parseFloat(input.value);
    } else {
      values[input.name] = input.value;
    }
  });
  setSettingsStatus("저장 중입니다...");
  try {
    const response = await fetch("api/runtime-settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ values }),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "settings save failed");
    }
    renderRuntimeSettings(payload);
    if (payload.warning) {
      setSettingsStatus(payload.warning, true);
    } else {
      setSettingsStatus("저장했습니다. 즉시 반영 항목은 새 요청부터 적용됩니다.");
    }
    loadResources();
  } catch (error) {
    setSettingsStatus(error.message || "설정 저장에 실패했습니다.", true);
  }
}

function stringifySettingValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

function runtimeGroupLabel(group) {
  const labels = {
    ocr_api: "OCR API",
    chandra: "Army-OCR",
    playground: "Playground",
    national_assembly: "국회 OCR API",
    vllm: "vLLM",
  };
  return labels[group] || group;
}

function setHistoryStatus(text, isError = false) {
  if (!els.historyStatusText) {
    return;
  }
  els.historyStatusText.textContent = text;
  els.historyStatusText.classList.toggle("error", isError);
}

function refreshHistoryAfterConversion() {
  state.historyLoaded = false;
  if (els.historyPane && !els.historyPane.hidden) {
    loadHistory();
  }
}

async function loadHistory() {
  if (!els.historyList) {
    return;
  }
  setHistoryStatus("작업 기록을 불러오는 중입니다...");
  try {
    const response = await fetch("api/history?limit=80", { cache: "no-store" });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || payload.error || "history load failed");
    }
    state.historyItems = Array.isArray(payload.items) ? payload.items : [];
    state.historyLoaded = true;
    renderHistory();
    setHistoryStatus(state.historyItems.length ? `최근 작업 ${state.historyItems.length}개` : "표시할 작업 기록이 없습니다.");
  } catch (error) {
    state.historyLoaded = false;
    state.historyItems = [];
    renderHistory();
    setHistoryStatus(error.message || "작업 기록을 불러오지 못했습니다.", true);
  }
}

function renderHistory() {
  if (!els.historyList) {
    return;
  }
  els.historyList.replaceChildren();
  const query = (els.historySearch?.value || "").trim().toLowerCase();
  const items = state.historyItems.filter((item) => historySearchText(item).includes(query));
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = query ? "검색 결과가 없습니다." : "표시할 작업 기록이 없습니다.";
    els.historyList.appendChild(empty);
    return;
  }
  items.forEach((item) => {
    els.historyList.appendChild(historyRow(item));
  });
}

function historyRow(item) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "history-row";
  row.dataset.historyRequestId = item.request_id || "";
  row.dataset.status = normalizeHistoryStatus(item.status);

  const head = document.createElement("div");
  head.className = "history-row-head";

  const status = document.createElement("span");
  status.className = "history-status";
  status.textContent = historyStatusLabel(item.status);
  head.appendChild(status);

  const title = document.createElement("strong");
  title.textContent = item.file_name || `요청 ${String(item.request_id || "").slice(0, 8)}`;
  head.appendChild(title);

  const time = document.createElement("time");
  time.textContent = formatHistoryTime(item.updated_at || item.created_at);
  head.appendChild(time);
  row.appendChild(head);

  const meta = document.createElement("div");
  meta.className = "history-meta";
  [
    `요청 ${String(item.request_id || "").slice(0, 8)}`,
    historyPageText(item),
    item.mode ? `모드 ${item.mode}` : "",
    historyScoreText(item),
  ]
    .filter(Boolean)
    .forEach((text) => {
      const part = document.createElement("span");
      part.textContent = text;
      meta.appendChild(part);
    });
  row.appendChild(meta);

  if (item.error) {
    const error = document.createElement("small");
    error.className = "history-error";
    error.textContent = item.error;
    row.appendChild(error);
  }
  return row;
}

function historySearchText(item) {
  return [
    item.request_id,
    item.file_name,
    item.status,
    item.mode,
    item.input_source,
    item.error,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function normalizeHistoryStatus(status) {
  const value = String(status || "processing").toLowerCase();
  if (["complete", "completed", "success"].includes(value)) {
    return "complete";
  }
  if (["failed", "error"].includes(value)) {
    return "failed";
  }
  return "processing";
}

function historyStatusLabel(status) {
  const labels = {
    complete: "완료",
    completed: "완료",
    success: "완료",
    failed: "실패",
    error: "실패",
    processing: "처리중",
    queued: "대기",
  };
  const value = String(status || "processing").toLowerCase();
  return labels[value] || value;
}

function historyPageText(item) {
  const total = Number(item.page_count || 0);
  const processed = Number(item.processed_page_count || item.progress?.processed_pages || 0);
  if (total > 0 && processed > 0 && processed < total) {
    return `${processed}/${total}쪽`;
  }
  if (total > 0) {
    return `${total}쪽`;
  }
  if (processed > 0) {
    return `${processed}쪽`;
  }
  return "";
}

function historyScoreText(item) {
  const score = Number(item.parse_quality_score);
  return Number.isFinite(score) ? `점수 ${score}` : "";
}

function formatHistoryTime(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function openHistoryItem(requestId) {
  if (!requestId) {
    return;
  }
  setHistoryStatus(`요청 ${requestId.slice(0, 8)} 결과를 불러오는 중입니다...`);
  setStatus(`작업 기록 ${requestId.slice(0, 8)} 결과를 불러오는 중입니다...`);
  state.currentRequestId = requestId;
  state.currentFileName = "";
  state.autoFollowLatestPage = true;
  clearLinkedBlockSelection();
  try {
    const payload = await fetchHistoryResult(requestId);
    applyConversionPayload(payload, { final: payload.status === "complete" });
    updateProgress(payload, requestId);
    const hasPages = Array.isArray(payload.pages) && payload.pages.length > 0;
    if (payload.status === "failed" || payload.success === false) {
      renderStatusPayload(payload);
      showPane("resultsPane");
      setStatus(payload.error || "실패한 작업 기록입니다.", true);
      setHistoryStatus("실패한 작업 기록을 열었습니다.", true);
      return;
    }
    if (!hasPages && payload.status !== "complete") {
      renderStatusPayload({ ...payload, error: payload.error || "처리 중입니다." });
    }
    showPane("resultsPane");
    if (payload.status === "complete") {
      setStatus(`작업 기록을 열었습니다: ${requestId.slice(0, 8)}`);
      setHistoryStatus("작업 기록을 열었습니다.");
      return;
    }
    const finalPayload = await pollConversion(requestId, payload.result_url || `api/convert/${encodeURIComponent(requestId)}`);
    applyConversionPayload(finalPayload, { final: true });
    updateProgress(finalPayload, requestId);
    setStatus(`완료: ${finalPayload.page_count || 0}쪽, 점수 ${finalPayload.parse_quality_score ?? "n/a"}`);
    refreshHistoryAfterConversion();
  } catch (error) {
    setStatus(error.message || "작업 기록을 열지 못했습니다.", true);
    setHistoryStatus(error.message || "작업 기록을 열지 못했습니다.", true);
  }
}

async function fetchHistoryResult(requestId) {
  const response = await fetch(`api/convert/${encodeURIComponent(requestId)}`, { cache: "no-store" });
  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.detail || payload.error || "history result load failed");
  }
  return payload;
}

function renderStatusPayload(payload) {
  state.result = {
    ...payload,
    pages: [],
    views: {
      json: JSON.stringify(payload, null, 2),
      blocks: payload.error || "",
      html: "",
      markdown: payload.error || "",
      ...(payload.views || {}),
    },
  };
  els.processingStage.hidden = true;
  els.previewStage.hidden = true;
  els.documentToolbar.hidden = true;
  els.thumbStrip.hidden = true;
  els.blocksView.replaceChildren();
  const card = document.createElement("article");
  card.className = "block-card";
  const type = document.createElement("div");
  type.className = "block-type";
  type.textContent = historyStatusLabel(payload.status);
  const text = document.createElement("div");
  text.className = "block-text";
  text.textContent = payload.error || "표시할 페이지 결과가 없습니다.";
  card.append(type, text);
  els.blocksView.appendChild(card);
  els.jsonView.textContent = state.result.views.json || "";
  els.markdownView.textContent = state.result.views.markdown || "";
  renderHtmlView();
  setActiveResult("json");
}

function setProcessStep(activeStep, completedSteps = []) {
  if (!els.processingStage) {
    return;
  }
  els.processingStage.querySelectorAll("[data-process-step]").forEach((item) => {
    const name = item.dataset.processStep;
    item.classList.toggle("active", name === activeStep);
    item.classList.toggle("done", completedSteps.includes(name));
  });
}

function resetProgress() {
  if (!els.progressPanel) {
    return;
  }
  els.progressPanel.hidden = true;
  els.progressTitle.textContent = "문서 읽는 중";
  els.progressLabel.textContent = "0 / ?쪽";
  els.progressDetail.textContent = "전체 쪽수를 확인한 뒤 완료된 페이지부터 바로 표시합니다.";
  els.progressBar.style.width = "0%";
  els.progressTrack.setAttribute("aria-valuenow", "0");
}

function updateProgress(payload = {}, requestId = state.currentRequestId, elapsedMs = 0) {
  if (!els.progressPanel) {
    return;
  }
  const progress = payload.progress || {};
  const pages = Array.isArray(payload.pages) ? payload.pages : [];
  const processed = Number(progress.processed_pages ?? payload.processed_page_count ?? pages.length ?? 0);
  const total = Number(progress.total_pages ?? payload.page_count ?? Math.max(processed, pages.length) ?? 0);
  const percent = Number.isFinite(Number(progress.percent))
    ? Number(progress.percent)
    : total > 0
      ? Math.min(100, Math.round((processed / total) * 1000) / 10)
      : 0;
  const safePercent = Math.max(0, Math.min(100, percent));
  const final = payload.status === "complete";
  const failed = payload.status === "failed" || payload.success === false;
  const elapsedText = elapsedMs > 0 ? ` · ${Math.max(1, Math.round(elapsedMs / 1000))}초 경과` : "";
  els.progressPanel.hidden = false;
  els.progressTitle.textContent = failed ? "읽기 실패" : final ? "읽기 완료" : "읽는 중";
  els.progressLabel.textContent = total > 0 ? `${processed} / ${total}쪽` : `${processed}쪽 완료`;
  els.progressDetail.textContent = final
    ? `총 ${total || processed}쪽 모두 읽었습니다.`
    : total > 0
      ? `총 ${total}쪽 문서 · 완료된 페이지는 바로 표시됩니다.${elapsedText}`
      : `${requestId ? requestId.slice(0, 8) : "요청"} 처리 중${elapsedText}`;
  els.progressBar.style.width = `${safePercent}%`;
  els.progressTrack.setAttribute("aria-valuenow", String(Math.round(safePercent)));
  els.progressPanel.dataset.state = failed ? "failed" : final ? "complete" : "processing";
}

function startProcessingFeedback() {
  if (!els.processingStage) {
    return;
  }
  if (!state.result) {
    els.uploadStage.hidden = true;
    els.processingStage.hidden = false;
  }
  if (els.progressPanel) {
    els.progressPanel.hidden = false;
  }
  clearInterval(state.processTimer);
  const steps = ["upload", "parse", "render"];
  let index = 0;
  setProcessStep(steps[index], []);
  state.processTimer = window.setInterval(() => {
    index = Math.min(index + 1, steps.length - 1);
    setProcessStep(steps[index], steps.slice(0, index));
  }, 1200);
}

function finishProcessingFeedback(success) {
  clearInterval(state.processTimer);
  state.processTimer = null;
  if (!els.processingStage) {
    return;
  }
  if (success) {
    setProcessStep("done", ["upload", "parse", "render"]);
    return;
  }
  setProcessStep("", []);
  if (!state.result) {
    els.processingStage.hidden = true;
  }
}

document.querySelectorAll(".pane-tab").forEach((button) => {
  button.addEventListener("click", () => showPane(button.dataset.pane));
});

document.addEventListener("mouseover", (event) => {
  const target = closestLinkedBlock(event.target);
  if (target) {
    setLinkedBlockHover(target.dataset.blockKey || "");
  }
});

document.addEventListener("mouseout", (event) => {
  const target = closestLinkedBlock(event.target);
  if (!target || (event.relatedTarget && target.contains(event.relatedTarget))) {
    return;
  }
  clearLinkedBlockHover(target.dataset.blockKey || "");
});

document.addEventListener("focusin", (event) => {
  const target = closestLinkedBlock(event.target);
  if (target) {
    setLinkedBlockHover(target.dataset.blockKey || "");
  }
});

document.addEventListener("focusout", (event) => {
  const target = closestLinkedBlock(event.target);
  if (!target || (event.relatedTarget && target.contains(event.relatedTarget))) {
    return;
  }
  clearLinkedBlockHover(target.dataset.blockKey || "");
});

document.addEventListener("click", (event) => {
  const target = closestLinkedBlock(event.target);
  if (target) {
    pinLinkedBlock(target.dataset.blockKey || "", target.dataset.blockRole || "");
  }
});

document.querySelectorAll(".result-tab").forEach((button) => {
  button.addEventListener("click", () => setActiveResult(button.dataset.result));
});

document.querySelectorAll(".mode-card input").forEach((input) => {
  input.addEventListener("change", syncModeCards);
});

els.fileInput.addEventListener("change", () => {
  const files = els.fileInput.files ? Array.from(els.fileInput.files) : [];
  if (files.length) {
    setFiles(files);
  }
});

els.dropZone.addEventListener("dragover", (event) => {
  event.preventDefault();
  els.dropZone.classList.add("dragging");
});

els.dropZone.addEventListener("dragleave", () => {
  els.dropZone.classList.remove("dragging");
});

els.dropZone.addEventListener("drop", (event) => {
  event.preventDefault();
  els.dropZone.classList.remove("dragging");
  const files = event.dataTransfer.files ? Array.from(event.dataTransfer.files) : [];
  if (files.length) {
    setFiles(files);
  }
});

els.useUrlButton.addEventListener("click", () => {
  state.file = null;
  state.files = [];
  state.fileEntries = [];
  state.fileUrl = els.fileUrlInput.value.trim();
  els.fileInput.value = "";
  els.dropZone.querySelector("strong").textContent = "파일을 놓거나 클릭";
  els.dropZone.classList.remove("has-file");
  renderFileList();
  if (els.fileNameLabel) {
    els.fileNameLabel.textContent = state.fileUrl ? "URL 입력됨" : "선택된 파일 없음";
  }
  if (els.fileHelpLabel) {
    els.fileHelpLabel.textContent = state.fileUrl || "파일을 선택하면 여기 표시됩니다.";
  }
  setStatus(state.fileUrl ? "URL이 선택됐습니다." : "URL을 입력하세요.", !state.fileUrl);
});

els.clearFileButton.addEventListener("click", clearInput);
els.configForm.addEventListener("submit", convertDocument);
els.updateSettingsButton.addEventListener("click", convertDocument);
if (els.runtimeSettingsForm) {
  els.runtimeSettingsForm.addEventListener("submit", saveRuntimeSettings);
}
if (els.reloadRuntimeSettings) {
  els.reloadRuntimeSettings.addEventListener("click", loadRuntimeSettings);
}
if (els.reloadHistory) {
  els.reloadHistory.addEventListener("click", () => {
    state.historyLoaded = false;
    loadHistory();
  });
}
if (els.historySearch) {
  els.historySearch.addEventListener("input", renderHistory);
}
if (els.historyList) {
  els.historyList.addEventListener("click", (event) => {
    const row = event.target.closest("[data-history-request-id]");
    if (row) {
      openHistoryItem(row.dataset.historyRequestId || "");
    }
  });
}
if (els.blockEditForm) {
  els.blockEditForm.addEventListener("submit", saveBlockEdit);
}
if (els.closeBlockEdit) {
  els.closeBlockEdit.addEventListener("click", closeBlockEditor);
}
if (els.cancelBlockEdit) {
  els.cancelBlockEdit.addEventListener("click", closeBlockEditor);
}
if (els.blockEditModal) {
  els.blockEditModal.addEventListener("click", (event) => {
    if (event.target.closest("[data-edit-close]")) {
      closeBlockEditor();
    }
  });
}
if (els.editBlockLabel) {
  els.editBlockLabel.addEventListener("change", syncEditDialogMode);
}
if (els.editImageDrop) {
  els.editImageDrop.addEventListener("click", () => els.editImageInput?.click());
  els.editImageDrop.addEventListener("dragover", (event) => {
    event.preventDefault();
    els.editImageDrop.classList.add("dragging");
  });
  els.editImageDrop.addEventListener("dragleave", () => {
    els.editImageDrop.classList.remove("dragging");
  });
  els.editImageDrop.addEventListener("drop", (event) => {
    event.preventDefault();
    els.editImageDrop.classList.remove("dragging");
    const file = imageFileFromTransfer(event.dataTransfer);
    if (file) {
      readEditImageFile(file);
    }
  });
  els.editImageDrop.addEventListener("paste", (event) => {
    const file = imageFileFromTransfer(event.clipboardData);
    if (file) {
      event.preventDefault();
      readEditImageFile(file);
    }
  });
}
if (els.editImageInput) {
  els.editImageInput.addEventListener("change", () => {
    const file = els.editImageInput.files?.[0];
    if (file) {
      readEditImageFile(file);
    }
  });
}
if (els.clearEditImage) {
  els.clearEditImage.addEventListener("click", clearEditImage);
}
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && els.blockEditModal && !els.blockEditModal.hidden) {
    closeBlockEditor();
  }
});
document.addEventListener("paste", (event) => {
  if (event.defaultPrevented || !els.blockEditModal || els.blockEditModal.hidden) {
    return;
  }
  const file = imageFileFromTransfer(event.clipboardData);
  if (file) {
    event.preventDefault();
    readEditImageFile(file);
  }
});
els.renderHtml.addEventListener("change", renderHtmlView);
els.copyButton.addEventListener("click", copyActiveText);
els.downloadButton.addEventListener("click", downloadResult);

els.prevPage.addEventListener("click", () => {
  state.autoFollowLatestPage = false;
  state.pageIndex = Math.max(0, state.pageIndex - 1);
  renderSelectedPage();
});

els.nextPage.addEventListener("click", () => {
  state.autoFollowLatestPage = false;
  state.pageIndex = Math.min((state.result?.pages || []).length - 1, state.pageIndex + 1);
  renderSelectedPage();
});

els.toggleThumbs.addEventListener("click", () => {
  if (!els.thumbStrip.hidden && !state.thumbsCollapsed) {
    const activeThumb = els.thumbStrip.querySelector("button.active");
    activeThumb?.scrollIntoView({ block: "nearest", inline: "center" });
  }
});

syncModeCards();
populateEditLabelOptions();
loadResources();
