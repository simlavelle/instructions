// Minimal RFC 4180 CSV reading/writing (quoted fields, embedded commas/newlines, BOM).

export function parseCsv(text) {
  const rows = [];
  let row = [];
  let field = "";
  let quoted = false;
  text = text.replace(/^﻿/, "");
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(field); rows.push(row); row = []; field = "";
    } else field += c;
  }
  if (field !== "" || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((v) => v.trim() !== ""));
}

// Header spellings people are likely to use, mapped to API field names.
const HEADER_ALIASES = {
  filename: ["filename", "file", "file_name", "image", "image_file", "label", "label_file"],
  brand_name: ["brand_name", "brand"],
  class_type: ["class_type", "class", "type", "class/type", "class_/_type"],
  alcohol_content: ["alcohol_content", "alcohol", "abv", "alcohol_%"],
  net_contents: ["net_contents", "net", "volume", "net_content"],
  bottler: ["bottler", "producer", "name_and_address", "bottler_name_address", "bottler_/_producer"],
  country_of_origin: ["country_of_origin", "country", "origin"],
};

function canonicalHeader(raw) {
  const key = raw.trim().toLowerCase().replace(/\s+/g, "_");
  for (const [name, aliases] of Object.entries(HEADER_ALIASES)) {
    if (aliases.includes(key)) return name;
  }
  return null;
}

/**
 * Parse an application-data CSV into objects keyed by API field names.
 * Throws an Error with a user-facing message when the file can't be used.
 */
export function readApplications(text) {
  const rows = parseCsv(text);
  if (rows.length < 2) throw new Error("The spreadsheet has no data rows.");
  const headers = rows[0].map(canonicalHeader);
  if (!headers.includes("filename")) throw new Error("The spreadsheet needs a “filename” column.");
  if (!headers.includes("brand_name")) throw new Error("The spreadsheet needs a “brand_name” column.");
  return rows.slice(1).map((cells, i) => {
    const record = { _row: i + 2 };
    headers.forEach((name, col) => {
      if (name) record[name] = (cells[col] || "").trim();
    });
    return record;
  });
}

export function toCsv(rows) {
  const escape = (v) => {
    const s = v == null ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return rows.map((r) => r.map(escape).join(",")).join("\r\n") + "\r\n";
}
