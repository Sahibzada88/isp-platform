"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Invoice, Page } from "@/lib/types";
import { formatDateTime, formatPKR, humanize } from "@/lib/format";
import { Alert, DataTable, FilterSelect, PageHeader, Pagination, StatusBadge, type Column } from "@/components/ui";

export default function InvoicesPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, status]);
  const { data, loading, error } = useFetch<Page<Invoice>>("/invoices/", { search: q, status, page });

  const columns: Column<Invoice>[] = [
    { header: "Invoice", cell: (i) => <Link href={`/invoices/${i.number}`} className="font-medium text-brand-700 hover:underline">{i.number}</Link> },
    { header: "Customer", cell: (i) => `${i.customer} · ${i.customer_name}` },
    { header: "Connection", cell: (i) => <Link href={`/connections/${i.connection}`} className="hover:underline">{i.connection}</Link> },
    { header: "Type", cell: (i) => humanize(i.kind) },
    { header: "Total", cell: (i) => formatPKR(i.total_amount), className: "text-end" },
    { header: "Issued", cell: (i) => formatDateTime(i.issued_at) },
    { header: "Status", cell: (i) => <StatusBadge status={i.status} /> },
  ];

  return (
    <>
      <PageHeader title="Invoices" subtitle="An invoice states what is owed. Payment is recorded separately." />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input type="search" aria-label="Search invoices" placeholder="Search invoice, customer or connection" value={search} onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-sm rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm" />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={["open", "payment_pending", "paid", "void"].map((s) => ({ value: s, label: humanize(s) }))} />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={columns} rows={data?.results} loading={loading} rowKey={(i) => i.id}
        empty={{ title: "No invoices found", hint: "Invoices are created from a connection page." }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
    </>
  );
}
