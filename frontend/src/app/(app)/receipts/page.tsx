"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { pdfUrl } from "@/lib/api";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Page, Receipt } from "@/lib/types";
import { formatDateTime, formatPKR, METHOD_LABELS } from "@/lib/format";
import { Alert, DataTable, PageHeader, Pagination, StatusBadge, type Column } from "@/components/ui";

export default function ReceiptsPage() {
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q]);
  const { data, loading, error } = useFetch<Page<Receipt>>("/receipts/", { search: q, page });

  const columns: Column<Receipt>[] = [
    { header: "Receipt", cell: (r) => <span className="font-medium">{r.number}</span> },
    { header: "Invoice", cell: (r) => <Link href={`/invoices/${r.invoice}`} className="text-brand-700 hover:underline">{r.invoice}</Link> },
    { header: "Customer", cell: (r) => r.customer },
    { header: "Method", cell: (r) => METHOD_LABELS[r.method] || r.method },
    { header: "Amount", cell: (r) => formatPKR(r.amount), className: "text-end" },
    { header: "Issued", cell: (r) => formatDateTime(r.issued_at) },
    { header: "Receipt status", cell: (r) => <StatusBadge status={r.status} /> },
    { header: "Payment", cell: (r) => <StatusBadge status={r.payment_status} /> },
    { header: "", cell: (r) => <a href={pdfUrl(`/receipts/${r.number}/pdf/`)} target="_blank" rel="noreferrer" className="text-brand-700 hover:underline">Print</a> },
  ];

  return (
    <>
      <PageHeader title="Receipts" subtitle="Receipts record payments. Invalidated receipts are kept, never deleted." />
      <div className="mb-3">
        <input type="search" aria-label="Search receipts" placeholder="Search receipt, invoice or customer" value={search} onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-sm rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm" />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={columns} rows={data?.results} loading={loading} rowKey={(r) => r.number} empty={{ title: "No receipts found" }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
    </>
  );
}
