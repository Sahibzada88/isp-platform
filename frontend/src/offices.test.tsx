// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import OfficesPage from "@/app/(app)/admin/offices/page";
import { AuthProvider } from "@/lib/auth";
import { I18nProvider } from "@/lib/i18n";
import { ToastProvider } from "@/components/ui";

vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn(), push: vi.fn() }), usePathname: () => "/", useParams: () => ({}) }));
vi.mock("next/link", () => ({ default: ({ href, children, ...rest }: any) => <a href={href} {...rest}>{children}</a> }));

const OWNER = { id: 1, username: "owner", role: "owner", is_owner: true, must_change_password: false, permissions: {}, offices: [] };
const MANAGER = { id: 2, username: "mgr", role: "manager", is_owner: false, must_change_password: false, permissions: { KOT: ["customers.view"] }, offices: ["KOT"] };

function setup(me: object, offices: object[], villages: object[] = []) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const page = (results: object[]) => ({ count: results.length, results });
    let body: unknown = {};
    if (url.includes("/auth/me/")) body = me;
    else if (url.includes("/offices/") && (!init || init.method === "GET")) body = page(offices);
    else if (url.includes("/villages/") && (!init || init.method === "GET")) body = page(villages);
    return { status: init?.method === "POST" ? 201 : 200, ok: true, json: async () => body } as Response;
  }));
  render(<I18nProvider><AuthProvider><ToastProvider><OfficesPage /></ToastProvider></AuthProvider></I18nProvider>);
  return calls;
}

beforeEach(() => { document.cookie = "csrftoken=tok; path=/"; });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("Offices & villages screen (first-time setup)", () => {
  it("guides the owner to create the first office and sends an upper-cased code", async () => {
    const calls = setup(OWNER, []);
    await userEvent.click(await screen.findByRole("button", { name: "Create the first office" }));
    await userEvent.type(screen.getByLabelText("Office code"), "kot");
    await userEvent.type(screen.getByLabelText("Office name"), "Kotha Office");
    await userEvent.click(screen.getByLabelText(/head \/ central office/));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.init?.method === "POST")).toBe(true));
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect(post.url).toBe("/api/v1/offices/");
    expect(JSON.parse(post.init!.body as string)).toEqual({ code: "KOT", name: "Kotha Office", is_head_office: true, is_active: true });
  });

  it("adds a village under an office with the office id", async () => {
    const calls = setup(OWNER, [{ id: 7, code: "KOT", name: "Kotha Office", is_active: true }], []);
    await userEvent.click(await screen.findByRole("button", { name: "Add village" }));
    await userEvent.type(screen.getByLabelText("Village code"), "kht");
    await userEvent.type(screen.getByLabelText("Village name"), "Kotha");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/villages/" && c.init?.method === "POST")).toBe(true));
    const post = calls.find((c) => c.url === "/api/v1/villages/" && c.init?.method === "POST")!;
    expect(JSON.parse(post.init!.body as string)).toEqual({ code: "KHT", name: "Kotha", is_active: true, office: 7 });
  });

  it("lists existing villages under their office and hides edit buttons from people without the right", async () => {
    setup(MANAGER, [{ id: 7, code: "KOT", name: "Kotha Office", is_active: true }], [{ id: 1, code: "KHT", name: "Kotha", office: 7, is_active: true }]);
    expect(await screen.findByText("KHT")).toBeTruthy();
    expect(screen.getByText(/Only the owner/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New office" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add village" })).toBeNull();
  });
});
