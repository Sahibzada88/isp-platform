import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { ApiError, api, buildQuery, fieldErrorsFrom } from "./api";
import { formatDateTime, formatPKR, humanize } from "./format";
import { hasPermission } from "./permissions";
import { fitWithin } from "./image";
import type { Me } from "./types";

const me = (over: Partial<Me> = {}): Me => ({
  id: 1, username: "u", first_name: "", last_name: "", role: "office_staff", is_owner: false,
  must_change_password: false, offices: ["AAA"],
  permissions: { AAA: ["customers.view", "billing.view"], BBB: ["packages.view"], "*": [] }, ...over,
});

describe("buildQuery", () => {
  it("skips empty values and encodes the rest", () => {
    expect(buildQuery({ search: "ali khan", status: "", page: 2, x: undefined, y: null })).toBe("?search=ali+khan&page=2");
    expect(buildQuery({})).toBe("");
    expect(buildQuery()).toBe("");
  });
});

describe("fieldErrorsFrom", () => {
  it("maps DRF field errors to the first message", () => {
    expect(fieldErrorsFrom({ phone: ["Required."], cnic: "Bad", n: 3, list: [] })).toEqual({ phone: "Required.", cnic: "Bad" });
    expect(fieldErrorsFrom(null)).toEqual({});
    expect(fieldErrorsFrom(["x"])).toEqual({});
  });
  it("ApiError exposes them", () => {
    const e = new ApiError(400, { code: "validation", message: "Validation failed.", details: { full_name: ["This field is required."] } });
    expect(e.fieldErrors.full_name).toBe("This field is required.");
    expect(e.message).toBe("Validation failed.");
  });
});

describe("hasPermission", () => {
  it("owner can do everything", () => expect(hasPermission(me({ is_owner: true }), "refunds.approve")).toBe(true));
  it("is office-aware", () => {
    expect(hasPermission(me(), "customers.view")).toBe(true);
    expect(hasPermission(me(), "customers.view", "AAA")).toBe(true);
    expect(hasPermission(me(), "customers.view", "BBB")).toBe(false);
    expect(hasPermission(me(), "packages.view", "AAA")).toBe(false);
    expect(hasPermission(me(), "payments.verify")).toBe(false);
  });
  it("global permissions apply to any office; no user => false", () => {
    expect(hasPermission(me({ permissions: { "*": ["audit.view"] } }), "audit.view", "AAA")).toBe(true);
    expect(hasPermission(null, "customers.view")).toBe(false);
  });
});

describe("format", () => {
  it("formats PKR", () => {
    expect(formatPKR("1500")).toBe("PKR 1,500.00");
    expect(formatPKR(null)).toBe("-");
    expect(formatPKR("abc")).toBe("abc");
  });
  it("shows dates in Pakistan time", () => {
    expect(formatDateTime("2026-01-10T19:30:00Z")).toBe("11 Jan 2026, 00:30"); // UTC+5
    expect(formatDateTime(null)).toBe("-");
  });
  it("humanizes", () => expect(humanize("pending_installation")).toBe("Pending installation"));
});

describe("api()", () => {
  const calls: { url: string; init: RequestInit }[] = [];
  beforeEach(() => {
    calls.length = 0;
    vi.stubGlobal("document", { cookie: "csrftoken=tok123" });
    vi.stubGlobal("window", { dispatchEvent: vi.fn() });
  });
  afterEach(() => vi.unstubAllGlobals());

  const mockFetch = (status: number, body: unknown) =>
    vi.stubGlobal("fetch", vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return { status, ok: status < 400, json: async () => body } as Response;
    }));

  it("sends the CSRF header on writes only", async () => {
    mockFetch(200, { ok: true });
    await api("/customers/", { method: "POST", body: { a: 1 } });
    await api("/customers/", { query: { page: 2 } });
    expect((calls[0].init.headers as Record<string, string>)["X-CSRFToken"]).toBe("tok123");
    expect(calls[0].init.body).toBe('{"a":1}');
    expect((calls[1].init.headers as Record<string, string>)["X-CSRFToken"]).toBeUndefined();
    expect(calls[1].url).toBe("/api/v1/customers/?page=2");
  });

  it("turns the error envelope into ApiError", async () => {
    mockFetch(409, { error: { code: "possible_duplicate", message: "dup", details: { duplicates: [1] } } });
    await expect(api("/customers/", { method: "POST", body: {} })).rejects.toMatchObject({ status: 409, code: "possible_duplicate", message: "dup" });
  });

  it("signals session expiry", async () => {
    mockFetch(403, { error: { code: "not_authenticated", message: "x" } });
    await expect(api("/auth/me/")).rejects.toBeInstanceOf(ApiError);
    expect((window.dispatchEvent as ReturnType<typeof vi.fn>)).toHaveBeenCalled();
  });

  it("handles 204", async () => {
    mockFetch(204, undefined);
    await expect(api("/auth/logout/", { method: "POST" })).resolves.toBeUndefined();
  });
});


describe("fitWithin (browser-side image resize)", () => {
  it("keeps small images and scales large ones to the 1600px limit", () => {
    expect(fitWithin(800, 600)).toEqual({ width: 800, height: 600 });
    expect(fitWithin(4000, 2000)).toEqual({ width: 1600, height: 800 });
    expect(fitWithin(1000, 3200)).toEqual({ width: 500, height: 1600 });
    expect(fitWithin(1600, 1600)).toEqual({ width: 1600, height: 1600 });
  });
});
