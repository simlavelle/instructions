// Thin client for the verification API.

export class ApiError extends Error {}

function detailMessage(data) {
  if (!data) return null;
  if (typeof data.detail === "string") return data.detail;
  if (Array.isArray(data.detail)) return data.detail.map((d) => d.msg).join("; ");
  return null;
}

/**
 * Check one label image against application data.
 * @returns {Promise<object>} VerificationResult
 */
export async function verifyLabel(file, application, { signal } = {}) {
  const body = new FormData();
  body.append("image", file, file.name || "label.jpg");
  body.append("application", JSON.stringify(application));

  let response;
  try {
    response = await fetch("/api/verify", { method: "POST", body, signal });
  } catch (err) {
    if (err.name === "AbortError") throw err;
    throw new ApiError("Couldn't reach the server. Check your connection and try again.");
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    /* non-JSON error page */
  }
  if (!response.ok) {
    throw new ApiError(detailMessage(data) || `The server had a problem (error ${response.status}). Please try again.`);
  }
  return data;
}

export async function fetchFile(url, name) {
  const response = await fetch(url);
  if (!response.ok) throw new ApiError(`Couldn't load ${name}.`);
  const blob = await response.blob();
  return new File([blob], name, { type: blob.type });
}
