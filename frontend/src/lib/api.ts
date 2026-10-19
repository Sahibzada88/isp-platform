const BASE = "/api/v1";

export interface ApiErrorBody { code?: string; message?: string; details?: unknown }

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  constructor(status: number, body: ApiErrorBody | undefined) {
    super(body?.message || `Request failed (${status})`);
    this.status = status;
    this.code = body?.code || "error";
    this.details = body?.details;
  }
  /** DRF field errors as {field: first message}. */
  get fieldErrors(): Record<string, string> {
    return fieldErrorsFrom(this.details);
  }
}

export function fieldErrorsFrom(details: unknown): Record<string, string> {
  const out: Record<string, string> = {};
  if (details && typeof details === "object" && !Array.isArray(details)) {
    for (const [k, v] of Object.entries(details as Record<string, unknown>)) {
      if (Array.isArray(v) && typeof v[0] === "string") out[k] = v[0];
      else if (typeof v === "string") out[k] = v;
    }
  }
  return out;
}

export function buildQuery(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue;
    q.set(k, String(v));
  }
  const s = q.toString();
  return s ? `?${s}` : "";
}

export function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;
  const m = document.cookie.split("; ").find((c) => c.startsWith(`${name}=`));
  return m ? decodeURIComponent(m.split("=")[1]) : null;
}

export async function ensureCsrf(): Promise<string> {
  const existing = getCookie("csrftoken");
  if (existing) return existing;
  const res = await fetch(`${BASE}/auth/csrf/`, { credentials: "same-origin" });
  const data = await res.json();
  return data.csrfToken as string;
}

type Query = Record<string, string | number | boolean | undefined | null>;
interface Options { method?: string; body?: unknown; query?: Query; signal?: AbortSignal }

const SESSION_EXPIRED = "isp:session-expired";

export async function api<T = unknown>(path: string, opts: Options = {}): Promise<T> {
  const method = (opts.method || "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
  if (method !== "GET" && method !== "HEAD") {
    headers["X-CSRFToken"] = await ensureCsrf();
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(`${BASE}${path}${buildQuery(opts.query)}`, {
    method,
    headers,
    credentials: "same-origin",
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
    signal: opts.signal,
  });
  if (res.status === 204) return undefined as T;
  let data: unknown;
  try {
    data = await res.json();
  } catch {
    data = undefined;
  }
  if (!res.ok) {
    const body = (data as { error?: ApiErrorBody } | undefined)?.error;
    const err = new ApiError(res.status, body);
    if (err.code === "not_authenticated" && typeof window !== "undefined") {
      window.dispatchEvent(new Event(SESSION_EXPIRED));
    }
    throw err;
  }
  return data as T;
}

export { SESSION_EXPIRED };
export const pdfUrl = (path: string) => `${BASE}${path}`;
