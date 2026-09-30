// Shared rendering for a verification result (used by the single and batch views).
// Everything is built with textContent, never innerHTML, because label text is untrusted.

export function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === false || value == null) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else el.setAttribute(key, value === true ? "" : value);
  }
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

const SVG_NS = "http://www.w3.org/2000/svg";
const ICON_PATHS = {
  match: ["M5 12.5l4.5 4.5L19 7.5"],
  pass: ["M5 12.5l4.5 4.5L19 7.5"],
  review: ["M12 7v6", "M12 16.5v.5"],
  mismatch: ["M7 7l10 10", "M17 7L7 17"],
  fail: ["M7 7l10 10", "M17 7L7 17"],
  missing: ["M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6", "M12 17v.5"],
  skipped: ["M7 12h10"],
  error: ["M7 12h10"],
};

export function icon(name, { circle = true } = {}) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  if (circle) {
    const c = document.createElementNS(SVG_NS, "circle");
    Object.entries({ cx: 12, cy: 12, r: 10.5, fill: "none", stroke: "currentColor", "stroke-width": 2 }).forEach(([k, v]) => c.setAttribute(k, v));
    svg.append(c);
  }
  for (const d of ICON_PATHS[name] || ICON_PATHS.skipped) {
    const p = document.createElementNS(SVG_NS, "path");
    Object.entries({ d, fill: "none", stroke: "currentColor", "stroke-width": 2.5, "stroke-linecap": "round", "stroke-linejoin": "round" }).forEach(([k, v]) => p.setAttribute(k, v));
    svg.append(p);
  }
  return svg;
}

export const STATUS_TEXT = {
  match: "Matches",
  review: "Check this",
  mismatch: "Doesn't match",
  missing: "Not found",
  skipped: "Not checked",
};

export const VERDICT_TEXT = {
  pass: "Looks good",
  review: "Needs review",
  fail: "Problems found",
  error: "Couldn't check",
};

export function badge(status, text = STATUS_TEXT[status] || VERDICT_TEXT[status]) {
  return h("span", { class: `badge badge--${status}` }, icon(status, { circle: false }), text);
}

function seconds(ms) {
  const s = ms / 1000;
  return s < 10 ? `${s.toFixed(1)} seconds` : `${Math.round(s)} seconds`;
}

function renderDiff(tokens) {
  const text = h("p", { class: "diff__text" });
  for (const t of tokens) {
    if (t.op === "equal") text.append(`${t.found} `);
    else if (t.op === "missing") text.append(h("del", { title: "Missing from the label" }, t.expected), " ");
    else if (t.op === "extra") text.append(h("ins", { title: "Not in the required text" }, t.found), " ");
    else text.append(h("del", { title: "Required wording" }, t.expected), " ", h("ins", { title: "What the label says" }, t.found), " ");
  }
  return h("div", { class: "diff" },
    text,
    h("p", { class: "diff__legend" },
      h("span", {}, h("del", {}, "Struck out"), " = required wording that is missing or different"),
      h("span", {}, h("ins", {}, "Underlined"), " = what the label says instead")));
}

function renderCheck(check) {
  // When there are sub-checks, they already say what's wrong; the summary would repeat them.
  const showMessage = !check.sub_checks?.length || check.status === "missing";
  const body = h("div", { class: "check__body" },
    h("h3", { class: "check__name" }, check.name),
    showMessage ? h("p", { class: "check__msg" }, check.message) : null);

  const isWarning = check.key === "government_warning";
  if (!isWarning && (check.expected || check.found)) {
    body.append(h("dl", { class: "compare" },
      h("dt", {}, "Application"), h("dd", {}, check.expected ? h("span", { class: "value" }, check.expected) : "—"),
      h("dt", {}, "Label"), h("dd", {}, check.found ? h("span", { class: "value" }, check.found) : h("em", {}, "not found"))));
  }
  if (check.sub_checks?.length) {
    body.append(h("ul", { class: "subchecks" }, check.sub_checks.map((s) =>
      h("li", { class: `is-${s.status}` }, icon(s.status), h("span", {}, h("strong", {}, `${s.name}: `), s.message)))));
  }
  if (isWarning && check.diff?.length && check.diff.some((t) => t.op !== "equal")) {
    body.append(renderDiff(check.diff));
  }
  return h("li", { class: `check check--${check.status}` }, badge(check.status), body);
}

/**
 * @param {object} result  VerificationResult from the API
 * @param {{imageUrl?: string, fileName?: string, onZoom?: Function, actions?: Node[]}} opts
 */
export function renderResult(result, { imageUrl, fileName, onZoom, actions = [] } = {}) {
  const verdict = h("div", { class: `verdict verdict--${result.verdict}`, tabindex: "-1", role: "status" },
    decorate(icon(result.verdict), "verdict__icon"),
    h("div", {},
      h("span", { class: "verdict__label" }, VERDICT_TEXT[result.verdict]),
      h("p", { class: "verdict__headline" }, result.headline),
      h("p", { class: "verdict__meta" }, `Checked in ${seconds(result.elapsed_ms)}.`)));

  const notes = [...(result.notes || [])];
  const issues = result.image_quality?.issues || [];
  const alerts = [];
  if (notes.length || issues.length) {
    alerts.push(h("div", { class: "alert alert--info" },
      h("h3", {}, "About this check"),
      h("ul", {}, [...notes, ...issues].map((n) => h("li", {}, n)))));
  }

  const figure = imageUrl
    ? h("figure", { class: "label-figure" },
        h("button", { type: "button", onclick: () => onZoom?.(imageUrl, fileName), "aria-label": "Enlarge label image" },
          h("img", { src: imageUrl, alt: `Label image${fileName ? `: ${fileName}` : ""}` })),
        h("figcaption", {}, "Click the image to enlarge it."))
    : null;

  const transcript = result.transcript?.length
    ? h("details", { class: "result__more" },
        h("summary", {}, "Show all the text the tool read on the label"),
        h("div", { class: "transcript" }, result.transcript.join("\n")))
    : null;

  return h("section", { class: "result", "aria-label": "Check results" },
    verdict,
    alerts,
    h("div", { class: "result__grid" },
      h("div", {}, h("ol", { class: "checklist" }, result.checks.map(renderCheck)), transcript),
      figure),
    actions.length ? h("div", { class: "result__actions" }, actions) : null);
}

function decorate(el, className) {
  el.classList.add(className);
  return el;
}

export function renderError(title, message, actions = []) {
  return h("div", { class: "alert", role: "alert" },
    h("h3", {}, title),
    h("p", {}, message),
    actions.length ? h("div", { class: "result__actions" }, actions) : null);
}

export function renderLoading(title, detail) {
  const timer = h("span", {}, detail);
  const el = h("div", { class: "loading" }, h("div", { class: "spinner", "aria-hidden": "true" }),
    h("div", { class: "loading__text" }, h("strong", {}, title), timer));
  return { el, timer };
}

/** What needs attention, one line per item, for the batch table and CSV. */
export function problemSummary(result) {
  const lines = [];
  for (const c of result.checks) {
    if (c.status === "match" || c.status === "skipped") continue;
    const subs = (c.sub_checks || []).filter((s) => s.status !== "match");
    if (subs.length && c.status !== "missing") subs.forEach((s) => lines.push(`${c.name}: ${s.message}`));
    else lines.push(`${c.name}: ${c.message}`);
  }
  return lines;
}
