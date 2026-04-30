const adminState = {
  users: [],
  settingsLoaded: false,
};

const adminEls = {
  userPill: document.getElementById("adminUserPill"),
  pendingCount: document.getElementById("pendingCount"),
  activeCount: document.getElementById("activeCount"),
  settingsPath: document.getElementById("settingsPath"),
  usersList: document.getElementById("usersList"),
  reloadUsers: document.getElementById("reloadUsers"),
  reloadSettings: document.getElementById("reloadAdminSettings"),
  settingsForm: document.getElementById("adminSettingsForm"),
  settingsFields: document.getElementById("adminSettingsFields"),
  settingsStatus: document.getElementById("adminSettingsStatus"),
  logoutButton: document.getElementById("logoutButton"),
};

function setAdminStatus(text, isError = false) {
  adminEls.settingsStatus.textContent = text;
  adminEls.settingsStatus.classList.toggle("error", isError);
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

async function loadMe() {
  const payload = await requestJson("api/auth/me");
  if (!payload || !payload.authenticated || payload.user.role !== "admin") {
    window.location.href = "login";
    return;
  }
  adminEls.userPill.textContent = `${payload.user.display_name || payload.user.username} · 관리자`;
}

async function loadUsers() {
  const payload = await requestJson("api/admin/users");
  if (!payload) {
    return;
  }
  adminState.users = payload.users || [];
  renderUsers();
}

function renderUsers() {
  adminEls.usersList.replaceChildren();
  const pending = adminState.users.filter((user) => user.status === "pending").length;
  const active = adminState.users.filter((user) => user.status === "active").length;
  adminEls.pendingCount.textContent = String(pending);
  adminEls.activeCount.textContent = String(active);

  if (!adminState.users.length) {
    const empty = document.createElement("p");
    empty.className = "history-empty";
    empty.textContent = "표시할 계정이 없습니다.";
    adminEls.usersList.appendChild(empty);
    return;
  }

  adminState.users.forEach((user) => {
    adminEls.usersList.appendChild(userRow(user));
  });
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
  meta.textContent = [user.email, user.role, statusLabel(user.status), formatTime(user.created_at)]
    .filter(Boolean)
    .join(" · ");
  main.append(title, meta);
  if (user.reason) {
    const reason = document.createElement("p");
    reason.textContent = user.reason;
    main.appendChild(reason);
  }
  row.appendChild(main);

  const actions = document.createElement("div");
  actions.className = "admin-user-actions";
  if (user.status === "pending") {
    const approve = document.createElement("button");
    approve.type = "button";
    approve.textContent = "승인";
    approve.className = "primary";
    approve.addEventListener("click", () => updateUser(user.id, "approve"));
    actions.appendChild(approve);
  }
  if (user.role !== "admin" && user.status !== "rejected") {
    const reject = document.createElement("button");
    reject.type = "button";
    reject.textContent = "반려";
    reject.addEventListener("click", () => updateUser(user.id, "reject"));
    actions.appendChild(reject);
  }
  row.appendChild(actions);
  return row;
}

async function updateUser(userId, action) {
  await requestJson(`api/admin/users/${encodeURIComponent(userId)}/${action}`, { method: "POST" });
  await loadUsers();
}

function renderRuntimeSettings(payload) {
  adminEls.settingsFields.replaceChildren();
  adminEls.settingsPath.textContent = payload.path || "-";
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
    adminEls.settingsFields.appendChild(section);
  });
  adminState.settingsLoaded = true;
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

  const detail = document.createElement("small");
  const source = spec.has_override ? "관리자 저장값" : "환경변수 기본값";
  const restart = spec.restart_required ? " / vLLM 재시작 필요" : " / 새 요청부터 반영";
  detail.textContent = `${spec.description || ""} (${source}${restart})`;
  label.appendChild(detail);
  return label;
}

async function loadRuntimeSettings() {
  setAdminStatus("설정을 불러오는 중입니다...");
  try {
    const payload = await requestJson("api/admin/runtime-settings");
    if (!payload) {
      return;
    }
    renderRuntimeSettings(payload);
    setAdminStatus(`저장 위치: ${payload.path || "-"}`);
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
    setAdminStatus(payload.warning || "저장했습니다. 재시작 필요 항목은 vLLM 재시작 후 적용됩니다.", Boolean(payload.warning));
  } catch (error) {
    setAdminStatus(error.message || "설정 저장에 실패했습니다.", true);
  }
}

async function logout() {
  await fetch("api/auth/logout", { method: "POST" });
  window.location.href = "login";
}

function statusLabel(status) {
  return {
    active: "활성",
    pending: "승인 대기",
    rejected: "반려",
  }[status] || status || "-";
}

function formatTime(value) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleString("ko-KR", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

document.querySelectorAll("[data-admin-pane]").forEach((button) => {
  button.addEventListener("click", () => showAdminPane(button.dataset.adminPane));
});
adminEls.reloadUsers.addEventListener("click", loadUsers);
adminEls.reloadSettings.addEventListener("click", loadRuntimeSettings);
adminEls.settingsForm.addEventListener("submit", saveRuntimeSettings);
adminEls.logoutButton.addEventListener("click", logout);

loadMe();
loadUsers();
loadRuntimeSettings();
