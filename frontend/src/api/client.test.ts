import { afterEach, describe, expect, it, vi } from "vitest";

import { mockFetch } from "../test/utils";
import { ApiError, buildQuery, getWorkspaceToken, request, setWorkspaceToken } from "./client";
import { shouldRetry } from "./hooks";

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
});

describe("api client", () => {
  it("builds query strings and skips empty values", () => {
    expect(buildQuery({ a: 1, b: "", c: null, d: [1, 2], e: undefined })).toBe("?a=1&d=1%2C2");
    expect(buildQuery({})).toBe("");
  });

  it("parses structured API errors including retry-after", async () => {
    mockFetch({
      "/api/market/X/quote": {
        status: 429,
        body: { error: { code: "provider_rate_limited", message: "Twelve Data per-minute credit limit reached.", retry_after: 12 } },
      },
    });
    const error = (await request("/market/X/quote").catch((e: unknown) => e)) as ApiError;
    expect(error).toBeInstanceOf(ApiError);
    expect(error.code).toBe("provider_rate_limited");
    expect(error.retryAfter).toBe(12);
    expect(shouldRetry(0, error)).toBe(true);
  });

  it("maps network failures to a clear error", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    }));
    const error = (await request("/health").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("network_error");
    expect(error.message).toMatch(/Cannot reach/);
  });

  it("does not retry client errors or configuration problems", () => {
    expect(shouldRetry(0, new ApiError(404, "symbol_not_found", "x"))).toBe(false);
    expect(shouldRetry(0, new ApiError(503, "provider_not_configured", "x"))).toBe(false);
    expect(shouldRetry(0, new ApiError(503, "provider_unavailable", "x"))).toBe(true);
  });

  it("creates a workspace token on demand and recreates it after a 401", async () => {
    let created = 0;
    const fetchMock = mockFetch({
      "/api/workspaces": () => ({ status: 201, body: { token: `tok-${++created}`, created_at: "2024-01-01T00:00:00Z" } }),
      "/api/watchlist": (_url, init) => {
        const token = (init?.headers as Record<string, string>)["X-Workspace-Token"];
        return token === "stale" ? { status: 401, body: { error: { code: "unauthorized", message: "Unknown workspace token." } } } : { body: { items: [], max_items: 50 } };
      },
    });
    setWorkspaceToken("stale");
    const result = await request<{ items: unknown[] }>("/watchlist", { auth: true });
    expect(result.items).toEqual([]);
    expect(getWorkspaceToken()).toBe("tok-1");
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });
});
