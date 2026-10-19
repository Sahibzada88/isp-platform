"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page } from "@/lib/types";
import { formatDateTime, formatPKR, humanize } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { Alert, Button, Card, DataTable, PageHeader, SelectField, StatusBadge, TextField, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;
type Tab = "deposits" | "corrections" | "refunds" | "overpayments";

export default function CashPage() {
  const { user, can } = useAuth();
  const run = useRun();
  const [tab, setTab] = useState<Tab>("deposits");
  const summary = useFetch<R>("/cash/summary/");
  const deps = useFetch<Page<R>>(tab === "deposits" ? "/cash/deposits/" : null);
  const corr = useFetch<Page<R>>(tab === "corrections" ? "/corrections/" : null);
  const refs = useFetch<Page<R>>(tab === "refunds" ? "/refunds/" : null);
  const over = useFetch<Page<R>>(tab === "overpayments" ? "/overpayments/" : null);
  const offices = useFetch<Page<Office>>("/offices/");
  const [dlg, setDlg] = useState<null | { k: string; row?: R }>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const set = (k: string) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const close = () => { setDlg(null); setF({}); };
  const reload = () => { deps.reload(); corr.reload(); refs.reload(); over.reload(); summary.reload(); };
  const call = (path: string, body?: object) => async () => { await api(path, { method: "POST", body: body ?? {} }); close(); reload(); };
  const quick = async (path: string, msg: string, body: object = {}) => { if (await run(() => api(path, { method: "POST", body }), msg)) reload(); };

  const dcols: Column<R>[] = [
    { header: "Deposit", cell: (d) => d.number }, { header: "Collector", cell: (d) => d.collector_name }, { header: "Expected", cell: (d) => formatPKR(d.expected_amount), className: "text-end" },
    { header: "Declared", cell: (d) => formatPKR(d.declared_amount), className: "text-end" }, { header: "Counted", cell: (d) => d.received_amount ? formatPKR(d.received_amount) : "-", className: "text-end" },
    { header: "Variance", cell: (d) => d.variance === null ? "-" : <span className={Number(d.variance) !== 0 ? "font-semibold text-red-700" : ""}>{formatPKR(d.variance)}</span>, className: "text-end" },
    { header: "Status", cell: (d) => <StatusBadge status={d.status} /> },
    { header: "", cell: (d) => d.status === "submitted" && can("cash.reconcile") ? (d.collector === user?.id && !user?.is_owner ? <span className="text-xs text-slate-500">Counted by someone else</span> :
      <div className="flex gap-1"><Button variant="secondary" onClick={() => setDlg({ k: "reconcile", row: d })}>Reconcile</Button><Button variant="ghost" onClick={() => setDlg({ k: "cancelDep", row: d })}>Cancel</Button></div>) : null },
  ];
  const ccols: Column<R>[] = [
    { header: "Invoice", cell: (c) => c.invoice }, { header: "Amount", cell: (c) => formatPKR(c.amount), className: "text-end" }, { header: "Reason", cell: (c) => c.reason },
    { header: "Requested", cell: (c) => formatDateTime(c.requested_at) }, { header: "Status", cell: (c) => <StatusBadge status={c.status} /> },
    { header: "", cell: (c) => c.status === "pending" && can("payments.approve_correction") ? (c.requested_by === user?.id && !user?.is_owner ? <span className="text-xs text-slate-500">Needs another approver</span> :
      <div className="flex gap-1"><Button variant="secondary" onClick={() => setDlg({ k: "approveCorr", row: c })}>Approve reversal</Button><Button variant="ghost" onClick={() => setDlg({ k: "rejectCorr", row: c })}>Reject</Button></div>) : null },
  ];
  const rcols: Column<R>[] = [
    { header: "Invoice", cell: (r) => r.invoice }, { header: "Amount", cell: (r) => formatPKR(r.amount), className: "text-end" }, { header: "Reason", cell: (r) => r.reason }, { header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
    { header: "Method", cell: (r) => humanize(r.method || r.original_method) },
    { header: "", cell: (r) => (
      <div className="flex gap-1">
        {r.status === "requested" && user?.is_owner && <><Button variant="secondary" onClick={() => quick(`/refunds/${r.id}/approve/`, "Refund approved.")}>Approve (owner)</Button><Button variant="ghost" onClick={() => setDlg({ k: "rejectRef", row: r })}>Reject</Button></>}
        {r.status === "requested" && !user?.is_owner && <span className="text-xs text-slate-500">Awaiting owner approval</span>}
        {r.status === "approved" && can("refunds.pay") && <Button variant="secondary" onClick={() => quick(`/refunds/${r.id}/mark-paid/`, "Refund recorded as paid.")}>Mark paid</Button>}
      </div>) },
  ];
  const ocols: Column<R>[] = [
    { header: "Reference", cell: (o) => o.reference || "-" }, { header: "Method", cell: (o) => humanize(o.method) }, { header: "Received", cell: (o) => formatPKR(o.amount_received), className: "text-end" },
    { header: "Excess", cell: (o) => o.excess ? formatPKR(o.excess) : "-", className: "text-end" }, { header: "Notes", cell: (o) => o.notes }, { header: "Status", cell: (o) => <StatusBadge status={o.status} /> },
    { header: "", cell: (o) => o.status === "open" && user?.is_owner ? <div className="flex gap-1"><Button variant="secondary" onClick={() => setDlg({ k: "decideRefund", row: o })}>Refund</Button><Button variant="ghost" onClick={() => setDlg({ k: "decideSettle", row: o })}>Settlement</Button></div>
      : o.status === "refund_approved" && can("refunds.pay") ? <Button variant="secondary" onClick={() => quick(`/overpayments/${o.id}/mark-refunded/`, "Marked refunded.", { method: o.method })}>Mark refunded</Button> : null },
  ];
  const s = summary.data?.results?.[0];

  return (
    <>
      <PageHeader title="Cash & corrections" subtitle="Deposits are counted by someone other than the collector. Corrections never delete a payment."
        actions={<>{can("cash.deposit") && <Button onClick={() => setDlg({ k: "deposit" })}>New cash deposit</Button>}
          {can("payments.request_correction") && <Button variant="secondary" onClick={() => setDlg({ k: "corr" })}>Request correction</Button>}
          {can("refunds.request") && <Button variant="secondary" onClick={() => setDlg({ k: "refund" })}>Request refund</Button>}
          {can("payments.record_exception") && <Button variant="secondary" onClick={() => setDlg({ k: "over" })}>Record overpayment</Button>}</>} />
      {s && <Card title={`Today · ${s.office}`} className="mb-4"><div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {[["Cash collected", s.cash_collected], ["Digital verified", s.digital_verified], ["Digital pending", s.digital_pending_verification], ["Cash not deposited", s.cash_undeposited_total], ["Cash variance", s.cash_variance], ["Reversed today", s.reversed_today]].map(([l, v]) => (
          <div key={l}><div className="text-xs uppercase text-slate-500">{l}</div><div className="text-sm font-semibold">{formatPKR(v)}</div></div>))}
      </div></Card>}
      <div role="tablist" className="mb-4 flex gap-1 border-b border-slate-200">
        {(["deposits", "corrections", "refunds", "overpayments"] as Tab[]).map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm font-medium ${tab === t ? "border-b-2 border-brand-600 text-brand-700" : "text-slate-600"}`}>{humanize(t)}</button>)}
      </div>
      {tab === "deposits" && <DataTable columns={dcols} rows={deps.data?.results} loading={deps.loading} rowKey={(d) => d.id} empty={{ title: "No deposits yet" }} />}
      {tab === "corrections" && <DataTable columns={ccols} rows={corr.data?.results} loading={corr.loading} rowKey={(c) => c.id} empty={{ title: "No correction requests" }} />}
      {tab === "refunds" && <DataTable columns={rcols} rows={refs.data?.results} loading={refs.loading} rowKey={(r) => r.id} empty={{ title: "No refunds" }} />}
      {tab === "overpayments" && <DataTable columns={ocols} rows={over.data?.results} loading={over.loading} rowKey={(o) => o.id} empty={{ title: "No overpayment exceptions" }} />}
      {(deps.error || corr.error || refs.error || over.error) && <Alert>{(deps.error || corr.error || refs.error || over.error)!.message}</Alert>}

      <ActionDialog open={dlg?.k === "deposit"} onClose={close} title="New cash deposit" confirmLabel="Submit deposit" confirmDisabled={!f.office}
        description="Deposits all of your confirmed cash receipts that are not yet in a deposit. A manager then counts it."
        onConfirm={call("/cash/deposits/", { office: Number(f.office), ...(f.declared ? { declared_amount: f.declared } : {}) })}>
        <SelectField label="Office" value={f.office || ""} onChange={set("office")}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField>
        <TextField label="Amount you are handing over (optional)" value={f.declared || ""} onChange={set("declared")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "reconcile"} onClose={close} title="Reconcile deposit" confirmLabel="Record count" confirmDisabled={!f.received}
        description={`Expected PKR ${dlg?.row?.expected_amount ?? ""}. A shortage or surplus needs a note and is kept for investigation.`}
        onConfirm={call(`/cash/deposits/${dlg?.row?.number}/reconcile/`, { received_amount: f.received, variance_note: f.note || "" })}>
        <TextField label="Cash actually counted (PKR)" value={f.received || ""} onChange={set("received")} /><TextField label="Variance note (required if different)" value={f.note || ""} onChange={set("note")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "cancelDep"} onClose={close} title="Cancel deposit" confirmLabel="Cancel deposit" danger reasonLabel="Reason" onConfirm={async (r) => { await call(`/cash/deposits/${dlg?.row?.number}/cancel/`, { reason: r })(); }} />
      <ActionDialog open={dlg?.k === "corr"} onClose={close} title="Request payment correction" confirmLabel="Submit request" confirmDisabled={!f.payment || !f.reason}
        description="Asks for a confirmed payment to be reversed (for example entered against the wrong customer). Another authorised employee must approve."
        onConfirm={call("/corrections/", { payment: Number(f.payment), reason: f.reason })}><TextField label="Payment ID" inputMode="numeric" value={f.payment || ""} onChange={set("payment")} hint="Shown on the Payments page link / API." /><TextField label="Reason" value={f.reason || ""} onChange={set("reason")} /></ActionDialog>
      <ActionDialog open={dlg?.k === "approveCorr"} onClose={close} title="Approve reversal" confirmLabel="Approve & reverse" danger description="The payment is marked reversed, its receipt invalidated and the invoice reopened. Nothing is deleted."
        onConfirm={call(`/corrections/${dlg?.row?.id}/approve/`, {})} />
      <ActionDialog open={dlg?.k === "rejectCorr"} onClose={close} title="Reject correction" confirmLabel="Reject" reasonLabel="Reason" onConfirm={async (r) => { await call(`/corrections/${dlg?.row?.id}/reject/`, { note: r })(); }} />
      <ActionDialog open={dlg?.k === "refund"} onClose={close} title="Request refund" confirmLabel="Submit request" confirmDisabled={!f.payment || !f.amount || !f.reason} description="The owner approves every refund."
        onConfirm={call("/refunds/", { payment: Number(f.payment), amount: f.amount, reason: f.reason })}><TextField label="Payment ID" inputMode="numeric" value={f.payment || ""} onChange={set("payment")} /><TextField label="Amount (PKR)" value={f.amount || ""} onChange={set("amount")} /><TextField label="Reason" value={f.reason || ""} onChange={set("reason")} /></ActionDialog>
      <ActionDialog open={dlg?.k === "rejectRef"} onClose={close} title="Reject refund" confirmLabel="Reject" danger reasonLabel="Reason" onConfirm={async (r) => { await call(`/refunds/${dlg?.row?.id}/reject/`, { note: r })(); }} />
      <ActionDialog open={dlg?.k === "over"} onClose={close} title="Record overpayment" confirmLabel="Record exception" confirmDisabled={!f.office || !f.amount || !f.when}
        description="For a real transfer larger than the invoice. It is kept for the owner to decide; nothing is credited automatically."
        onConfirm={call("/overpayments/", { office: Number(f.office), invoice: (f.invoice || "").toUpperCase(), method: f.method || "easypaisa", amount_received: f.amount, transaction_at: new Date(f.when).toISOString(), reference: f.reference || "", notes: f.notes || "" })}>
        <SelectField label="Office" value={f.office || ""} onChange={set("office")}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField>
        <div className="grid grid-cols-2 gap-3"><TextField label="Invoice (optional)" value={f.invoice || ""} onChange={set("invoice")} /><SelectField label="Method" value={f.method || "easypaisa"} onChange={set("method")}>{["cash", "easypaisa", "jazzcash", "bank_transfer"].map((m) => <option key={m} value={m}>{humanize(m)}</option>)}</SelectField></div>
        <div className="grid grid-cols-2 gap-3"><TextField label="Amount received" value={f.amount || ""} onChange={set("amount")} /><TextField label="Reference" value={f.reference || ""} onChange={set("reference")} /></div>
        <TextField label="Transaction time" type="datetime-local" value={f.when || ""} onChange={set("when")} /><TextField label="Evidence notes" value={f.notes || ""} onChange={set("notes")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "decideRefund"} onClose={close} title="Approve refund of the excess" confirmLabel="Approve refund" reasonLabel="Note" onConfirm={async (n) => { await call(`/overpayments/${dlg?.row?.id}/decide/`, { decision: "refund", note: n })(); }} />
      <ActionDialog open={dlg?.k === "decideSettle"} onClose={close} title="Approve settlement" confirmLabel="Approve settlement" reasonLabel="Describe the agreed settlement" onConfirm={async (n) => { await call(`/overpayments/${dlg?.row?.id}/decide/`, { decision: "settlement", note: n })(); }} />
    </>
  );
}
