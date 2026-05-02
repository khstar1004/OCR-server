const adminState = {
  users: [],
  settingsLoaded: false,
  settingsPayload: null,
  userStatusFilter: "all",
  userQuery: "",
  settingsQuery: "",
  settingsGroupFilter: "all",
  restartOnly: false,
  overrideOnly: false,
};

const adminEls = {
  userPill: document.getElementById("adminUserPill"),
  pendingCount: document.getElementById("pendingCount"),
  activeCount: document.getElementById("activeCount"),
  suspendedCount: document.getElementById("suspendedCount"),
  rejectedCount: document.getElementById("rejectedCount"),
  sessionCount: document.getElementById("sessionCount"),
  restartSettingCount: document.getElementById("restartSettingCount"),
  settingsPath: document.getElementById("settingsPath"),
  serviceState: document.getElementById("serviceState"),
  ocrBackendState: document.getElementById("ocrBackendState"),
  concurrencyState: document.getElementById("concurrencyState"),
  authStorePath: document.getElementById("authStorePath"),
  settingsUpdatedAt: document.getElementById("settingsUpdatedAt"),
  reloadOverview: document.getElementById("reloadOverview"),
  refreshAllButton: document.getElementById("refreshAllButton"),
  commandCopyStatus: document.getElementById("commandCopyStatus"),
  quickPendingCount: document.getElementById("quickPendingCount"),
  quickRestartCount: document.getElementById("quickRestartCount"),
  usersList: document.getElementById("usersList"),
  userSearchInput: document.getElementById("userSearchInput"),
  userStatusFilter: document.getElementById("userStatusFilter"),
  chipPendingCount: document.getElementById("chipPendingCount"),
  chipActiveCount: document.getElementById("chipActiveCount"),
  chipSuspendedCount: document.getElementById("chipSuspendedCount"),
  chipRejectedCount: document.getElementById("chipRejectedCount"),
  reloadUsers: document.getElementById("reloadUsers"),
  reloadSettings: document.getElementById("reloadAdminSettings"),
  settingsForm: document.getElementById("adminSettingsForm"),
  settingsFields: document.getElementById("adminSettingsFields"),
  settingsStatus: document.getElementById("adminSettingsStatus"),
  settingCount: document.getElementById("settingCount"),
  settingOverrideCount: document.getElementById("settingOverrideCount"),
  settingRestartOverrideCount: document.getElementById("settingRestartOverrideCount"),
  settingsSearchInput: document.getElementById("settingsSearchInput"),
  settingsGroupFilter: document.getElementById("settingsGroupFilter"),
  restartOnlyToggle: document.getElementById("restartOnlyToggle"),
  overrideOnlyToggle: document.getElementById("overrideOnlyToggle"),
  logoutButton: document.getElementById("logoutButton"),
};

function setAdminStatus(text, isError = false) {
  adminEls.settingsStatus.textContent = text;
  adminEls.settingsStatus.classList.toggle("error", isError);
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

function stringifySettingValue(value) {
  if (value === null || value === undefined) {
    return "";
  }
  return String(value);
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    cache: "no-store",
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) {
      window.location.href = "login";
      return null;
    }
    throw new Error(payload.detail || "요청에 실패했습니다.");
  }
  return payload;
}

function showAdminPane(id) {
  document.querySelectorAll("[data-admin-pane]").forEach((button) => {
    button.classList.toggle("active", button.dataset.adminPane === id);
  });
  document.querySelectorAll(".admin-pane").forEach((pane) => {
    pane.hidden = pane.id !== id;
  });
  if (id === "usersPane") {
    loadUsers();
  }
  if (id === "settingsPane" && !adminState.settingsLoaded) {
    loadRuntimeSettings();
  }
}

async function refreshAll() {
  setAdminStatus("전체 상태를 새로고침 중입니다...");
  await Promise.all([loadOverview(), loadUsers(), loadRuntimeSettings()]);
  setAdminStatus("새로고침했습니다.");
}

async function loadMe() {
  const payload = await requestJson("api/auth/me");
  if (!payload || !payload.authenticated || payload.user.role !== "admin") {
    window.location.href = "login";
    return;
  }
  adminEls.userPill.textContent = `${payload.user.display_name || payload.user.username} · 관리자`;
}

