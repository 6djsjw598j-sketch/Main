import type { ApiErrorBody } from "./types";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryAfter: number | null;
  readonly details: Record<string, unknown>;

  constructor(status: number, code: string, message: string, retryAfter: number | null = null, details: Record<string, unknown> = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
    this.details = details;
  }
}

export type QueryValue = string | number | boolean | null | undefined | (string | number)[];

export function buildQuery(params: Record<string, QueryValue> = {}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, Array.isArray(value) ? value.join(",") : String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

const WORKSPACE_KEY = "marketlens.workspace";

export function getWorkspaceToken(): string | null {
  try {
    return localStorage.getItem(WORKSPACE_KEY);
  } catch {
    return null;
  }
}

export function setWorkspaceToken(token: string | null): void {
  try {
    if (token) localStorage.setItem(WORKSPACE_KEY, token);
    else localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    /* storage unavailable (private mode) - the session still works without persistence */
  }
}

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "DELETE";
  body?: unknown;
  params?: Record<string, QueryValue>;
  auth?: boolean;
  signal?: AbortSignal;
}

async function parseError(response: Response): Promise<ApiError> {
  let body: Partial<ApiErrorBody> | null = null;
  try {
    body = (await response.json()) as Partial<ApiErrorBody>;
  } catch {
    body = null;
  }
  const err = body?.error;
  const header = Number(response.headers.get("Retry-After"));
  const retryAfter = err?.retry_after ?? (Number.isFinite(header) && header > 0 ? header : null);
  if (err) return new ApiError(response.status, err.code, err.message, retryAfter, err.details ?? {});
  if (response.status === 502 || response.status === 504) {
    return new ApiError(response.status, "backend_unreachable", "The MarketLens API is not reachable. Is the backend running?");
  }
  return new ApiError(response.status, "http_error", `Request failed with HTTP ${response.status}.`, retryAfter);
}

async function ensureWorkspace(): Promise<string> {
  const existing = getWorkspaceToken();
  if (existing) return existing;
  const created = await request<{ token: string }>("/workspaces", { method: "POST" });
  setWorkspaceToken(created.token);
  return created.token;
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, params, auth = false, signal } = options;
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) headers["X-Workspace-Token"] = await ensureWorkspace();
  let response: Response;
  try {
    response = await fetch(`/api${path}${buildQuery(params)}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    throw new ApiError(0, "network_error", "Cannot reach the MarketLens API. Check your connection or that the backend is running.");
  }
  if (response.status === 401 && auth) {
    // The stored workspace token is unknown to the server (e.g. database reset): start a new one once.
    setWorkspaceToken(null);
    headers["X-Workspace-Token"] = await ensureWorkspace();
    response = await fetch(`/api${path}${buildQuery(params)}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const apiGet = <T>(path: string, params?: Record<string, QueryValue>, signal?: AbortSignal) =>
  request<T>(path, { params, signal });
