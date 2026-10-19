"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";
import { useAuth } from "@/lib/auth";
import { Button, Spinner } from "@/components/ui";
import { NotificationsBell } from "@/components/notifications-bell";
import { useI18n } from "@/lib/i18n";

const NAV = [
  { href: "/dashboard", key: "dashboard", perm: "attendance.self" },
  { href: "/customers", key: "customers", perm: "customers.view" },
  { href: "/connections", key: "connections", perm: "connections.view" },
  { href: "/invoices", key: "invoices", perm: "billing.view" },
  { href: "/payments", key: "payments", perm: "billing.view" },
  { href: "/receipts", key: "receipts", perm: "billing.view" },
  { href: "/cash", key: "cash", perm: "billing.view" },
  { href: "/opening-balances", key: "openingBalances", perm: "billing.view" },
  { href: "/transfers", key: "transfers", perm: ["transfers.request", "transfers.approve"] },
  { href: "/complaints", key: "complaints", perm: ["complaints.view", "complaints.view_assigned"] },
  { href: "/tasks", key: "tasks", perm: ["tasks.view", "tasks.work", "tasks.manage"] },
  { href: "/leads", key: "leads", perm: "leads.view" },
  { href: "/inventory", key: "inventory", perm: "inventory.view" },
  { href: "/expenses", key: "expenses", perm: "expenses.view" },
  { href: "/attendance", key: "attendance", perm: "attendance.self" },
  { href: "/reports", key: "reports", perm: ["reports.view", "reports.export"] },
  { href: "/admin/offices", key: "offices", perm: "offices.create" },
  { href: "/admin/employees", key: "employees", perm: "employees.view" },
  { href: "/admin/packages", key: "packages", perm: "packages.view" },
  { href: "/admin/audit", key: "audit", perm: "audit.view" },
  { href: "/admin/imports", key: "imports", perm: "imports.manage" },
  { href: "/admin/settings", key: "settings", perm: "system.manage" },
];

export default function AppLayout({ children }: { children: ReactNode }) {
  const { user, loading, logout, can } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const { t, toggle } = useI18n();

  useEffect(() => {
    if (loading) return;
    if (!user) router.replace("/login");
    else if (user.must_change_password && pathname !== "/account/password") router.replace("/account/password");
  }, [user, loading, pathname, router]);
  useEffect(() => setOpen(false), [pathname]);

  if (loading || !user) {
    return <div className="flex h-screen items-center justify-center"><Spinner className="h-8 w-8 text-brand-600" /></div>;
  }
  const items = NAV.filter((n) => (Array.isArray(n.perm) ? n.perm : [n.perm]).some((p) => can(p)));

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-1 p-3">
      {items.map((n) => {
        const active = pathname === n.href || pathname.startsWith(`${n.href}/`);
        return (
          <Link key={n.href} href={n.href} aria-current={active ? "page" : undefined}
            className={`rounded-md px-3 py-2 text-sm font-medium ${active ? "bg-brand-50 text-brand-700" : "text-slate-700 hover:bg-slate-100"}`}>
            {t(n.key)}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <div className="min-h-screen lg:flex">
      <aside className="hidden w-56 shrink-0 border-e border-slate-200 bg-white lg:block">
        <div className="px-5 py-4 text-base font-semibold text-brand-900">ISP Management</div>
        {nav}
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-slate-200 bg-white px-4 py-2.5">
          <div className="flex items-center gap-2">
            <button type="button" className="rounded-md p-2 text-slate-700 hover:bg-slate-100 lg:hidden" aria-expanded={open} aria-label="Toggle menu" onClick={() => setOpen((o) => !o)}>
              <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M3 5h14M3 10h14M3 15h14" /></svg>
            </button>
            <span className="text-sm font-semibold text-brand-900 lg:hidden">ISP Management</span>
          </div>
          <div className="flex items-center gap-3 text-sm">
            <span className="hidden text-slate-600 sm:inline">{user.username} · {user.role.replace("_", " ")}</span>
            <NotificationsBell />
            <button type="button" onClick={toggle} className="text-slate-600 underline-offset-2 hover:underline">{t("language")}</button>
            <Link href="/account/password" className="text-slate-600 underline-offset-2 hover:underline">{t("password")}</Link>
            <Button variant="secondary" onClick={async () => { await logout(); router.replace("/login"); }}>{t("signOut")}</Button>
          </div>
        </header>
        {open && <div className="border-b border-slate-200 bg-white lg:hidden">{nav}</div>}
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-6">{children}</main>
      </div>
    </div>
  );
}
