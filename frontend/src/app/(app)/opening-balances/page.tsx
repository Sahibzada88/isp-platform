"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { formatDate, formatPKR } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { Alert, Button, Card, DataTable, FilterSelect, PageHeader, StatusBadge, TextField, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;

export default function OpeningBalancesPage() {
  const { can } = useAuth();
  const run = useRun();
  const router = useRouter();
  const [status, setStatus] = useState("");
  const list = useFetch<Page<R>>("/opening-balances/", { status });
  const sum = useFetch<R>("/opening-balances/summary/");
  const [pick, setPick] = useState<R | null>(null);
  const [conn, setConn] = useState("");

  const makeInvoice = async (b: R, connection = "") => {
    const inv = await api<R>(`/opening-balances/${b.id}/invoice/`, { method: "POST", body: { connection } });
    router.push(`/invoices/${inv.number}`);
  };
  const cols: Column<R>[] = [
    { header: "Customer", cell: (b) => <Link href={`/customers/${b.customer_id}`} className="text-brand-700 hover:underline">{b.customer_id}</Link> },
    { header: "Connection", cell: (b) => b.connection_id || "-" }, { header: "Office", cell: (b) => b.office_code }, { header: "Amount", cell: (b) => formatPKR(b.amount), className: "text-end" },
    { header: "Old reference", cell: (b) => b.source_reference }, { header: "Migrated", cell: (b) => formatDate(b.migration_date) }, { header: "Status", cell: (b) => <StatusBadge status={b.status} /> },
    { header: "", cell: (b) => (
      <div className="flex gap-1">
        {b.status === "unverified" && can("imports.manage") && <Button variant="secondary" onClick={async () => { if (await run(() => api(`/opening-balances/${b.id}/verify/`, { method: "POST" }), "Balance verified.")) { list.reload(); sum.reload(); } }}>Verify</Button>}
        {b.status === "verified" && can("billing.create_invoice") && <Button variant="secondary" onClick={() => b.connection_id ? run(() => makeInvoice(b), "Invoice ready.") : setPick(b)}>{b.invoice ? "Open invoice" : "Create invoice"}</Button>}
        {b.status === "settled" && <span className="text-xs text-slate-500">Collected</span>}
      </div>) },
  ];
  return (
    <>
      <PageHeader title="Opening balances" subtitle="Money owed before go-live. It is not income, and no invoices or payments were invented for it." />
      {sum.data && <Card className="mb-4"><div className="grid gap-3 sm:grid-cols-4">
        {[["Verified (collectable)", sum.data.verified], ["Unverified", sum.data.unverified], ["Collected", sum.data.settled]].map(([l, v]) => <div key={l}><div className="text-xs uppercase text-slate-500">{l}</div><div className="text-lg font-semibold">{formatPKR(v)}</div></div>)}
        <div><div className="text-xs uppercase text-slate-500">Records</div><div className="text-lg font-semibold">{sum.data.count}</div></div></div></Card>}
      <div className="mb-3"><FilterSelect label="Status" value={status} onChange={setStatus} options={["unverified", "verified", "settled"].map((s) => ({ value: s, label: s }))} /></div>
      {list.error && <Alert>{list.error.message}</Alert>}
      <DataTable columns={cols} rows={list.data?.results} loading={list.loading} rowKey={(b) => b.id} empty={{ title: "No opening balances" }} />
      <ActionDialog open={!!pick} onClose={() => setPick(null)} title="Attach to a connection" confirmLabel="Create invoice" confirmDisabled={!conn.trim()}
        description="This balance has no connection. Choose one of the customer's connections to hang the invoice on."
        onConfirm={async () => { await makeInvoice(pick!, conn.trim().toUpperCase()); }}><TextField label="Connection" placeholder="CON-00001" value={conn} onChange={(e) => setConn(e.target.value)} /></ActionDialog>
    </>
  );
}
