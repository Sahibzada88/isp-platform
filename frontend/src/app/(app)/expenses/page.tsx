"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page } from "@/lib/types";
import { formatDate, formatPKR, humanize, METHOD_LABELS } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { ImageAttachments } from "@/components/image-upload";
import { Alert, Button, Card, DataTable, FilterSelect, Modal, PageHeader, Pagination, SelectField, StatusBadge, TextAreaField, TextField, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;
const CATS = ["rent", "electricity", "fuel", "transport", "repair", "supplies", "equipment", "office", "other"];

export default function ExpensesPage() {
  const { user, can } = useAuth();
  const run = useRun();
  const [status, setStatus] = useState(""); const [category, setCategory] = useState(""); const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [status, category]);
  const list = useFetch<Page<R>>("/expenses/", { status, category, page });
  const summary = useFetch<R>("/expenses/summary/", { status: "", category });
  const offices = useFetch<Page<Office>>("/offices/");
  const [dlg, setDlg] = useState<null | "new" | { reject: R } | { receipt: R }>(null);
  const [f, setF] = useState({ office: "", category: "other", description: "", amount: "", expense_date: new Date().toLocaleDateString("en-CA"), payment_method: "cash" });
  const reload = () => { list.reload(); summary.reload(); };

  const cols: Column<R>[] = [
    { header: "Expense", cell: (e) => e.number }, { header: "Date", cell: (e) => formatDate(e.expense_date) }, { header: "Office", cell: (e) => e.office_code }, { header: "Category", cell: (e) => humanize(e.category) },
    { header: "Description", cell: (e) => <div>{e.description}{e.decision_reason && <div className="text-xs text-slate-500">{e.decision_reason}</div>}</div> },
    { header: "Amount", cell: (e) => formatPKR(e.amount), className: "text-end" }, { header: "Paid by", cell: (e) => METHOD_LABELS[e.payment_method] || e.payment_method },
    { header: "Entered by", cell: (e) => e.entered_by_name }, { header: "Status", cell: (e) => <StatusBadge status={e.status} /> },
    { header: "", cell: (e) => (
      <div className="flex gap-1">
        <Button variant="ghost" onClick={() => setDlg({ receipt: e })}>Receipt</Button>
        {e.status === "pending" && can("expenses.approve") && (e.entered_by === user?.id && !user?.is_owner ? <span className="text-xs text-slate-500">Needs another approver</span> : <>
          <Button variant="secondary" onClick={async () => { if (await run(() => api(`/expenses/${e.id}/approve/`, { method: "POST", body: {} }), "Expense approved.")) reload(); }}>Approve</Button>
          <Button variant="ghost" onClick={() => setDlg({ reject: e })}>Reject</Button></>)}
      </div>) },
  ];
  const t = summary.data?.totals;

  return (
    <>
      <PageHeader title="Expenses" subtitle="Only approved expenses count toward totals. Rejected ones stay visible with their reason."
        actions={can("expenses.record") && <Button onClick={() => setDlg("new")}>New expense</Button>} />
      {t && <div className="mb-4 grid gap-3 sm:grid-cols-3">
        {(["approved", "pending", "rejected"] as const).map((s) => <Card key={s}><div className="text-xs uppercase text-slate-500">{humanize(s)}</div><div className="text-lg font-semibold">{formatPKR(t[s].total)}</div><div className="text-xs text-slate-500">{t[s].count} record(s)</div></Card>)}
      </div>}
      <div className="mb-3 flex flex-wrap gap-2">
        <FilterSelect label="Status" value={status} onChange={setStatus} options={["pending", "approved", "rejected"].map((s) => ({ value: s, label: humanize(s) }))} />
        <FilterSelect label="Category" value={category} onChange={setCategory} options={CATS.map((s) => ({ value: s, label: humanize(s) }))} />
      </div>
      {list.error && <Alert>{list.error.message}</Alert>}
      <DataTable columns={cols} rows={list.data?.results} loading={list.loading} rowKey={(e) => e.id} empty={{ title: "No expenses" }} />
      <Pagination page={page} count={list.data?.count || 0} onPage={setPage} />
      <ActionDialog open={dlg === "new"} onClose={() => setDlg(null)} title="New expense" confirmLabel="Submit for approval" confirmDisabled={!f.office || !f.description.trim() || !f.amount}
        onConfirm={async () => { const e = await api<R>("/expenses/", { method: "POST", body: { ...f, office: Number(f.office) } }); setDlg({ receipt: e }); reload(); }}>
        <SelectField label="Office" value={f.office} onChange={(e) => setF({ ...f, office: e.target.value })}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField>
        <div className="grid grid-cols-2 gap-3"><SelectField label="Category" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>{CATS.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</SelectField>
          <SelectField label="Paid by" value={f.payment_method} onChange={(e) => setF({ ...f, payment_method: e.target.value })}>{Object.entries(METHOD_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</SelectField></div>
        <div className="grid grid-cols-2 gap-3"><TextField label="Amount (PKR)" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /><TextField label="Date" type="date" value={f.expense_date} onChange={(e) => setF({ ...f, expense_date: e.target.value })} /></div>
        <TextAreaField label="Description" value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
      </ActionDialog>
      <ActionDialog open={typeof dlg === "object" && dlg !== null && "reject" in dlg} onClose={() => setDlg(null)} title="Reject expense" confirmLabel="Reject" danger reasonLabel="Reason"
        onConfirm={async (r) => { await api(`/expenses/${(dlg as any).reject.id}/reject/`, { method: "POST", body: { reason: r } }); setDlg(null); reload(); }} />
      <Modal open={typeof dlg === "object" && dlg !== null && "receipt" in dlg} onClose={() => setDlg(null)} title={`Receipt photos${dlg && typeof dlg === "object" && "receipt" in dlg ? ` · ${dlg.receipt.number}` : ""}`} footer={<Button onClick={() => setDlg(null)}>Done</Button>}>
        {dlg && typeof dlg === "object" && "receipt" in dlg && <ImageAttachments entityType="expense" entityId={dlg.receipt.id} kind="expense_receipt" canUpload={can("expenses.record")} />}
      </Modal>
    </>
  );
}
