"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { api, pdfUrl } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Invoice, Page, Payment } from "@/lib/types";
import { formatDateTime, formatPKR, humanize, METHOD_LABELS } from "@/lib/format";
import { ActionDialog } from "@/components/actions";
import { Alert, Button, Card, DataTable, KeyValue, PageHeader, SelectField, Spinner, StatusBadge, TextField, useToast, type Column } from "@/components/ui";

export default function InvoiceDetailPage() {
  const { number } = useParams<{ number: string }>();
  const router = useRouter();
  const { can } = useAuth();
  const toast = useToast();
  const inv = useFetch<Invoice>(`/invoices/${number}/`);
  const pays = useFetch<Page<Payment>>("/payments/", { search: number });
  const [dialog, setDialog] = useState<null | "pay" | "void" | "reissue">(null);
  const [method, setMethod] = useState("cash");
  const [reference, setReference] = useState("");
  const [paidAt, setPaidAt] = useState("");

  if (inv.loading && !inv.data) return <Spinner className="h-6 w-6 text-brand-600" />;
  if (inv.error) return <Alert>{inv.error.status === 404 ? "Invoice not found." : inv.error.message}</Alert>;
  const i = inv.data!;
  const isOpen = i.status === "open";
  const digital = method !== "cash";

  const reload = () => { inv.reload(); pays.reload(); };
  const payColumns: Column<Payment>[] = [
    { header: "Method", cell: (p) => METHOD_LABELS[p.method] || p.method },
    { header: "Reference", cell: (p) => p.reference || "-" },
    { header: "Amount", cell: (p) => formatPKR(p.amount), className: "text-end" },
    { header: "Receipt", cell: (p) => p.receipt ? <a href={pdfUrl(`/receipts/${p.receipt}/pdf/`)} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">{p.receipt}</a> : "-" },
    { header: "Entered", cell: (p) => formatDateTime(p.entered_at) },
    { header: "Status", cell: (p) => <StatusBadge status={p.status} /> },
  ];

  return (
    <>
      <PageHeader title={i.number} subtitle={`${i.customer_name} · ${i.customer}`}
        actions={<>
          <a href={pdfUrl(`/invoices/${i.number}/pdf/`)} target="_blank" rel="noreferrer" className="inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50">Print / PDF</a>
          {isOpen && can("payments.record") && <Button onClick={() => setDialog("pay")}>Record payment</Button>}
          {isOpen && can("billing.void_invoice") && <Button variant="secondary" onClick={() => setDialog("reissue")}>Void &amp; reissue</Button>}
          {isOpen && can("billing.void_invoice") && <Button variant="danger" onClick={() => setDialog("void")}>Void</Button>}
        </>} />
      <div className="space-y-5">
        {i.status === "payment_pending" && <Alert kind="info">A digital payment is awaiting verification. Staff with the verify permission can confirm it on the Payments page.</Alert>}
        {i.status === "void" && <Alert kind="info">Voided{i.void_reason ? `: ${i.void_reason}` : "."} This invoice is kept for the record.</Alert>}
        <Card title="Invoice">
          <KeyValue items={[
            { label: "Status", value: <StatusBadge status={i.status} /> },
            { label: "Type", value: humanize(i.kind) },
            { label: "Connection", value: <Link href={`/connections/${i.connection}`} className="text-brand-700 hover:underline">{i.connection}</Link> },
            { label: "Issued", value: formatDateTime(i.issued_at) },
            { label: "Package (snapshot)", value: `${i.package_name} · ${i.speed_mbps} Mbps · ${i.term_days} days` },
            { label: "Replaces", value: i.supersedes || "-" },
          ]} />
          <table className="mt-5 w-full text-sm">
            <thead><tr className="border-b border-slate-200 text-start text-xs uppercase text-slate-500"><th className="py-2 font-medium">Description</th><th className="py-2 text-end font-medium">Amount</th></tr></thead>
            <tbody>
              {i.lines.map((l, idx) => <tr key={idx} className="border-b border-slate-100"><td className="py-2">{l.description}</td><td className="py-2 text-end">{formatPKR(l.amount)}</td></tr>)}
              <tr><td className="py-3 font-semibold">Total payable</td><td className="py-3 text-end text-base font-semibold">{formatPKR(i.total_amount)}</td></tr>
            </tbody>
          </table>
        </Card>
        <Card title="Payments">
          <DataTable columns={payColumns} rows={pays.data?.results.filter((p) => p.invoice === i.number)} loading={pays.loading} rowKey={(p) => p.id} empty={{ title: "No payments recorded" }} />
        </Card>
      </div>

      <ActionDialog open={dialog === "pay"} onClose={() => setDialog(null)} title="Record payment" confirmLabel="Record payment"
        description={`Full payment of ${formatPKR(i.total_amount)} is required. Partial payments are not accepted.`}
        confirmDisabled={digital && !reference.trim()}
        onConfirm={async () => {
          await api("/payments/", { method: "POST", body: { invoice: i.number, method, amount: i.total_amount, reference: digital ? reference : "", ...(digital && paidAt ? { paid_at: new Date(paidAt).toISOString() } : {}) } });
          setDialog(null); setReference(""); setPaidAt(""); reload();
          toast("success", digital ? "Payment recorded. It stays pending until verified." : "Cash payment recorded and receipt issued.");
        }}>
        <SelectField label="Payment method" value={method} onChange={(e) => setMethod(e.target.value)}>
          {Object.entries(METHOD_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </SelectField>
        <TextField label="Amount (PKR)" value={i.total_amount} readOnly />
        {digital && <>
          <TextField label="Transaction reference" value={reference} onChange={(e) => setReference(e.target.value)} hint="Duplicate references are blocked." />
          <TextField label="Transaction date & time" type="datetime-local" value={paidAt} onChange={(e) => setPaidAt(e.target.value)} hint="Leave empty for now." />
        </>}
      </ActionDialog>
      <ActionDialog open={dialog === "void"} onClose={() => setDialog(null)} title="Void invoice" confirmLabel="Void invoice" danger reasonLabel="Reason"
        description="Only unpaid invoices can be voided. The invoice is kept and marked void."
        onConfirm={async (reason) => { await api(`/invoices/${i.number}/void/`, { method: "POST", body: { reason } }); setDialog(null); reload(); toast("success", "Invoice voided."); }} />
      <ActionDialog open={dialog === "reissue"} onClose={() => setDialog(null)} title="Void and reissue" confirmLabel="Void & reissue" reasonLabel="Reason"
        description="Voids this invoice and issues a new one using the current package price and fee rules."
        onConfirm={async (reason) => { const n = await api<Invoice>(`/invoices/${i.number}/reissue/`, { method: "POST", body: { reason } }); setDialog(null); toast("success", `Reissued as ${n.number}.`); router.push(`/invoices/${n.number}`); }} />
    </>
  );
}
