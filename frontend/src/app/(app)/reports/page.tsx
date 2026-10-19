"use client";

import { useEffect, useState } from "react";
import { buildQuery } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page } from "@/lib/types";
import { Alert, Card, PageHeader, Pagination, SelectField, Spinner, TextField } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
interface Rep { name: string; title: string; exportable: boolean }

export default function ReportsPage() {
  const { can } = useAuth();
  const reps = useFetch<Rep[]>("/reports/");
  const offices = useFetch<Page<Office>>("/offices/");
  const [name, setName] = useState("");
  const [f, setF] = useState({ office: "", date_from: "", date_to: "", status: "", fy: "" });
  const [page, setPage] = useState(1);
  useEffect(() => { if (!name && reps.data?.length) setName(reps.data[0].name); }, [reps.data, name]);
  useEffect(() => setPage(1), [name, f.office, f.date_from, f.date_to, f.status, f.fy]);
  const view = useFetch<any>(name ? `/reports/${name}/` : null, { ...f, page, page_size: 25 });
  const current = reps.data?.find((r) => r.name === name);
  const dl = (fmt: string) => `/api/v1/reports/${name}/${buildQuery({ ...f, fmt })}`;

  return (
    <>
      <PageHeader title="Reports" subtitle="Filtered on the server; downloads respect your office and permissions" />
      {reps.error && <Alert>{reps.error.message}</Alert>}
      <Card className="mb-4">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-6">
          <SelectField label="Report" value={name} onChange={(e) => setName(e.target.value)}>{reps.data?.map((r) => <option key={r.name} value={r.name}>{r.title}</option>)}</SelectField>
          <SelectField label="Office" value={f.office} onChange={(e) => setF({ ...f, office: e.target.value })}><option value="">All my offices</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField>
          <TextField label="From" type="date" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
          <TextField label="To" type="date" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} />
          <TextField label="Status (optional)" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })} />
          <TextField label="Financial year (start year)" inputMode="numeric" placeholder="2026" value={f.fy} onChange={(e) => setF({ ...f, fy: e.target.value })} hint="Overrides the dates." />
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-2">
          {current?.exportable && can("reports.export") ? (["csv", "xlsx", "pdf"] as const).map((fmt) => <a key={fmt} href={dl(fmt)} className="rounded-md border border-slate-300 bg-white px-3 py-2 text-sm font-medium hover:bg-slate-50">Download {fmt.toUpperCase()}</a>)
            : <span className="text-xs text-slate-500">You can preview this report. Downloading needs the export permission.</span>}
          <span className="text-xs text-slate-500">Large files: narrow the filters if a download is refused.</span>
        </div>
      </Card>
      {view.error && <Alert>{view.error.message}</Alert>}
      {view.loading && !view.data ? <Spinner className="h-6 w-6 text-brand-600" /> : view.data && (
        <>
          <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
            <table className="min-w-full text-start text-sm">
              <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500"><tr>{view.data.columns.map((c: string) => <th key={c} scope="col" className="px-4 py-2.5 font-medium">{c}</th>)}</tr></thead>
              <tbody>{view.data.results.map((r: string[], i: number) => <tr key={i} className="border-t border-slate-100">{r.map((c, j) => <td key={j} className="px-4 py-2">{c}</td>)}</tr>)}</tbody>
            </table>
            {view.data.results.length === 0 && <p className="px-4 py-8 text-center text-sm text-slate-500">No rows for these filters.</p>}
          </div>
          <Pagination page={page} count={view.data.count} pageSize={25} onPage={setPage} />
        </>
      )}
    </>
  );
}
