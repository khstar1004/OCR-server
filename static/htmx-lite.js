(function () {
  function buildUrl(url, formData) {
    const params = new URLSearchParams();
    for (const [key, value] of formData.entries()) {
      params.append(key, value);
    }
    const separator = url.includes("?") ? "&" : "?";
    return params.toString() ? url + separator + params.toString() : url;
  }

  function resolveTarget(element) {
    const selector = element.getAttribute("hx-target");
    if (!selector) {
      return element;
    }
    return document.querySelector(selector);
  }

  function swapContent(target, html, swapMode) {
    if (!target) {
      return;
    }
    if (swapMode === "outerHTML") {
      target.outerHTML = html;
      return;
    }
    target.innerHTML = html;
  }

  async function request(element, method, url, body) {
    const target = resolveTarget(element);
    const swapMode = element.getAttribute("hx-swap") || "innerHTML";
    const response = await fetch(url, {
      method: method,
      body: body,
      headers: {
        "HX-Request": "true"
      }
    });
    const html = await response.text();
    swapContent(target, html, swapMode);
    if (element.getAttribute("hx-push-url") === "true") {
      window.history.pushState({}, "", url);
    }
  }

  document.addEventListener("click", function (event) {
    const trigger = event.target.closest("[hx-get]");
    if (!trigger) {
      return;
    }
    event.preventDefault();
    request(trigger, "GET", trigger.getAttribute("hx-get"), undefined).catch(console.error);
  });

  document.addEventListener("submit", function (event) {
    const form = event.target.closest("form[hx-post], form[hx-get]");
    if (!form) {
      return;
    }
    event.preventDefault();
    const formData = new FormData(form);
    if (form.hasAttribute("hx-get")) {
      request(form, "GET", buildUrl(form.getAttribute("hx-get"), formData), undefined).catch(console.error);
      return;
    }
    request(form, "POST", form.getAttribute("hx-post"), formData).catch(console.error);
  });
})();
