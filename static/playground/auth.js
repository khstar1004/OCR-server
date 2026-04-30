const statusText = document.getElementById("authStatusText");
const loginForm = document.getElementById("loginPane");
const signupForm = document.getElementById("signupPane");

function setAuthStatus(text, isError = false) {
  statusText.textContent = text;
  statusText.classList.toggle("error", isError);
}

function formPayload(form) {
  return Object.fromEntries(new FormData(form).entries());
}

document.querySelectorAll("[data-auth-pane]").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("[data-auth-pane]").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    loginForm.hidden = button.dataset.authPane !== "loginPane";
    signupForm.hidden = button.dataset.authPane !== "signupPane";
    setAuthStatus("");
  });
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
      throw new Error(payload.detail || "로그인에 실패했습니다.");
    }
    setAuthStatus("로그인했습니다.");
    window.location.href = "admin";
  } catch (error) {
    setAuthStatus(error.message || "로그인에 실패했습니다.", true);
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
