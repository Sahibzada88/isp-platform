"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { formatDateTime, humanize } from "@/lib/format";
import { ActionDialog } from "@/components/actions";
import { CustomerPicker } from "@/components/customer-picker";
import { Alert, Button, DataTable, FilterSelect, PageHeader, Pagination, SelectField, StatusBadge, TextAreaField, TextField, useToast, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type C = Record<string, any>;
const CATEGORIES = ["internet_down", "slow_speed", "cable_damage", "router_issue", "payment_issue", "new_connection", "other"];
const PRIORITY_TONE: Record<string, string> = { urgent: "bg-red-100 text-red-800", high: "bg-orange-100 text-orange-800", medium: "bg-blue-100 text-blue-800", low: "bg-slate-100 text-slate-700" };

export default function ComplaintsPage() {
  const { can } = useAuth();
  const router = useRouter();
  const toast = useToast();
  const [search, setSearch] = useState(""); const [status, setStatus] = useState(""); const [priority, setPriority] = useState(""); const [page, setPage] = useState(1);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ customer: "", connection: "", category: "internet_down", description: "", priority: "medium", source: "phone" });
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, status, priority]);
  const { data, loading, error } = useFetch<Page<C>>("/complaints/", { search: q, status, priority, page });

  const columns: Column<C>[] = [
    { header: "Complaint", cell: (c) => <Link href={`/complaints/${c.number}`} className="font-medium text-brand-700 hover:underline">{c.number}</Link> },
    { header: "Customer", cell: (c) => `${c.customer} · ${c.customer_name}` },
    { header: "Category", cell: (c) => humanize(c.category) },
    { header: "Priority", cell: (c) => <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${PRIORITY_TONE[c.priority]}`}>{humanize(c.priority)}</span> },
    { header: "Assigned to", cell: (c) => c.assigned_to_name || <span className="text-slate-400">Unassigned</span> },
    { header: "Opened", cell: (c) => formatDateTime(c.created_at) },
    { header: "Status", cell: (c) => <StatusBadge status={c.status} /> },
  ];
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <>
      <PageHeader title="Complaints" subtitle="Reopening keeps the original record, its notes and history"
        actions={can("complaints.create") && <Button onClick={() => setCreating(true)}>Log complaint</Button>} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input type="search" aria-label="Search complaints" placeholder="Search number, customer or text" value={search} onChange={(e) => setSearch(e.target.value)} className="w-full max-w-xs rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm" />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={["new", "assigned", "in_progress", "waiting", "resolved", "closed", "reopened"].map((s) => ({ value: s, label: humanize(s) }))} />
        <FilterSelect label="Priority" value={priority} onChange={setPriority} options={["urgent", "high", "medium", "low"].map((s) => ({ value: s, label: humanize(s) }))} />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={columns} rows={data?.results} loading={loading} rowKey={(c) => c.id} empty={{ title: "No complaints found" }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
      <ActionDialog open={creating} onClose={() => setCreating(false)} title="Log a complaint" confirmLabel="Create complaint" confirmDisabled={!form.customer.trim() || !form.description.trim()}
        onConfirm={async () => {
          const c = await api<C>("/complaints/", { method: "POST", body: { ...form, customer: form.customer.trim().toUpperCase(), connection: form.connection.trim().toUpperCase() } });
          setCreating(false); toast("success", `Complaint ${c.number} logged.`); router.push(`/complaints/${c.number}`);
        }}>
        <CustomerPicker onPick={(id) => setForm((f) => ({ ...f, customer: id }))} />
        <TextField label="Connection (optional)" placeholder="CON-00001" value={form.connection} onChange={set("connection")} />
        <SelectField label="Category" value={form.category} onChange={set("category")}>{CATEGORIES.map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</SelectField>
        <div className="grid grid-cols-2 gap-3">
          <SelectField label="Priority" value={form.priority} onChange={set("priority")}>{["low", "medium", "high", "urgent"].map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</SelectField>
          <SelectField label="Received via" value={form.source} onChange={set("source")}>{["office", "phone", "whatsapp"].map((c) => <option key={c} value={c}>{humanize(c)}</option>)}</SelectField>
        </div>
        <TextAreaField label="Description" value={form.description} onChange={set("description")} />
      </ActionDialog>
    </>
  );
}
