"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import { formatPKR, humanize } from "@/lib/format";
import { Alert, Card, PageHeader, Spinner } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type D = Record<string, any>;

function Stat({ label, value, href, tone }: { label: string; value: string | number; href?: string; tone?: "warn" }) {
  const body = (
    <div className={`rounded-md border px-3 py-2 ${tone === "warn" ? "border-amber-200 bg-amber-50" : "border-slate-200 bg-white"}`}>
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-0.5 text-lg font-semibold text-slate-900">{value}</div>
    </div>
  );
  return href ? <Link href={href} className="block hover:opacity-90">{body}</Link> : body;
}

function GettingStarted() {
  const { can } = useAuth();
  const offices = useFetch<{ count: number }>("/offices/", { page_size: 1 });
  const villages = useFetch<{ count: number }>("/villages/", { page_size: 1 });
  const packages = useFetch<{ count: number }>("/packages/", { page_size: 1 });
  const staff = useFetch<{ count: number }>(can("employees.view") ? "/employees/" : null, { page_size: 1 });
  if (!can("offices.create") || [offices, villages, packages].some((x) => x.loading && !x.data)) return null;
  const steps = [
    { done: (offices.data?.count ?? 0) > 0, label: "Create your office(s)", href: "/admin/offices", hint: "Head office and each village office." },
    { done: (villages.data?.count ?? 0) > 0, label: "Add the villages each office serves", href: "/admin/offices", hint: "The village code starts every customer ID." },
    { done: (packages.data?.count ?? 0) > 0, label: "Create your internet packages", href: "/admin/packages", hint: "Name, speed and price. Every package lasts 30 days." },
    { done: false, optional: true, label: "Set the installation fee and financial year", href: "/admin/settings", hint: "Optional; you can change it later." },
    { done: (staff.data?.count ?? 1) > 1, label: "Add employees and check their permissions", href: "/admin/employees", hint: "Managers, office staff, technicians, collectors." },
    { done: false, optional: true, label: "Register customers, or import your old data", href: "/customers/new", hint: "Use “Data import” for existing customers (dry run first)." },
  ];
  const required = steps.filter((x) => !x.optional);
  if (required.every((x) => x.done)) return null;
  return (
    <Card title="Getting started: set up in this order">
      <ol className="space-y-2">
        {steps.map((st, i) => (
          <li key={st.label} className="flex items-start gap-3 text-sm">
            <span aria-hidden className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs ${st.done ? "bg-green-600 text-white" : "border border-slate-300 text-slate-500"}`}>{st.done ? "✓" : i + 1}</span>
            <div><Link href={st.href} className={`font-medium ${st.done ? "text-slate-500 line-through" : "text-brand-700 hover:underline"}`}>{st.label}</Link>{st.optional && <span className="ms-2 text-xs text-slate-400">optional</span>}<div className="text-xs text-slate-500">{st.hint}</div></div>
          </li>))}
      </ol>
    </Card>
  );
}

export default function DashboardPage() {
  const { data, loading, error } = useFetch<D>("/dashboard/");
  if (loading && !data) return <Spinner className="h-6 w-6 text-brand-600" />;
  if (error) return <Alert>{error.message}</Alert>;
  const d = data!;
  const grid = "grid gap-3 sm:grid-cols-2 lg:grid-cols-4";
  return (
    <>
      <PageHeader title="Dashboard" subtitle="Live figures from your records. Verified, pending and reversed amounts are never blended." />
      <div className="space-y-5">
        <GettingStarted />
        {d.my_attendance && !d.my_attendance.checked_in && <Alert kind="info">You have not checked in today. <Link href="/attendance" className="underline">Check in</Link></Alert>}
        {d.customers && d.connections && (
          <Card title="Customers & connections"><div className={grid}>
            <Stat label="Customers" value={d.customers.total} href="/customers" />
            <Stat label="Connections" value={d.connections.total} href="/connections" />
            <Stat label="Active" value={d.connections.by_status?.active ?? 0} />
            <Stat label="Expired" value={d.connections.by_status?.expired ?? 0} />
            <Stat label="Pending installation" value={d.connections.pending_installations} tone={d.connections.pending_installations ? "warn" : undefined} />
            <Stat label="Pending approval" value={d.connections.pending_approvals} tone={d.connections.pending_approvals ? "warn" : undefined} />
            <Stat label="Expiring in 7 days" value={d.connections.expiring_in_7_days} />
            <Stat label="Cancelled" value={d.connections.by_status?.cancelled ?? 0} />
          </div></Card>
        )}
        {d.collections && (
          <Card title="Collections (verified money only)"><div className={grid}>
            <Stat label="Cash today" value={formatPKR(d.collections.today.cash)} />
            <Stat label="Digital verified today" value={formatPKR(d.collections.today.digital_verified)} />
            <Stat label="Cash this month" value={formatPKR(d.collections.month.cash)} />
            <Stat label="Digital this month" value={formatPKR(d.collections.month.digital_verified)} />
            <Stat label="Awaiting verification" value={`${d.collections.pending_verification.count} · ${formatPKR(d.collections.pending_verification.amount)}`} href="/payments" tone={d.collections.pending_verification.count ? "warn" : undefined} />
            <Stat label="Cash not yet deposited" value={formatPKR(d.collections.undeposited_cash)} href="/cash" />
            <Stat label="Deposits to reconcile" value={d.collections.deposits_awaiting_reconciliation} href="/cash" />
            <Stat label="Reversed this month" value={formatPKR(d.collections.reversed_this_month)} />
            {d.outstanding_invoices && <Stat label="Unpaid invoices" value={`${d.outstanding_invoices.count} · ${formatPKR(d.outstanding_invoices.amount)}`} href="/invoices" />}
          </div>
          {d.office_income_month && <div className="mt-4"><div className="mb-2 text-xs uppercase text-slate-500">Income by office, this month</div>
            <div className={grid}>{Object.entries(d.office_income_month).map(([o, v]) => <Stat key={o} label={o} value={formatPKR(v as string)} />)}</div></div>}
          </Card>
        )}
        {d.expenses && (
          <Card title="Expenses"><div className={grid}>
            <Stat label="Approved this month" value={formatPKR(d.expenses.approved_this_month)} />
            <Stat label="Awaiting approval" value={`${d.expenses.pending_approval_count} · ${formatPKR(d.expenses.pending_approval_amount)}`} href="/expenses" tone={d.expenses.pending_approval_count ? "warn" : undefined} />
          </div></Card>
        )}
        <div className="grid gap-5 lg:grid-cols-2">
          {d.complaints && (
            <Card title="Open complaints"><div className="grid grid-cols-2 gap-3">
              <Stat label="Open" value={d.complaints.open} href="/complaints" />
              <Stat label="Unassigned" value={d.complaints.unassigned} tone={d.complaints.unassigned ? "warn" : undefined} />
              {Object.entries(d.complaints.by_priority).map(([p, n]) => <Stat key={p} label={humanize(p)} value={n as number} />)}
            </div></Card>
          )}
          {(d.tasks || d.inventory || d.attendance_today || d.leave_pending !== undefined) && (
            <Card title="Operations"><div className="grid grid-cols-2 gap-3">
              {d.tasks && <Stat label="My open tasks" value={d.tasks.mine_open} href="/tasks" />}
              {d.tasks && <Stat label="My overdue tasks" value={d.tasks.mine_overdue} tone={d.tasks.mine_overdue ? "warn" : undefined} />}
              {d.inventory && <Stat label="Low-stock items" value={d.inventory.low_stock_items} href="/inventory" tone={d.inventory.low_stock_items ? "warn" : undefined} />}
              {d.attendance_today && <Stat label="Checked in today" value={d.attendance_today.checked_in} href="/attendance" />}
              {d.leave_pending !== undefined && <Stat label="Leave requests" value={d.leave_pending} href="/attendance" />}
            </div></Card>
          )}
        </div>
        <p className="text-xs text-slate-500">{d.scope_note}</p>
      </div>
    </>
  );
}
