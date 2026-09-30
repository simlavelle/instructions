// Small UI helpers shared by the single-label and batch views.

export const $ = (id) => document.getElementById(id);

export function openZoom(url, name) {
  $("zoom-img").src = url;
  $("zoom-img").alt = name ? `Label image: ${name}` : "Label image";
  $("zoom-title").textContent = name || "Label image";
  $("zoom-dialog").showModal();
}

/** Drag-and-drop plus the hidden file input, both feeding the same handler. */
export function wireDropzone(zone, input, onFiles) {
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  ["dragenter", "dragover"].forEach((t) => zone.addEventListener(t, (e) => { stop(e); zone.classList.add("is-dragging"); }));
  ["dragleave", "drop"].forEach((t) => zone.addEventListener(t, (e) => { stop(e); zone.classList.remove("is-dragging"); }));
  zone.addEventListener("drop", (e) => {
    const files = [...(e.dataTransfer?.files || [])];
    if (files.length) onFiles(files);
  });
  input.addEventListener("change", () => {
    if (input.files.length) onFiles([...input.files]);
    input.value = "";
  });
}