async function loadOverview() {
  const payload = await requestJson("api/admin/overview");
  if (!payload) {
    return;
  }
  renderOverview(payload);
  if (payload.settings && !adminState.settingsLoaded) {
    renderRuntimeSettings(payload.settings);
  }
}

function renderOverview(payload) {
  const auth = payload.auth || {};
  const runtime = payload.runtime || {};
  const health = payload.health || {};
  const upstreamHealth = health.upstream || {};
  const capabilities = payload.capabilities || {};
  const features = capabilities.features || {};

  renderAccountCounts(auth);
  adminEls.sessionCount.textContent = String(auth.session_count ?? "-");
  adminEls.restartSettingCount.textContent = String(runtime.restart_required_override_count ?? 0);
  adminEls.settingsPath.textContent = runtime.path || "-";
  adminEls.authStorePath.textContent = auth.path || "-";
  adminEls.settingsUpdatedAt.textContent = formatTime(runtime.updated_at) || "-";

  const ready = Boolean(health.ocr_service_ready || upstreamHealth.ocr_service_ready);
  adminEls.serviceState.textContent = ready ? "정상" : (health.status || "확인 필요");
  const backendName = capabilities.ocr_backend || health.ocr_backend || upstreamHealth.ocr_backend || "-";
  adminEls.ocrBackendState.textContent = backendName === "army_ocr" ? "Army-OCR" : backendName;
  adminEls.concurrencyState.textContent = String(
    features.max_concurrent_ocr_requests
      ?? health.max_concurrent_ocr_requests
      ?? upstreamHealth.max_concurrent_ocr_requests
      ?? "-",
  );
}

function renderAccountCounts(summary = {}) {
  const counts = summary.status_counts || {};
  const pending = summary.pending_count ?? counts.pending ?? 0;
  const active = summary.active_count ?? counts.active ?? 0;
  const suspended = summary.suspended_count ?? counts.suspended ?? 0;
  const rejected = summary.rejected_count ?? counts.rejected ?? 0;
  adminEls.pendingCount.textContent = String(pending);
  adminEls.activeCount.textContent = String(active);
  adminEls.suspendedCount.textContent = String(suspended);
  adminEls.rejectedCount.textContent = String(rejected);
  adminEls.quickPendingCount.textContent = String(pending);
  adminEls.chipPendingCount.textContent = String(pending);
  adminEls.chipActiveCount.textContent = String(active);
  adminEls.chipSuspendedCount.textContent = String(suspended);
  adminEls.chipRejectedCount.textContent = String(rejected);
}

async function loadUsers() {
  const payload = await requestJson("api/admin/users");
  if (!payload) {
    return;
  }
  adminState.users = payload.users || [];
  renderAccountCounts(payload.summary || countUsers(adminState.users));
  renderUsers();
}

function countUsers(users) {
  const counts = { pending: 0, active: 0, suspended: 0, rejected: 0 };
  users.forEach((user) => {
    const key = user.status || "pending";
    counts[key] = (counts[key] || 0) + 1;
  });
  return {
    pending_count: counts.pending || 0,
    active_count: counts.active || 0,
    suspended_count: counts.suspended || 0,
    rejected_count: counts.rejected || 0,
    status_counts: counts,
  };
}

function filteredUsers() {
  const query = adminState.userQuery.trim().toLowerCase();
  return adminState.users.filter((user) => {
    if (adminState.userStatusFilter !== "all" && user.status !== adminState.userStatusFilter) {
      return false;
    }
    if (!query) {
      return true;
    }
    return [user.username, user.display_name, user.email, user.reason, user.role, statusLabel(user.status)]
      .join(" ")
      .toLowerCase()
      .includes(query);
  });
}

function renderUsers() {
  adminEls.usersList.replaceChildren();
  const users = filteredUsers();
  if (!users.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "조건에 맞는 계정이 없습니다.";
    adminEls.usersList.appendChild(empty);
    return;
  }

  users.forEach((user) => {
    adminEls.usersList.appendChild(userRow(user));
  });
}

function setUserStatusFilter(value) {
  adminState.userStatusFilter = value;
  adminEls.userStatusFilter.value = value;
  document.querySelectorAll("[data-user-status-chip]").forEach((button) => {
    button.classList.toggle("active", button.dataset.userStatusChip === value);
  });
  renderUsers();
}

