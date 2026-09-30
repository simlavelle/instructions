// Page setup: tabs, the single-label check, examples, dialogs.

import { ApiError, fetchFile, verifyLabel } from "./api.js";
import { initBatch } from "./batch.js";
import { h, renderError, renderLoading, renderResult } from "./render.js";
import { $, openZoom, wireDropzone } from "./ui.js";

// ------------------------------------------------------------------ tabs

function initTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab) => {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", on);
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).hidden = !on;
    }
    tab.focus();
  };
  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab));
    tab.addEventListener("keydown", (e) => {
      if (e.key === "ArrowRight") select(tabs[(i + 1) % tabs.length]);
      if (e.key === "ArrowLeft") select(tabs[(i - 1 + tabs.length) % tabs.length]);
    });
  });
}

// ------------------------------------------------------------------ dialogs

function initDialogs() {
  for (const dialog of document.querySelectorAll("dialog")) {
    dialog.addEventListener("click", (e) => {
      if (e.target === dialog || e.target.closest("[data-close]")) dialog.close();
    });
  }
}

// ------------------------------------------------------------------ single label

const FIELDS = {
  brand_name: "f-brand",
  class_type: "f-class",
  alcohol_content: "f-abv",
  net_contents: "f-net",
  bottler: "f-bottler",
  country_of_origin: "f-country",
};

const single = { file: null, url: null, controller: null };

function setImage(file) {
  if (!file.type.startsWith("image/")) {
    showFieldError("image-error", "That file isn't an image. Please choose a JPG or PNG of the label.");
    return;
  }
  if (single.url) URL.revokeObjectURL(single.url);
  single.file = file;
  single.url = URL.createObjectURL(file);
  $("preview-img").src = single.url;
  $("preview-img").alt = `Selected label image: ${file.name}`;
  $("preview-name").textContent = file.name;
  $("dropzone").dataset.state = "filled";
  $("dropzone").querySelector(".dropzone__empty").hidden = true;
  $("dropzone").querySelector(".dropzone__preview").hidden = false;
  hideFieldError("image-error");
}

function clearImage() {
  if (single.url) URL.revokeObjectURL(single.url);
  Object.assign(single, { file: null, url: null });
  $("dropzone").dataset.state = "empty";
  $("dropzone").querySelector(".dropzone__empty").hidden = false;
  $("dropzone").querySelector(".dropzone__preview").hidden = true;
}

function showFieldError(id, message) {
  const el = $(id);
  el.textContent = message;
  el.hidden = false;
}

function hideFieldError(id) {
  $(id).hidden = true;
}

function readApplication() {
  const app = {};
  for (const [key, id] of Object.entries(FIELDS)) {
    const value = $(id).value.trim();
    if (value) app[key] = value;
  }
  if (!$("f-imported").checked) delete app.country_of_origin;
  return app;
}

function fillApplication(app) {
  for (const [key, id] of Object.entries(FIELDS)) $(id).value = app[key] || "";
  $("f-imported").checked = Boolean(app.country_of_origin);
  $("country-field").hidden = !app.country_of_origin;
}

async function runSingleCheck() {
  const application = readApplication();
  let valid = true;
  if (!single.file) {
    showFieldError("image-error", "Please add the label image first.");
    valid = false;
  }
  if (!application.brand_name) {
    showFieldError("brand-error", "Please enter the brand name from the application.");
    $("f-brand").setAttribute("aria-invalid", "true");
    if (single.file) $("f-brand").focus();
    valid = false;
  }
  if (!valid) {
    if (!single.file) $("dropzone").scrollIntoView({ behavior: "smooth", block: "center" });
    return;
  }

  single.controller?.abort();
  single.controller = new AbortController();
  const button = $("check-btn");
  button.disabled = true;
  button.textContent = "Checking…";
  $("single-result").replaceChildren();
  const { el, timer } = renderLoading("Reading the label…", "This usually takes a few seconds.");
  $("single-status").replaceChildren(el);
  const started = performance.now();
  const tick = setInterval(() => {
    const s = Math.round((performance.now() - started) / 1000);
    if (s >= 3) timer.textContent = `This usually takes a few seconds. (${s} s so far)`;
  }, 1000);

  try {
    const result = await verifyLabel(single.file, application, { signal: single.controller.signal });
    const again = h("button", { type: "button", class: "btn btn--primary", onclick: resetSingle }, "Check another label");
    const print = h("button", { type: "button", class: "btn btn--secondary", onclick: () => window.print() }, "Print these results");
    const view = renderResult(result, { imageUrl: single.url, fileName: single.file.name, onZoom: openZoom, actions: [again, print] });
    $("single-status").replaceChildren();
    $("single-result").replaceChildren(view);
    const verdict = view.querySelector(".verdict");
    verdict.scrollIntoView({ behavior: "smooth", block: "start" });
    verdict.focus({ preventScroll: true });
  } catch (err) {
    if (err.name === "AbortError") return;
    const retry = h("button", { type: "button", class: "btn btn--primary", onclick: runSingleCheck }, "Try again");
    const message = err instanceof ApiError ? err.message : "Something unexpected went wrong. Please try again.";
    $("single-status").replaceChildren(renderError("The label couldn't be checked", message, [retry]));
  } finally {
    clearInterval(tick);
    button.disabled = false;
    button.textContent = "Check label";
  }
}

function resetSingle() {
  $("single-form").reset();
  $("country-field").hidden = true;
  clearImage();
  $("single-result").replaceChildren();
  $("single-status").replaceChildren();
  window.scrollTo({ top: 0, behavior: "smooth" });
  $("dropzone").querySelector("label").focus();
}

async function loadExamples() {
  let samples = [];
  try {
    samples = await (await fetch("/static/samples/samples.json")).json();
  } catch {
    $("examples").hidden = true;
    return;
  }
  $("examples-list").replaceChildren(...samples.map((s) =>
    h("li", {},
      h("button", { type: "button", class: "example", onclick: () => useExample(s) },
        h("img", { src: `/static/samples/${s.file}`, alt: "", loading: "lazy" }),
        h("span", {}, h("span", { class: "example__title" }, s.title), h("span", { class: "example__desc" }, s.description))))));
}

async function useExample(sample) {
  try {
    setImage(await fetchFile(`/static/samples/${sample.file}`, sample.file));
  } catch (err) {
    $("single-status").replaceChildren(renderError("Couldn't load the example", err.message));
    return;
  }
  fillApplication(sample.application);
  hideFieldError("brand-error");
  $("examples").open = false;
  runSingleCheck();
}

function initSingle() {
  wireDropzone($("dropzone"), $("image-input"), (files) => setImage(files[0]));
  document.addEventListener("paste", (e) => {
    if ($("panel-single").hidden) return;
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith("image/"));
    if (item) setImage(new File([item.getAsFile()], "pasted-label.png", { type: item.type }));
  });
  $("f-imported").addEventListener("change", (e) => {
    $("country-field").hidden = !e.target.checked;
    if (e.target.checked) $("f-country").focus();
  });
  $("f-brand").addEventListener("input", () => {
    hideFieldError("brand-error");
    $("f-brand").removeAttribute("aria-invalid");
  });
  $("single-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runSingleCheck();
  });
  $("clear-btn").addEventListener("click", resetSingle);
  loadExamples();
}

// ------------------------------------------------------------------ start

initTabs();
initDialogs();
initSingle();
initBatch();
