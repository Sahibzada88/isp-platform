"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page } from "@/lib/types";
import { humanize } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import Link from "next/link";
import { Alert, Button, DataTable, Modal, PageHeader, SelectField, StatusBadge, TextField, useToast, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;
const ROLES = ["manager", "office_staff", "technician", "labour", "collector"];

export default function EmployeesPage() {
  const { can } = useAuth();
  const run = useRun();
  const toast = useToast();
  const list = useFetch<Page<R>>("/employees/");
  const offices = useFetch<Page<Office>>("/offices/");
  const catalog = useFetch<{ code: string; label: string }[]>("/permissions/catalog/");
  const [dlg, setDlg] = useState<null | { k: "new" } | { k: "disable" | "perms"; emp: R }>(null);
  const [f, setF] = useState<Record<string, string>>({ role: "office_staff" });
  const [office, setOffice] = useState("");
  const [granted, setGranted] = useState<Set<string>>(new Set());
  const grants = useFetch<any[]>(dlg?.k === "perms" ? `/employees/${dlg.emp.id}/grants/` : null);
  const set = (k: string) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const close = () => { setDlg(null); setF({ role: "office_staff" }); setOffice(""); };
  const loadOffice = (code: string) => { setOffice(code); setGranted(new Set((grants.data || []).filter((g) => (g.office__code || "*") === code).map((g) => g.code))); };

  const cols: Column<R>[] = [
    { header: "Code", cell: (e) => e.employee_code || "-" }, { header: "Username", cell: (e) => e.username }, { header: "Name", cell: (e) => `${e.first_name} ${e.last_name}`.trim() || "-" },
    { header: "Role", cell: (e) => humanize(e.role) }, { header: "Offices", cell: (e) => e.offices.join(", ") || "-" }, { header: "Status", cell: (e) => <StatusBadge status={e.is_active ? "active" : "cancelled"} /> },
    { header: "", cell: (e) => e.is_active ? (
      <div className="flex gap-1">
        {can("permissions.manage") && <Button variant="secondary" onClick={() => setDlg({ k: "perms", emp: e })}>Permissions</Button>}
        {can("employees.manage") && <Button variant="ghost" onClick={async () => { const r = await run(async () => { const x = await api<R>(`/employees/${e.id}/reset-password/`, { method: "POST" }); window.prompt("Temporary password (shown once). Give it to the employee:", x.temporary_password); }, "Password reset; sessions ended."); void r; }}>Reset password</Button>}
        {can("employees.manage") && <Button variant="ghost" onClick={() => setDlg({ k: "disable", emp: e })}>Disable</Button>}
      </div>) : null },
  ];
  const officeCodes = Array.from(new Set((offices.data?.results || []).map((o) => o.code)));

  return (
    <>
      <PageHeader title="Employees" subtitle="Roles are templates; the permissions you set per office decide what each person can do"
        actions={can("employees.manage") && <Button onClick={() => setDlg({ k: "new" })}>New employee</Button>} />
      {offices.data && offices.data.results.length === 0 && <Alert kind="info">There are no offices yet. <Link href="/admin/offices" className="underline">Create an office first</Link>; every employee works in at least one office.</Alert>}
      {list.error && <Alert>{list.error.message}</Alert>}
      <DataTable columns={cols} rows={list.data?.results} loading={list.loading} rowKey={(e) => e.id} empty={{ title: "No employees" }} />
      <ActionDialog open={dlg?.k === "new"} onClose={close} title="New employee" confirmLabel="Create" confirmDisabled={!f.username || !f.password || !f.office}
        onConfirm={async () => { await api("/employees/", { method: "POST", body: { username: f.username, password: f.password, role: f.role, first_name: f.first || "", last_name: f.last || "", phone: f.phone || "", offices: [Number(f.office)] } }); close(); list.reload(); toast("success", "Employee created with the role's default permissions."); }}>
        <div className="grid grid-cols-2 gap-3"><TextField label="Username" value={f.username || ""} onChange={set("username")} /><TextField label="Temporary password" type="password" value={f.password || ""} onChange={set("password")} hint="Min. 10 characters." /></div>
        <div className="grid grid-cols-2 gap-3"><TextField label="First name" value={f.first || ""} onChange={set("first")} /><TextField label="Last name" value={f.last || ""} onChange={set("last")} /></div>
        <div className="grid grid-cols-2 gap-3"><SelectField label="Role" value={f.role} onChange={set("role")}>{ROLES.map((r) => <option key={r} value={r}>{humanize(r)}</option>)}</SelectField>
          <SelectField label="Office" value={f.office || ""} onChange={set("office")}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField></div>
      </ActionDialog>
      <ActionDialog open={dlg?.k === "disable"} onClose={close} title={`Disable ${dlg && "emp" in dlg ? dlg.emp.username : ""}`} confirmLabel="Disable account" danger reasonLabel="Reason"
        description="Ends all sessions immediately. Pending complaints and tasks are handed to the colleague you choose, or returned to managers if you choose nobody."
        onConfirm={async (reason) => { await api(`/employees/${(dlg as any).emp.id}/disable/`, { method: "POST", body: { reason, ...(f.handover ? { reassign_to: Number(f.handover) } : {}) } }); close(); list.reload(); toast("success", "Account disabled."); }}>
        <SelectField label="Hand pending work to" value={f.handover || ""} onChange={set("handover")}><option value="">Nobody (return to managers)</option>{list.data?.results.filter((e) => e.is_active && dlg && "emp" in dlg && e.id !== dlg.emp.id).map((e) => <option key={e.id} value={e.id}>{e.username}</option>)}</SelectField>
      </ActionDialog>
      <Modal open={dlg?.k === "perms"} onClose={close} title={`Permissions: ${dlg && "emp" in dlg ? dlg.emp.username : ""}`}
        footer={<><Button variant="secondary" onClick={close}>Close</Button><Button disabled={!office} onClick={async () => { await run(() => api(`/employees/${(dlg as any).emp.id}/permissions/`, { method: "POST", body: { office: office === "*" ? null : offices.data?.results.find((o) => o.code === office)?.id, permissions: Array.from(granted) } }), "Permissions saved and logged."); }}>Save for this office</Button></>}>
        {grants.loading ? <p className="text-sm text-slate-500">Loading…</p> : <>
          <SelectField label="Office" value={office} onChange={(e) => loadOffice(e.target.value)}><option value="">Select…</option>{officeCodes.map((c) => <option key={c} value={c}>{c}</option>)}<option value="*">Global permissions</option></SelectField>
          {office && <div className="max-h-72 overflow-y-auto rounded-md border border-slate-200 p-2">{catalog.data?.filter((p) => (office === "*") === ["offices.create", "permissions.manage", "audit.view", "system.manage", "imports.manage"].includes(p.code)).map((p) => (
            <label key={p.code} className="flex items-center gap-2 py-1 text-sm"><input type="checkbox" checked={granted.has(p.code)} onChange={(e) => setGranted((g) => { const n = new Set(g); if (e.target.checked) n.add(p.code); else n.delete(p.code); return n; })} />{p.label} <span className="text-xs text-slate-400">{p.code}</span></label>))}</div>}
        </>}
      </Modal>
    </>
  );
}