function userRow(user) {
  const row = document.createElement("article");
  row.className = "admin-user-row";
  row.dataset.status = user.status || "pending";

  const main = document.createElement("div");
  main.className = "admin-user-main";
  const title = document.createElement("strong");
  title.textContent = `${user.display_name || user.username} (${user.username})`;

  const meta = document.createElement("span");
  meta.textContent = [
    user.email,
    roleLabel(user.role),
    `신청 ${formatTime(user.created_at) || "-"}`,
    user.last_login_at ? `최근 로그인 ${formatTime(user.last_login_at)}` : "",
  ].filter(Boolean).join(" · ");

  const badge = document.createElement("span");
  badge.className = `admin-status-badge ${user.status || "pending"}`;
  badge.textContent = statusLabel(user.status);

  const titleLine = document.createElement("div");
  titleLine.className = "admin-user-title-line";
  titleLine.append(title, badge);
  main.append(titleLine, meta);

  if (user.reason) {
    const reason = document.createElement("p");
    reason.textContent = user.reason;
    main.appendChild(reason);
  }
  row.appendChild(main);

  const actions = document.createElement("div");
  actions.className = "admin-user-actions";
  if (user.status === "pending") {
    actions.appendChild(userActionButton("승인", () => updateUser(user.id, "approve"), true));
    actions.appendChild(userActionButton("반려", () => updateUser(user.id, "reject", "이 계정 신청을 반려할까요?")));
  } else if (user.status === "active" && user.role !== "admin") {
    actions.appendChild(userActionButton("정지", () => updateUser(user.id, "suspend", "이 계정을 정지하고 로그인 세션을 끊을까요?")));
  } else if (user.role !== "admin" && (user.status === "suspended" || user.status === "rejected")) {
    actions.appendChild(userActionButton("재활성화", () => updateUser(user.id, "activate"), true));
  }
  if (!actions.children.length) {
    const locked = document.createElement("span");
    locked.className = "admin-action-note";
    locked.textContent = user.role === "admin" ? "관리자 보호" : "처리 완료";
    actions.appendChild(locked);
  }
  row.appendChild(actions);
  return row;
}

function userActionButton(label, handler, primary = false) {
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = label;
  if (primary) {
    button.className = "primary";
  }
  button.addEventListener("click", handler);
  return button;
}

async function updateUser(userId, action, confirmation = "") {
  if (confirmation && !window.confirm(confirmation)) {
    return;
  }
  await requestJson(`api/admin/users/${encodeURIComponent(userId)}/${action}`, { method: "POST" });
  await Promise.all([loadUsers(), loadOverview()]);
}

function renderRuntimeSettings(payload) {
  adminState.settingsPayload = payload;
  adminEls.settingsFields.replaceChildren();
  updateSettingsSummary(payload);
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
    section.dataset.group = group;
    const title = document.createElement("h3");
    title.textContent = `${runtimeGroupLabel(group)} · ${items.length}`;
    section.appendChild(title);
    items.forEach((spec) => {
      section.appendChild(runtimeSettingField(spec));
    });
    adminEls.settingsFields.appendChild(section);
  });
  adminState.settingsLoaded = true;
  applySettingsFilter();
}

function updateSettingsSummary(payload) {
  const specs = payload.specs || [];
  const overrides = payload.overrides || {};
  const restartOverrides = specs.filter((spec) => spec.restart_required && spec.has_override);
  adminEls.settingsPath.textContent = payload.path || "-";
  adminEls.settingCount.textContent = String(specs.length);
  adminEls.settingOverrideCount.textContent = String(Object.keys(overrides).length);
  adminEls.settingRestartOverrideCount.textContent = String(restartOverrides.length);
  adminEls.restartSettingCount.textContent = String(restartOverrides.length);
  adminEls.quickRestartCount.textContent = String(restartOverrides.length);
  adminEls.settingsUpdatedAt.textContent = formatTime(payload.updated_at) || "-";
}

