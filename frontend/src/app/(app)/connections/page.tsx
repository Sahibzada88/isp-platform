"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Connection, Page } from "@/lib/types";
import { formatDate, humanize } from "@/lib/format";
import { Alert, DataTable, FilterSelect, PageHeader, Pagination, StatusBadge, type Column } from "@/components/ui";

const STATUSES = ["pending_installation", "pending_approval", "active", "expired", "suspended", "cancelled"];

export default function ConnectionsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, status]);
  const { data, loading, error } = useFetch<Page<Connection>>("/connections/", { search: q, status, page });

  const columns: Column<Connection>[] = [
    { header: "Connection", cell: (c) => <Link href={`/connections/${c.connection_id}`} className="font-medium text-brand-700 hover:underline">{c.connection_id}</Link> },
    { header: "Customer", cell: (c) => <Link href={`/customers/${c.customer}`} className="hover:underline">{c.customer}</Link> },
    { header: "Package", cell: (c) => c.package_code },
    { header: "Office", cell: (c) => c.office_code },
    { header: "Expires", cell: (c) => formatDate(c.expires_at) },
    { header: "Status", cell: (c) => <StatusBadge status={c.status} /> },
  ];

  return (
    <>
      <PageHeader title="Connections" subtitle="Each connection has its own package, expiry and status" />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input type="search" aria-label="Search connections" placeholder="Search connection, customer ID, name or phone" value={search} onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-sm rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm" />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={STATUSES.map((s) => ({ value: s, label: humanize(s) }))} />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={columns} rows={data?.results} loading={loading} rowKey={(c) => c.id}
        empty={{ title: "No connections found", hint: q || status ? "Try clearing the filters." : undefined }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
    </>
  );
}
