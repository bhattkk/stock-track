// Shared API helper for the Stock Track front end (loaded before app.js and
// screener.js / analysis.js). Every call goes to `/api/*`: nginx proxies
// `/api/screener/*` to the screener service, `/api/analysis/*` to the analysis
// service and the rest to the portfolio service.

// The screener runs on demand, so "unreachable" usually just means it's stopped.
const SCREENER_DOWN_MESSAGE =
  "Screener service isn't running. Start it with: docker compose --profile screener up -d screener";

const ANALYSIS_DOWN_MESSAGE =
  "Analysis service is unreachable. Start it with: docker compose up -d analysis";

const API_BASE = "/api";

class ApiError extends Error {
  constructor(title, message) {
    super(message);
    this.title = title;
  }
}

// FastAPI errors are {detail: "..."} or, for validation, {detail: [{msg, loc}, ...]}.
function describeDetail(body) {
  if (!body || body.detail === undefined) return null;
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) {
    return body.detail.map((d) => `${(d.loc || []).slice(1).join(".") || "request"}: ${d.msg}`).join("; ");
  }
  return JSON.stringify(body.detail);
}

async function api(method, path, body) {
  const label = `${method} ${path}`;
  let res;
  try {
    res = await fetch(API_BASE + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    throw new ApiError(`${label} failed`, `Could not reach the server (${e.message}).`);
  }

  if (res.status === 204) return null;

  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    // Non-JSON (e.g. nginx's HTML 502 page when the portfolio container is down).
  }

  if (!res.ok) {
    let message = describeDetail(data);
    if (!message) {
      message = [502, 503, 504].includes(res.status)
        ? path.startsWith("/screener/")
          ? SCREENER_DOWN_MESSAGE
          : path.startsWith("/analysis/")
            ? ANALYSIS_DOWN_MESSAGE
            : "Portfolio service is unreachable. Is the `portfolio` container running?"
        : res.statusText || "Unexpected error";
    }
    throw new ApiError(`${label} failed (HTTP ${res.status})`, message);
  }
  return data;
}

const enc = encodeURIComponent;