function runtimeSettingField(spec) {
  const label = document.createElement("label");
  label.className = "runtime-setting-field admin-runtime-field";
  label.dataset.key = spec.key;
  label.dataset.group = spec.group || "";
  label.dataset.restartRequired = spec.restart_required ? "true" : "false";
  label.dataset.hasOverride = spec.has_override ? "true" : "false";
  label.dataset.search = [
    spec.key,
    spec.env,
    spec.label,
    spec.group,
    spec.description,
    runtimeGroupLabel(spec.group),
  ].join(" ").toLowerCase();

  const head = document.createElement("span");
  head.className = "runtime-setting-label";
  head.textContent = spec.label || spec.key;
  label.appendChild(head);

  const input = spec.choices && spec.choices.length ? document.createElement("select") : document.createElement("input");
  input.name = spec.key;
  input.dataset.valueType = spec.type || "string";
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
    input.step = spec.type === "float" ? "0.01" : "1";
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

  const badges = document.createElement("span");
  badges.className = "runtime-setting-badges";
  badges.appendChild(settingBadge(spec.has_override ? "저장값" : "환경값", spec.has_override ? "override" : ""));
  if (spec.restart_required) {
    badges.appendChild(settingBadge("재시작 필요", "restart"));
  } else {
    badges.appendChild(settingBadge("새 요청 반영", "live"));
  }
  label.appendChild(badges);

  const detail = document.createElement("small");
  detail.textContent = `${spec.description || ""} · ${spec.env || spec.key}`;
  label.appendChild(detail);
  return label;
}

function settingBadge(text, type) {
  const badge = document.createElement("span");
  badge.className = `setting-badge ${type}`;
  badge.textContent = text;
  return badge;
}

function applySettingsFilter() {
  const query = adminState.settingsQuery.trim().toLowerCase();
  const restartOnly = adminState.restartOnly;
  const overrideOnly = adminState.overrideOnly;
  const groupFilter = adminState.settingsGroupFilter;
  document.querySelectorAll(".runtime-settings-group").forEach((section) => {
    let visibleCount = 0;
    section.querySelectorAll(".runtime-setting-field").forEach((field) => {
      const matchesQuery = !query || (field.dataset.search || "").includes(query);
      const matchesRestart = !restartOnly || field.dataset.restartRequired === "true";
      const matchesOverride = !overrideOnly || field.dataset.hasOverride === "true";
      const matchesGroup = groupFilter === "all" || field.dataset.group === groupFilter;
      const visible = matchesQuery && matchesRestart && matchesOverride && matchesGroup;
      field.hidden = !visible;
      if (visible) {
        visibleCount += 1;
      }
    });
    section.hidden = visibleCount === 0;
  });
}

function setSettingsGroupFilter(value) {
  adminState.settingsGroupFilter = value;
  adminEls.settingsGroupFilter.value = value;
  applySettingsFilter();
}

async function loadRuntimeSettings() {
  setAdminStatus("설정을 불러오는 중입니다...");
  try {
    const payload = await requestJson("api/admin/runtime-settings");
    if (!payload) {
      return;
    }
    renderRuntimeSettings(payload);
    setAdminStatus(`저장 위치: ${payload.path || payload.proxy_path || "-"}`);
  } catch (error) {
    setAdminStatus(error.message || "설정을 불러오지 못했습니다.", true);
  }
}

async function saveRuntimeSettings(event) {
  event.preventDefault();
  const values = {};
  adminEls.settingsForm.querySelectorAll("[name]").forEach((input) => {
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
  setAdminStatus("저장 중입니다...");
  try {
    const payload = await requestJson("api/admin/runtime-settings", {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ values }),
    });
    if (!payload) {
      return;
    }
    renderRuntimeSettings(payload);
    await loadOverview();
    setAdminStatus(payload.warning || "저장했습니다. 재시작 필요 항목은 vLLM 재시작 후 적용됩니다.", Boolean(payload.warning));
  } catch (error) {
    setAdminStatus(error.message || "설정 저장에 실패했습니다.", true);
  }
}

function applySettingsPreset(name) {
  const presets = {
    stable: {
      ocr_service_timeout_sec: 300,
      ocr_max_concurrent_requests: 1,
      playground_default_max_pages: 0,
      playground_max_upload_mb: 512,
      vllm_max_num_seqs: 1,
      vllm_gpu_memory_utilization: 0.8,
    },
    preview: {
      ocr_service_timeout_sec: 300,
      playground_default_max_pages: 0,
      playground_max_upload_mb: 1024,
      target_api_timeout_sec: 60,
    },
    assembly: {
      ocr_service_timeout_sec: 300,
      ocr_service_poll_interval_sec: 2,
      target_api_timeout_sec: 60,
      callback_timeout_seconds: 60,
      watch_poll_interval_sec: 1,
      watch_stable_scan_count: 2,
    },
  };
  const values = presets[name];
  if (!values) {
    return;
  }
  Object.entries(values).forEach(([key, value]) => setSettingInputValue(key, value));
  setAdminStatus("프리셋 값을 채웠습니다. 반영하려면 설정 저장을 누르세요.");
}

