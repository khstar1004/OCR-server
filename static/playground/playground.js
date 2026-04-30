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
  historyLoaded: false,
  historyItems: [],
};

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
      return payload;
    }
    if (payload.status === "failed" || payload.success === false) {
      throw new Error(payload.error || "OCR conversion failed");
    }
    const seconds = Math.max(1, Math.round((Date.now() - startedAt) / 1000));
    const filePrefix = state.currentFileName ? `${state.currentFileName} · ` : "";
    setStatus(`${filePrefix}처리 중입니다... 요청 ${requestId.slice(0, 8)} / 약 ${seconds}초 경과`);
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
      html: `<!doctype html><html><head><meta charset="utf-8"><title>army-ocr Batch Result</title></head><body>${html.join("")}</body></html>`,
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

function applyConversionPayload(payload, { final = false } = {}) {
  const previousPage = currentPage();
  const previousPageNumber = previousPage ? previousPage.page_number : null;
  state.result = payload;
  const pages = Array.isArray(payload.pages) ? payload.pages : [];
  if (pages.length > 0) {
    const samePageIndex = previousPageNumber == null
      ? -1
      : pages.findIndex((page) => page.page_number === previousPageNumber);
    state.pageIndex = samePageIndex >= 0
      ? samePageIndex
      : Math.min(state.pageIndex, pages.length - 1);
    renderResult({ final });
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
  if (pages.length === 0) {
    return;
  }
  els.jsonView.textContent = state.result.views.json || "";
  els.markdownView.textContent = state.result.views.markdown || "";
  const hasBatchDownloads = Array.isArray(state.result.batch_downloads) && state.result.batch_downloads.length > 0;
  els.downloadButton.disabled = !state.result.download_url && !hasBatchDownloads;
  els.downloadButton.textContent = hasBatchDownloads ? "파일별 ZIP" : "이미지 포함 ZIP";
  renderBlocks();
  renderHtmlView();
  renderPage();
  renderThumbs();
  setActiveResult(state.activeResult);
}

function renderBlocks() {
  const result = state.result;
  els.blocksView.replaceChildren();
  (result.pages || []).forEach((page) => {
    const pageHeader = document.createElement("div");
    pageHeader.className = "block-card";
    const source = page.source_file ? `${escapeHtml(page.source_file)} · ` : "";
    pageHeader.innerHTML = `<div class="block-type">${source}${escapeHtml(page.page_number)}쪽</div><div class="block-text">${escapeHtml(page.width)} x ${escapeHtml(page.height)}</div>`;
    els.blocksView.appendChild(pageHeader);

    (page.blocks || []).forEach((block, index) => {
      const label = String(block.label || "text").toLowerCase();
      const card = document.createElement("article");
      card.className = `block-card ${label}`;
      const type = document.createElement("div");
      type.className = "block-type";
      type.textContent = `${blockLabelText(label)} ${index + 1}`;
      card.appendChild(type);

      const asset = findBlockAsset(page, block);
      if (asset && asset.url) {
        const image = document.createElement("img");
        image.className = "block-image-preview";
        image.src = asset.url;
        image.alt = asset.alt || blockLabelText(label);
        card.appendChild(image);
      }

      const text = document.createElement("div");
      text.className = "block-text";
      text.textContent = block.text || `위치=${JSON.stringify(block.bbox || [])}`;
      card.appendChild(text);
      els.blocksView.appendChild(card);
    });
  });
}

function renderHtmlView() {
  if (!state.result) {
    return;
  }
  els.htmlView.replaceChildren();
  const html = state.result.views.html || "";
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
  els.pageLabel.textContent = page.source_file ? `${page.source_file} · ${page.page_number}쪽` : `${page.page_number}쪽`;
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
  (page.blocks || []).forEach((block) => {
    const bbox = Array.isArray(block.bbox) ? block.bbox : null;
    if (!bbox || bbox.length !== 4) {
      return;
    }
    const [x0, y0, x1, y1] = bbox.map(Number);
    if (!(x1 > x0 && y1 > y0)) {
      return;
    }
    const label = String(block.label || "text").toLowerCase();
    const box = document.createElement("div");
    box.className = `bbox ${label}`;
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
  (state.result.pages || []).forEach((page, index) => {
    const button = document.createElement("button");
    button.type = "button";
    button.classList.toggle("active", index === state.pageIndex);
    button.title = `${page.page_number}쪽`;
    const image = document.createElement("img");
    image.src = page.image_url || "";
    image.alt = `${page.page_number}쪽`;
    button.appendChild(image);
    button.addEventListener("click", () => {
      state.pageIndex = index;
      renderPage();
    });
    els.thumbStrip.appendChild(button);
  });
}

function updateThumbSelection() {
  els.thumbStrip.querySelectorAll("button").forEach((button, index) => {
    button.classList.toggle("active", index === state.pageIndex);
  });
}

function currentPage() {
  if (!state.result || !Array.isArray(state.result.pages)) {
    return null;
  }
  return state.result.pages[state.pageIndex] || null;
}

function findBlockAsset(page, block) {
  const blockId = String(block.block_id || "");
  return (page.assets || []).find((asset) => asset.kind === "crop" && asset.block_id === blockId);
}

function activeText() {
  if (!state.result) {
    return "";
  }
  if (state.activeResult === "json") {
    return state.result.views.json || "";
  }
  if (state.activeResult === "html") {
    return state.result.views.html || "";
  }
  if (state.activeResult === "markdown") {
    return state.result.views.markdown || "";
  }
  return state.result.views.blocks || "";
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
    table: "표",
    formula: "수식",
    list: "목록",
    page: "페이지",
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
    chandra: "Chandra OCR",
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
  els.progressLabel.textContent = "0 / 0쪽";
  els.progressDetail.textContent = "완료된 페이지부터 바로 표시합니다.";
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
    ? "모든 페이지를 읽었습니다."
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
els.renderHtml.addEventListener("change", renderHtmlView);
els.copyButton.addEventListener("click", copyActiveText);
els.downloadButton.addEventListener("click", downloadResult);

els.prevPage.addEventListener("click", () => {
  state.pageIndex = Math.max(0, state.pageIndex - 1);
  renderPage();
});

els.nextPage.addEventListener("click", () => {
  state.pageIndex = Math.min((state.result?.pages || []).length - 1, state.pageIndex + 1);
  renderPage();
});

els.toggleThumbs.addEventListener("click", () => {
  els.thumbStrip.hidden = !els.thumbStrip.hidden;
  els.toggleThumbs.textContent = els.thumbStrip.hidden ? "작은 페이지 보기" : "작은 페이지 숨기기";
});

syncModeCards();
loadResources();
