// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { ActionDialog } from "@/components/actions";
import { CustomerPicker } from "@/components/customer-picker";
import { ImageAttachments } from "@/components/image-upload";
import { Button, DataTable, Pagination, StatusBadge, ToastProvider } from "@/components/ui";
import { AuthProvider } from "@/lib/auth";
import { I18nProvider, useI18n } from "@/lib/i18n";
import LoginPage from "@/app/login/page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace, push: vi.fn() }), usePathname: () => "/", useParams: () => ({}) }));

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown };
function mockApi(handler: Handler) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const r = handler(url, init) ?? {};
    const status = r.status ?? 200;
    return { status, ok: status < 400, json: async () => r.body } as Response;
  }));
  return calls;
}
const wrap = (ui: ReactNode) => render(<I18nProvider><AuthProvider><ToastProvider>{ui}</ToastProvider></AuthProvider></I18nProvider>);

beforeEach(() => { document.cookie = "csrftoken=tok; path=/"; localStorage.clear(); replace.mockClear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("DataTable / Pagination", () => {
  const cols = [{ header: "Name", cell: (r: { n: string }) => r.n }, { header: "Status", cell: () => <StatusBadge status="pending_installation" /> }];
  it("renders rows, headers and a humanized status badge", () => {
    wrap(<DataTable columns={cols} rows={[{ n: "Ali" }, { n: "Sara" }]} loading={false} rowKey={(r) => r.n} empty={{ title: "Nothing" }} />);
    expect(screen.getByRole("columnheader", { name: "Name" })).toBeTruthy();
    expect(screen.getByText("Sara")).toBeTruthy();
    expect(screen.getAllByText("Pending installation")).toHaveLength(2);
  });
  it("shows the empty state only when not loading", () => {
    const { rerender } = wrap(<DataTable columns={cols} rows={[]} loading={true} rowKey={() => 1} empty={{ title: "No customers found", hint: "Try again" }} />);
    expect(screen.queryByText("No customers found")).toBeNull();
    rerender(<I18nProvider><AuthProvider><ToastProvider><DataTable columns={cols} rows={[]} loading={false} rowKey={() => 1} empty={{ title: "No customers found", hint: "Try again" }} /></ToastProvider></AuthProvider></I18nProvider>);
    expect(screen.getByText("No customers found")).toBeTruthy();
  });
  it("pagination disables the edges and reports page changes", async () => {
    const onPage = vi.fn();
    wrap(<Pagination page={1} count={60} onPage={onPage} />);
    expect((screen.getByRole("button", { name: "Previous" }) as HTMLButtonElement).disabled).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(onPage).toHaveBeenCalledWith(2);
    expect(screen.getByText("Page 1 of 3")).toBeTruthy();
  });
});

describe("ActionDialog (financial/destructive confirmations)", () => {
  it("requires a reason, then submits it trimmed", async () => {
    const onConfirm = vi.fn(async () => {});
    wrap(<ActionDialog open onClose={() => {}} title="Void invoice" confirmLabel="Void it" reasonLabel="Reason" onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Void it" }));
    expect((await screen.findByRole("alert")).textContent).toContain("A reason is required");
    expect(onConfirm).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText("Reason"), "  duplicate invoice  ");
    await userEvent.click(screen.getByRole("button", { name: "Void it" }));
    await waitFor(() => expect(onConfirm).toHaveBeenCalledWith("duplicate invoice"));
  });
  it("shows the API's message inline when the action fails", async () => {
    const { ApiError } = await import("@/lib/api");
    const onConfirm = vi.fn(async () => { throw new ApiError(400, { code: "business_rule_violation", message: "Only open invoices can be voided." }); });
    wrap(<ActionDialog open onClose={() => {}} title="Void" confirmLabel="Go" onConfirm={onConfirm} />);
    await userEvent.click(screen.getByRole("button", { name: "Go" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Only open invoices can be voided.");
  });
});

describe("Urdu / RTL", () => {
  function Probe() {
    const { tr, toggle, lang } = useI18n();
    return <div><span data-testid="t">{tr("Customers")}</span><span data-testid="unknown">{tr("Some brand new string")}</span><button onClick={toggle}>{lang}</button></div>;
  }
  it("switches language, flips the page direction and remembers the choice", async () => {
    wrap(<Probe />);
    expect(screen.getByTestId("t").textContent).toBe("Customers");
    expect(document.documentElement.dir).toBe("ltr");
    await userEvent.click(screen.getByRole("button"));
    expect(screen.getByTestId("t").textContent).toBe("صارفین");
    expect(screen.getByTestId("unknown").textContent).toBe("Some brand new string");   // missing phrases stay English
    expect(document.documentElement.dir).toBe("rtl");
    expect(document.documentElement.lang).toBe("ur");
    expect(localStorage.getItem("isp-lang")).toBe("ur");
  });
  it("translates shared components (buttons, status badges)", async () => {
    localStorage.setItem("isp-lang", "ur");
    wrap(<div><Button>Save</Button><StatusBadge status="active" /></div>);
    await waitFor(() => expect(screen.getByRole("button").textContent).toBe("محفوظ کریں"));
    expect(screen.getByText("فعال")).toBeTruthy();
  });
});

describe("LoginPage", () => {
  it("signs in with the CSRF token and goes to the dashboard", async () => {
    const calls = mockApi((url) => {
      if (url.endsWith("/auth/me/")) return { status: 403, body: { error: { code: "not_authenticated", message: "x" } } };
      if (url.endsWith("/auth/login/")) return { body: { id: 1, username: "ali", role: "owner", is_owner: true, must_change_password: false, permissions: {}, offices: [] } };
      return {};
    });
    wrap(<LoginPage />);
    await userEvent.type(await screen.findByLabelText("Username or e-mail"), "ali");
    await userEvent.type(screen.getByLabelText("Password"), "secret-pass");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/dashboard"));
    const login = calls.find((c) => c.url.endsWith("/auth/login/"))!;
    expect(JSON.parse(login.init!.body as string)).toEqual({ username: "ali", password: "secret-pass" });
    expect((login.init!.headers as Record<string, string>)["X-CSRFToken"]).toBe("tok");
  });
  it("shows a generic error for bad credentials and stays on the page", async () => {
    mockApi((url) => url.endsWith("/auth/login/") ? { status: 401, body: { error: { code: "invalid_credentials", message: "Invalid credentials or account locked." } } }
      : { status: 403, body: { error: { code: "not_authenticated", message: "x" } } });
    wrap(<LoginPage />);
    await userEvent.type(await screen.findByLabelText("Username or e-mail"), "ali");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect((await screen.findByRole("alert")).textContent).toBe("Invalid credentials or account locked.");
    expect(replace).not.toHaveBeenCalledWith("/dashboard");
  });
  it("sends staff who must change their password to that page", async () => {
    mockApi((url) => url.endsWith("/auth/me/") ? { body: { id: 2, username: "x", role: "office_staff", is_owner: false, must_change_password: true, permissions: {}, offices: [] } } : {});
    wrap(<LoginPage />);
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/account/password"));
  });
});

describe("CustomerPicker", () => {
  it("searches after two characters, lists matches and reports the chosen customer id", async () => {
    const calls = mockApi((url) => ({ body: { count: 1, results: [{ id: 5, customer_id: "KHT005", full_name: "Bilal Ahmed", phone: "0300111" }] } }));
    const onPick = vi.fn();
    wrap(<CustomerPicker onPick={onPick} />);
    await userEvent.type(screen.getByLabelText("Customer"), "b");
    const searches = () => calls.filter((c) => c.url.includes("/customers/"));
    expect(searches()).toHaveLength(0);                                               // no search before two characters
    await userEvent.type(screen.getByLabelText("Customer"), "il");
    const option = await screen.findByRole("option", { name: /KHT005/ });
    expect(searches().at(-1)!.url).toContain("search=bil");
    await userEvent.click(option);
    expect(onPick).toHaveBeenCalledWith("KHT005", expect.objectContaining({ full_name: "Bilal Ahmed" }));
    expect((screen.getByLabelText("Customer") as HTMLInputElement).value).toBe("KHT005 · Bilal Ahmed");
  });
});

describe("ImageAttachments", () => {
  it("loads thumbnails through the authorised endpoint and hides upload without permission", async () => {
    mockApi(() => ({ body: [{ id: 9, filename: "bill.jpg", width: 800, height: 600, size_bytes: 1, created_at: "2026-01-01T00:00:00Z" }] }));
    wrap(<ImageAttachments entityType="expense" entityId={3} kind="expense_receipt" canUpload={false} />);
    const img = (await screen.findByAltText("bill.jpg")) as HTMLImageElement;
    expect(img.getAttribute("src")).toBe("/api/v1/attachments/9/thumbnail/");
    expect(screen.queryByRole("button", { name: "Add photo" })).toBeNull();
  });
  it("offers an upload button when permitted and shows an empty message", async () => {
    mockApi(() => ({ body: [] }));
    wrap(<ImageAttachments entityType="expense" entityId={3} kind="expense_receipt" canUpload />);
    expect(await screen.findByText("No images attached.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add photo" })).toBeTruthy();
  });
});