function setSettingInputValue(key, value) {
  const input = adminEls.settingsForm.querySelector(`[name="${key}"]`);
  if (!input) {
    return;
  }
  if (input.dataset.valueType === "bool") {
    input.checked = Boolean(value);
  } else {
    input.value = stringifySettingValue(value);
  }
  input.closest(".runtime-setting-field")?.classList.add("dirty");
}

function markSettingsDirty(event) {
  event.target.closest(".runtime-setting-field")?.classList.add("dirty");
  setAdminStatus("저장하지 않은 변경이 있습니다.");
}

async function logout() {
  await fetch("api/auth/logout", { method: "POST" });
  window.location.href = "login";
}

function statusLabel(status) {
  return {
    active: "활성",
    pending: "승인 대기",
    suspended: "정지",
    rejected: "반려",
  }[status] || status || "-";
}

function roleLabel(role) {
  return role === "admin" ? "관리자" : "일반 사용자";
}

function formatTime(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("ko-KR", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

async function copyCommand(button) {
  const command = button.dataset.command || "";
  if (!command) {
    return;
  }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(command);
    } else {
      const textarea = document.createElement("textarea");
      textarea.value = command;
      textarea.style.position = "fixed";
      textarea.style.left = "-9999px";
      document.body.appendChild(textarea);
      textarea.focus();
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    adminEls.commandCopyStatus.textContent = "명령을 복사했습니다.";
  } catch (error) {
    adminEls.commandCopyStatus.textContent = command;
  }
}

document.querySelectorAll("[data-admin-pane]").forEach((button) => {
  button.addEventListener("click", () => showAdminPane(button.dataset.adminPane));
});
document.querySelectorAll(".copy-command").forEach((button) => {
  button.addEventListener("click", () => copyCommand(button));
});
document.querySelectorAll("[data-quick-pane]").forEach((button) => {
  button.addEventListener("click", () => {
    showAdminPane(button.dataset.quickPane);
    if (button.dataset.quickUserStatus) {
      setUserStatusFilter(button.dataset.quickUserStatus);
    }
    if (button.dataset.quickSettingsGroup) {
      setSettingsGroupFilter(button.dataset.quickSettingsGroup);
    }
    if (button.dataset.quickRestartOnly) {
      adminState.restartOnly = true;
      adminEls.restartOnlyToggle.checked = true;
      applySettingsFilter();
    }
  });
});
document.querySelectorAll("[data-user-status-chip]").forEach((button) => {
  button.addEventListener("click", () => setUserStatusFilter(button.dataset.userStatusChip));
});
document.querySelectorAll("[data-settings-preset]").forEach((button) => {
  button.addEventListener("click", () => applySettingsPreset(button.dataset.settingsPreset));
});
adminEls.refreshAllButton.addEventListener("click", refreshAll);
adminEls.reloadOverview.addEventListener("click", loadOverview);
adminEls.reloadUsers.addEventListener("click", loadUsers);
adminEls.reloadSettings.addEventListener("click", loadRuntimeSettings);
adminEls.settingsForm.addEventListener("submit", saveRuntimeSettings);
adminEls.settingsForm.addEventListener("input", markSettingsDirty);
adminEls.settingsForm.addEventListener("change", markSettingsDirty);
adminEls.userSearchInput.addEventListener("input", (event) => {
  adminState.userQuery = event.target.value;
  renderUsers();
});
adminEls.userStatusFilter.addEventListener("change", (event) => {
  setUserStatusFilter(event.target.value);
});
adminEls.settingsSearchInput.addEventListener("input", (event) => {
  adminState.settingsQuery = event.target.value;
  applySettingsFilter();
});
adminEls.settingsGroupFilter.addEventListener("change", (event) => {
  setSettingsGroupFilter(event.target.value);
});
adminEls.restartOnlyToggle.addEventListener("change", (event) => {
  adminState.restartOnly = event.target.checked;
  applySettingsFilter();
});
adminEls.overrideOnlyToggle.addEventListener("change", (event) => {
  adminState.overrideOnly = event.target.checked;
  applySettingsFilter();
});
adminEls.logoutButton.addEventListener("click", logout);

loadMe();
loadOverview();
loadUsers();
