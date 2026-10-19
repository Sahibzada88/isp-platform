"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, pdfUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Page, Payment } from "@/lib/types";
import { formatDateTime, formatPKR, humanize, METHOD_LABELS } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { ImageAttachments } from "@/components/image-upload";
import { Alert, Button, DataTable, FilterSelect, Modal, PageHeader, Pagination, StatusBadge, type Column } from "@/components/ui";

export default function PaymentsPage() {
  const { user, can } = useAuth();
  const run = useRun();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [method, setMethod] = useState("");
  const [page, setPage] = useState(1);
  const [rejecting, setRejecting] = useState<Payment | null>(null);
  const [evidence, setEvidence] = useState<Payment | null>(null);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, status, method]);
  const { data, loading, error, reload } = useFetch<Page<Payment>>("/payments/", { search: q, status, method, page });

  const verify = async (p: Payment) => { if (await run(() => api(`/payments/${p.id}/verify/`, { method: "POST" }), "Payment verified.")) reload(); };

  const columns: Column<Payment>[] = [
    { header: "Invoice", cell: (p) => <Link href={`/invoices/${p.invoice}`} className="font-medium text-brand-700 hover:underline">{p.invoice}</Link> },
    { header: "Customer", cell: (p) => p.customer },
    { header: "Method", cell: (p) => METHOD_LABELS[p.method] || p.method },
    { header: "Reference", cell: (p) => p.reference || "-" },
    { header: "Amount", cell: (p) => formatPKR(p.amount), className: "text-end" },
    { header: "Entered", cell: (p) => formatDateTime(p.entered_at) },
    { header: "Receipt", cell: (p) => p.receipt ? <a href={pdfUrl(`/receipts/${p.receipt}/pdf/`)} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">{p.receipt}</a> : "-" },
    { header: "Status", cell: (p) => <StatusBadge status={p.status} /> },
    { header: "Evidence", cell: (p) => <Button variant="ghost" onClick={() => setEvidence(p)}>Photos</Button> },
    { header: "", cell: (p) => p.status === "pending_verification" && can("payments.verify") ? (
      p.entered_by === user?.id && !user?.is_owner
        ? <span className="text-xs text-slate-500">Needs another verifier</span>
        : <div className="flex gap-2"><Button variant="secondary" onClick={() => verify(p)}>Verify</Button><Button variant="ghost" onClick={() => setRejecting(p)}>Reject</Button></div>
    ) : null },
  ];

  return (
    <>
      <PageHeader title="Payments" subtitle="Digital payments stay pending until someone else verifies them" />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input type="search" aria-label="Search payments" placeholder="Search reference, invoice or customer" value={search} onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-sm rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm" />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={["pending_verification", "confirmed", "rejected", "reversed"].map((s) => ({ value: s, label: humanize(s) }))} />
        <FilterSelect label="Method" value={method} onChange={setMethod} options={Object.entries(METHOD_LABELS).map(([value, label]) => ({ value, label }))} />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={columns} rows={data?.results} loading={loading} rowKey={(p) => p.id} empty={{ title: "No payments found" }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
      <Modal open={!!evidence} onClose={() => setEvidence(null)} title={`Payment evidence${evidence ? ` · ${evidence.invoice}` : ""}`} footer={<Button onClick={() => setEvidence(null)}>Done</Button>}>
        {evidence && <ImageAttachments entityType="payment" entityId={evidence.id} kind="payment_evidence" canUpload={can("payments.record")} />}
      </Modal>
      <ActionDialog open={!!rejecting} onClose={() => setRejecting(null)} title="Reject payment" confirmLabel="Reject payment" danger reasonLabel="Reason (e.g. not found in statement)"
        description="The receipt is marked invalid and the invoice becomes payable again. The record is kept."
        onConfirm={async (reason) => { await api(`/payments/${rejecting!.id}/reject/`, { method: "POST", body: { reason } }); setRejecting(null); reload(); }} />
    </>
  );
}
