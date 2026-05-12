const statusText = document.getElementById("authStatusText");
const loginForm = document.getElementById("loginPane");
const signupForm = document.getElementById("signupPane");
const changePasswordForm = document.getElementById("changePasswordPane");

function setAuthStatus(text, isError = false) {
  statusText.textContent = text;
  statusText.classList.toggle("error", isError);
}

function formPayload(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function showAuthPane(id) {
  document.querySelectorAll("[data-auth-pane]").forEach((item) => {
    item.classList.toggle("active", item.dataset.authPane === id);
  });
  loginForm.hidden = id !== "loginPane";
  signupForm.hidden = id !== "signupPane";
  changePasswordForm.hidden = id !== "changePasswordPane";
  setAuthStatus("");
}

document.querySelectorAll("[data-auth-pane]").forEach((button) => {
  button.addEventListener("click", () => showAuthPane(button.dataset.authPane));
});

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setAuthStatus("로그인 중입니다...");
  try {
    const response = await fetch("api/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(formPayload(loginForm)),
    });
    const payload = await response.json();
    if (!response.ok) {
      if (String(payload.detail || "").includes("비밀번호 변경")) {
        changePasswordForm.elements.username.value = loginForm.elements.username.value;
        showAuthPane("changePasswordPane");
      }
      throw new Error(payload.detail || "로그인에 실패했습니다.");
    }
    setAuthStatus("로그인했습니다.");
    window.location.href = payload.user?.role === "admin" ? "admin" : "./";
  } catch (error) {
    setAuthStatus(error.message || "로그인에 실패했습니다.", true);
  }
});

changePasswordForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = formPayload(changePasswordForm);
  if (payload.new_password !== payload.confirm_password) {
    setAuthStatus("새 비밀번호 확인이 맞지 않습니다.", true);
    return;
  }
  setAuthStatus("비밀번호를 변경하는 중입니다...");
  try {
    const response = await fetch("api/auth/change-password", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) {
      throw new Error(result.detail || "비밀번호 변경에 실패했습니다.");
    }
    const username = payload.username;
    changePasswordForm.reset();
    loginForm.elements.username.value = username;
    showAuthPane("loginPane");
    setAuthStatus("비밀번호를 변경했습니다. 새 비밀번호로 로그인하세요.");
  } catch (error) {
    setAuthStatus(error.message || "비밀번호 변경에 실패했습니다.", true);
  }
});

signupForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  setAuthStatus("계정 신청을 등록하는 중입니다...");
  try {
    const response = await fetch("api/auth/signup", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(formPayload(signupForm)),
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || "계정 신청에 실패했습니다.");
    }
    signupForm.reset();
    setAuthStatus("계정 신청이 등록됐습니다. 관리자 승인 후 로그인할 수 있습니다.");
  } catch (error) {
    setAuthStatus(error.message || "계정 신청에 실패했습니다.", true);
  }
});
