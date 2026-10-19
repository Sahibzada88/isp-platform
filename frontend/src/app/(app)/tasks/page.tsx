"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page } from "@/lib/types";
import { formatDate, humanize } from "@/lib/format";
import { ActionDialog } from "@/components/actions";
import { Alert, Button, DataTable, FilterSelect, PageHeader, Pagination, SelectField, StatusBadge, TextAreaField, TextField, useToast, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type T = Record<string, any>;
const KINDS = ["complaint", "installation", "cable_repair", "equipment_delivery", "stock_delivery", "office_work", "other"];

export default function TasksPage() {
  const { can } = useAuth();
  const toast = useToast();
  const [status, setStatus] = useState(""); const [mine, setMine] = useState(""); const [page, setPage] = useState(1);
  const [dlg, setDlg] = useState<null | { kind: "new" } | { kind: "complete" | "cancel"; task: T }>(null);
  const [form, setForm] = useState({ office: "", title: "", description: "", kind: "other", priority: "medium", assigned_to: "", due_date: "" });
  useEffect(() => setPage(1), [status, mine]);
  const { data, loading, error, reload } = useFetch<Page<T>>("/tasks/", { status, mine, page });
  const offices = useFetch<Page<Office>>(dlg?.kind === "new" ? "/offices/" : null);
  const people = useFetch<any[]>(dlg?.kind === "new" && form.office ? "/employees/assignable/" : null, { office: form.office, for: "tasks" });

  const act = async (t: T, verb: string, body?: object) => { try { await api(`/tasks/${t.id}/${verb}/`, { method: "POST", body }); reload(); toast("success", "Task updated."); } catch (e: any) { toast("error", e.message); } };
  const columns: Column<T>[] = [
    { header: "Task", cell: (t) => <div><div className="font-medium">{t.title}</div>{t.complaint_number && <div className="text-xs text-slate-500">{t.complaint_number}</div>}</div> },
    { header: "Type", cell: (t) => humanize(t.kind) }, { header: "Priority", cell: (t) => humanize(t.priority) },
    { header: "Assigned to", cell: (t) => t.assigned_to_name || "-" }, { header: "Due", cell: (t) => formatDate(t.due_date) },
    { header: "Status", cell: (t) => <StatusBadge status={t.status} /> },
    { header: "", cell: (t) => (
      <div className="flex gap-2">
        {t.status === "todo" && <Button variant="secondary" onClick={() => act(t, "start")}>Start</Button>}
        {["todo", "in_progress"].includes(t.status) && <Button variant="secondary" onClick={() => setDlg({ kind: "complete", task: t })}>Complete</Button>}
        {["todo", "in_progress"].includes(t.status) && can("tasks.manage") && <Button variant="ghost" onClick={() => setDlg({ kind: "cancel", task: t })}>Cancel</Button>}
      </div>) },
  ];

  return (
    <>
      <PageHeader title="Tasks" subtitle="Installations, repairs, deliveries and office work" actions={can("tasks.manage") && <Button onClick={() => setDlg({ kind: "new" })}>New task</Button>} />
      <div className="mb-3 flex flex-wrap gap-2">
        <FilterSelect label="Status" value={status} onChange={setStatus} options={["todo", "in_progress", "done", "cancelled"].map((s) => ({ value: s, label: humanize(s) }))} />
        <FilterSelect label="Show" value={mine} onChange={setMine} options={[{ value: "true", label: "Only mine" }]} />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={columns} rows={data?.results} loading={loading} rowKey={(t) => t.id} empty={{ title: "No tasks" }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
      <ActionDialog open={dlg?.kind === "new"} onClose={() => setDlg(null)} title="New task" confirmLabel="Create task" confirmDisabled={!form.office || !form.title.trim()}
        onConfirm={async () => { await api("/tasks/", { method: "POST", body: { ...form, office: Number(form.office), assigned_to: form.assigned_to ? Number(form.assigned_to) : null, due_date: form.due_date || null } }); setDlg(null); reload(); toast("success", "Task created."); }}>
        <SelectField label="Office" value={form.office} onChange={(e) => setForm({ ...form, office: e.target.value, assigned_to: "" })}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField>
        <TextField label="Title" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        <div className="grid grid-cols-2 gap-3">
          <SelectField label="Type" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>{KINDS.map((k) => <option key={k} value={k}>{humanize(k)}</option>)}</SelectField>
          <SelectField label="Priority" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>{["low", "medium", "high", "urgent"].map((k) => <option key={k} value={k}>{humanize(k)}</option>)}</SelectField>
        </div>
        <SelectField label="Assign to" value={form.assigned_to} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })}><option value="">Unassigned</option>{people.data?.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</SelectField>
        <TextField label="Due date" type="date" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
        <TextAreaField label="Description" value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
      </ActionDialog>
      <ActionDialog open={dlg?.kind === "complete"} onClose={() => setDlg(null)} title="Complete task" confirmLabel="Mark done" reasonLabel="Completion notes"
        onConfirm={async (n) => { const t = (dlg as any).task; await api(`/tasks/${t.id}/complete/`, { method: "POST", body: { notes: n } }); setDlg(null); reload(); toast("success", "Task completed."); }} />
      <ActionDialog open={dlg?.kind === "cancel"} onClose={() => setDlg(null)} title="Cancel task" confirmLabel="Cancel task" danger reasonLabel="Reason"
        onConfirm={async (r) => { const t = (dlg as any).task; await api(`/tasks/${t.id}/cancel/`, { method: "POST", body: { reason: r } }); setDlg(null); reload(); }} />
    </>
  );
}
