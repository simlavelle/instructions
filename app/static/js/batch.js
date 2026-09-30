// Batch checking: many label images + one CSV of application data.
//
// The browser sends labels to the same /api/verify endpoint a few at a time.
// This keeps the server stateless (nothing stored between requests) and shows
// results as they arrive. The trade-off: closing the tab stops the batch.

import { ApiError, fetchFile, verifyLabel } from "./api.js";
import { readApplications, toCsv } from "./csv.js";
import { badge, h, problemSummary, renderResult, VERDICT_TEXT } from "./render.js";
import { $, openZoom, wireDropzone } from "./ui.js";
const CONCURRENCY = 4;
const ORDER = { fail: 0, review: 1, error: 2, pass: 3 };

const state = {
  images: new Map(), // lower-cased file name -> File
  applications: null, // parsed CSV rows
  csvName: "",
  items: [], // {file, name, application, result?, error?, url?}
  filter: "all",
  controller: null,
  running: false,
};

function key(name) {
  return name.trim().toLowerCase();
}

function addImages(files) {
  let skipped = 0;
  for (const f of files) {
    if (f.type.startsWith("image/")) state.images.set(key(f.name), f);
    else skipped += 1;
  }
  const n = state.images.size;
  $("batch-images-summary").replaceChildren(
    h("strong", {}, `${n} image${n === 1 ? "" : "s"} chosen.`),
    skipped ? ` ${skipped} file${skipped === 1 ? " was" : "s were"} not an image and will be ignored.` : "",
    n ? " " : "",
    n ? h("button", { type: "button", class: "btn btn--link", onclick: clearImages }, "Clear") : "");
  hideError();
}

function clearImages() {
  state.images.clear();
  $("batch-images-summary").textContent = "No images chosen yet.";
}

async function setCsv(file) {
  try {
    state.applications = readApplications(await file.text());
    state.csvName = file.name;
    const n = state.applications.length;
    $("batch-csv-summary").replaceChildren(h("strong", {}, file.name), ` — ${n} application${n === 1 ? "" : "s"}.`);
    hideError();
  } catch (err) {
    state.applications = null;
    $("batch-csv-summary").textContent = "No spreadsheet chosen yet.";
    showError(`That spreadsheet can't be used: ${err.message}`);
  }
}

function showError(message) {
  $("batch-error").textContent = message;
  $("batch-error").hidden = false;
}

function hideError() {
  $("batch-error").hidden = true;
}

function buildItems() {
  const items = [];
  const byName = new Map((state.applications || []).map((a) => [key(a.filename || ""), a]));
  for (const [name, file] of state.images) {
    const application = byName.get(name);
    if (!application) items.push({ file, name: file.name, error: "No row for this image in the spreadsheet." });
    else if (!application.brand_name) items.push({ file, name: file.name, application, error: `Row ${application._row} has no brand name.` });
    else items.push({ file, name: file.name, application });
  }
  for (const [name, application] of byName) {
    if (name && !state.images.has(name)) {
      items.push({ name: application.filename, application, error: "The spreadsheet lists this file but the image wasn't uploaded." });
    }
  }
  return items;
}

async function start() {
  if (state.running) return;
  if (!state.images.size) return showError("Please choose the label images first.");
  if (!state.applications) return showError("Please choose the spreadsheet of application data.");
  hideError();

  state.items = buildItems();
  const queue = state.items.filter((i) => !i.error);
  if (!queue.length) return showError("None of the images match a row in the spreadsheet. Check the filename column.");

  state.running = true;
  state.filter = "all";
  state.controller = new AbortController();
  $("batch-start").disabled = true;
  $("batch-example").disabled = true;
  $("batch-progress").hidden = false;
  $("batch-results").hidden = false;
  render();

  const total = queue.length;
  let done = 0;
  const started = performance.now();
  const updateProgress = () => {
    const pct = Math.round((done / total) * 100);
    $("batch-progress-bar").style.width = `${pct}%`;
    let text = `Checked ${done} of ${total} label${total === 1 ? "" : "s"}`;
    if (done > 0 && done < total) {
      const perLabel = (performance.now() - started) / done;
      const left = Math.max(1, Math.round((perLabel * (total - done)) / 1000));
      text += ` · about ${left < 60 ? `${left} seconds` : `${Math.round(left / 60)} minute${left >= 90 ? "s" : ""}`} left`;
    }
    $("batch-progress-text").textContent = text;
  };
  updateProgress();

  const application = (item) => {
    const { _row, filename, ...fields } = item.application;
    return Object.fromEntries(Object.entries(fields).filter(([, v]) => v));
  };

  const worker = async () => {
    while (queue.length && !state.controller.signal.aborted) {
      const item = queue.shift();
      try {
        item.result = await verifyLabel(item.file, application(item), { signal: state.controller.signal });
      } catch (err) {
        if (err.name === "AbortError") { queue.unshift(item); break; }
        item.error = err instanceof ApiError ? err.message : "Unexpected error.";
      }
      done += 1;
      updateProgress();
      scheduleRender();
    }
  };
  await Promise.all(Array.from({ length: CONCURRENCY }, worker));

  for (const item of queue) item.error = "Not checked: the batch was stopped.";
  state.running = false;
  $("batch-start").disabled = false;
  $("batch-example").disabled = false;
  $("batch-progress-text").textContent = state.controller.signal.aborted
    ? `Stopped after ${done} of ${total} labels.`
    : `Finished: checked ${total} label${total === 1 ? "" : "s"} in ${Math.round((performance.now() - started) / 1000)} seconds.`;
  $("batch-stop").hidden = true;
  render();
  $("batch-results-title").focus?.();
}

