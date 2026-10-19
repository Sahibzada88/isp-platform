"use client";

import { useEffect, useState } from "react";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { Alert, DataTable, PageHeader, Pagination, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;

export default function AuditPage() {
  const [f, setF] = useState({ search: "", action_prefix: "", entity_type: "", date_from: "" });
  const [page, setPage] = useState(1);
  const q = useDebounced(f);
  useEffect(() => setPage(1), [q]);
  const { data, loading, error } = useFetch<Page<R>>("/audit-logs/", { ...q, page });
  const cols: Column<R>[] = [
    { header: "When", cell: (a) => formatDateTime(a.created_at) }, { header: "Who", cell: (a) => a.actor_label }, { header: "Action", cell: (a) => <span className="font-mono text-xs">{a.action}</span> },
    { header: "Record", cell: (a) => `${a.entity_type} #${a.entity_id}` }, { header: "Office", cell: (a) => a.office_code || "-" }, { header: "Reason", cell: (a) => a.reason || "-" },
    { header: "Details", cell: (a) => (a.before || a.after) ? <details><summary className="cursor-pointer text-xs text-brand-700">View</summary><pre className="mt-1 max-w-md overflow-x-auto whitespace-pre-wrap text-xs">{JSON.stringify({ before: a.before, after: a.after }, null, 1)}</pre></details> : "-" },
  ];
  const inp = "rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm";
  return (
    <>
      <PageHeader title="Audit log" subtitle="Read-only. Records cannot be edited or deleted, even by the owner." />
      <div className="mb-3 flex flex-wrap gap-2">
        <input className={inp} aria-label="Search" placeholder="Search reason / actor / record id" value={f.search} onChange={(e) => setF({ ...f, search: e.target.value })} />
        <input className={inp} aria-label="Action starts with" placeholder="Action prefix, e.g. payment." value={f.action_prefix} onChange={(e) => setF({ ...f, action_prefix: e.target.value })} />
        <input className={inp} aria-label="Record type" placeholder="Record type, e.g. billing.payment" value={f.entity_type} onChange={(e) => setF({ ...f, entity_type: e.target.value })} />
        <input className={inp} aria-label="From date" type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={cols} rows={data?.results} loading={loading} rowKey={(a) => a.id} empty={{ title: "No matching events" }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
    </>
  );
}