let renderQueued = false;
function scheduleRender() {
  if (renderQueued) return;
  renderQueued = true;
  requestAnimationFrame(() => {
    renderQueued = false;
    render();
  });
}

function verdictOf(item) {
  if (item.result) return item.result.verdict;
  if (item.error) return "error";
  return null;
}

function render() {
  const finished = state.items.filter(verdictOf);
  const counts = { all: finished.length, fail: 0, review: 0, pass: 0, error: 0 };
  finished.forEach((i) => { counts[verdictOf(i)] += 1; });

  const tile = (id, label, extra = "") => h("button", {
    type: "button", class: `tile ${extra}`, "aria-pressed": String(state.filter === id),
    onclick: () => { state.filter = id; render(); },
  }, h("span", { class: "tile__count" }, counts[id]), h("span", { class: "tile__label" }, label));
  $("batch-tiles").replaceChildren(
    tile("all", "All checked"),
    tile("fail", VERDICT_TEXT.fail, "tile--fail"),
    tile("review", VERDICT_TEXT.review, "tile--review"),
    tile("pass", VERDICT_TEXT.pass, "tile--pass"),
    ...(counts.error ? [tile("error", VERDICT_TEXT.error)] : []));

  const rows = finished
    .filter((i) => state.filter === "all" || verdictOf(i) === state.filter)
    .sort((a, b) => ORDER[verdictOf(a)] - ORDER[verdictOf(b)] || a.name.localeCompare(b.name));

  $("batch-rows").replaceChildren(...rows.map((item) => {
    const v = verdictOf(item);
    const issues = item.result ? problemSummary(item.result) : [item.error];
    return h("tr", {},
      h("td", {}, badge(v, VERDICT_TEXT[v])),
      h("td", {}, h("div", { class: "file" }, item.name), item.application?.brand_name ? h("div", { class: "brand" }, item.application.brand_name) : null),
      h("td", {}, issues.length ? h("ul", { class: "issues" }, issues.map((t) => h("li", {}, t))) : h("span", { class: "muted" }, "Nothing to flag")),
      h("td", {}, item.result
        ? h("button", { type: "button", class: "btn btn--secondary", onclick: () => showDetail(item) }, "View details")
        : null));
  }));
  if (!rows.length) {
    $("batch-rows").replaceChildren(h("tr", {}, h("td", { colspan: "4", class: "muted" }, state.running ? "Results will appear here as each label is checked." : "No labels in this group.")));
  }
}

function showDetail(item) {
  item.url ??= URL.createObjectURL(item.file);
  $("detail-title").textContent = item.name;
  $("detail-body").replaceChildren(renderResult(item.result, { imageUrl: item.url, fileName: item.name, onZoom: openZoom }));
  $("detail-dialog").showModal();
}

function download() {
  const header = ["filename", "result", "summary", "brand_name", "class_type", "alcohol_content", "net_contents",
    "bottler", "country_of_origin", "government_warning", "details"];
  const rows = [header];
  const sorted = [...state.items].filter(verdictOf).sort((a, b) => ORDER[verdictOf(a)] - ORDER[verdictOf(b)]);
  for (const item of sorted) {
    const r = item.result;
    const status = (k) => r?.checks.find((c) => c.key === k)?.status ?? "";
    rows.push([
      item.name,
      VERDICT_TEXT[verdictOf(item)],
      r ? r.headline : item.error,
      ...header.slice(3, 10).map(status),
      r ? problemSummary(r).join(" | ") : "",
    ]);
  }
  const blob = new Blob([toCsv(rows)], { type: "text/csv" });
  const a = h("a", { href: URL.createObjectURL(blob), download: `label-check-results-${new Date().toISOString().slice(0, 10)}.csv` });
  document.body.append(a);
  a.click();
  a.remove();
}

async function runExample() {
  $("batch-example").disabled = true;
  try {
    const samples = await (await fetch("/static/samples/samples.json")).json();
    const csv = await fetchFile("/static/samples/batch-example.csv", "batch-example.csv");
    clearImages();
    addImages(await Promise.all(samples.map((s) => fetchFile(`/static/samples/${s.file}`, s.file))));
    await setCsv(csv);
  } catch (err) {
    showError(`Couldn't load the example batch: ${err.message}`);
    $("batch-example").disabled = false;
    return;
  }
  $("batch-example").disabled = false;
  start();
}

export function initBatch() {
  wireDropzone($("batch-dropzone"), $("batch-images"), addImages);
  $("batch-csv").addEventListener("change", (e) => {
    if (e.target.files[0]) setCsv(e.target.files[0]);
    e.target.value = "";
  });
  $("batch-start").addEventListener("click", () => {
    $("batch-stop").hidden = false;
    start();
  });
  $("batch-example").addEventListener("click", () => {
    $("batch-stop").hidden = false;
    runExample();
  });
  $("batch-stop").addEventListener("click", () => state.controller?.abort());
  $("batch-download").addEventListener("click", download);
}
