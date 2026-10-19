"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page } from "@/lib/types";
import { formatDate, formatDateTime } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { Alert, Button, Card, DataTable, PageHeader, SelectField, StatusBadge, TextField, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;

export default function AttendancePage() {
  const { can } = useAuth();
  const run = useRun();
  const offices = useFetch<Page<Office>>("/offices/");
  const att = useFetch<Page<R>>("/attendance/");
  const leave = useFetch<Page<R>>("/leave/");
  const [office, setOffice] = useState("");
  const [dlg, setDlg] = useState<null | "leave" | { reject: R }>(null);
  const [lv, setLv] = useState({ start_date: "", end_date: "", reason: "" });
  const multi = (offices.data?.results.length || 0) > 1;

  const checkIn = async () => { if (await run(() => api("/attendance/check-in/", { method: "POST", body: office ? { office: Number(office) } : {} }), "Checked in.")) att.reload(); };
  const checkOut = async () => { if (await run(() => api("/attendance/check-out/", { method: "POST" }), "Checked out.")) att.reload(); };
  const decide = async (id: number, verb: "approve" | "reject" | "cancel", note = "") => { if (await run(() => api(`/leave/${id}/${verb}/`, { method: "POST", body: { note } }), "Leave updated.")) leave.reload(); };

  const cols: Column<R>[] = [
    { header: "Date", cell: (a) => formatDate(a.date) }, { header: "Employee", cell: (a) => a.employee_name }, { header: "Office", cell: (a) => a.office_code },
    { header: "In", cell: (a) => formatDateTime(a.check_in) }, { header: "Out", cell: (a) => formatDateTime(a.check_out) },
  ];
  const lcols: Column<R>[] = [
    { header: "Employee", cell: (l) => l.employee_name }, { header: "From", cell: (l) => formatDate(l.start_date) }, { header: "To", cell: (l) => formatDate(l.end_date) },
    { header: "Reason", cell: (l) => l.reason }, { header: "Status", cell: (l) => <StatusBadge status={l.status} /> },
    { header: "", cell: (l) => l.status === "pending" && can("leave.approve") ? <div className="flex gap-2"><Button variant="secondary" onClick={() => decide(l.id, "approve")}>Approve</Button><Button variant="ghost" onClick={() => setDlg({ reject: l })}>Reject</Button></div> : null },
  ];

  return (
    <>
      <PageHeader title="Attendance & leave" />
      <div className="space-y-5">
        <Card title="Today">
          <div className="flex flex-wrap items-end gap-3">
            {multi && <div className="w-56"><SelectField label="Office" value={office} onChange={(e) => setOffice(e.target.value)}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField></div>}
            <Button onClick={checkIn}>Check in</Button><Button variant="secondary" onClick={checkOut}>Check out</Button>
          </div>
          <p className="mt-2 text-xs text-slate-500">Managers can correct mistakes; every correction records who changed it, why, and the old and new times.</p>
        </Card>
        <Card title="Attendance records">
          {att.error && <Alert>{att.error.message}</Alert>}
          <DataTable columns={cols} rows={att.data?.results} loading={att.loading} rowKey={(a) => a.id} empty={{ title: "No attendance yet" }} />
        </Card>
        <Card title="Leave" actions={<Button variant="secondary" onClick={() => setDlg("leave")}>Request leave</Button>}>
          <DataTable columns={lcols} rows={leave.data?.results} loading={leave.loading} rowKey={(l) => l.id} empty={{ title: "No leave requests" }} />
        </Card>
      </div>
      <ActionDialog open={dlg === "leave"} onClose={() => setDlg(null)} title="Request leave" confirmLabel="Submit request" confirmDisabled={!lv.start_date || !lv.end_date || !lv.reason.trim()}
        onConfirm={async () => { await api("/leave/", { method: "POST", body: { ...lv, ...(office ? { office: Number(office) } : {}) } }); setDlg(null); leave.reload(); }}>
        <div className="grid grid-cols-2 gap-3"><TextField label="From" type="date" value={lv.start_date} onChange={(e) => setLv({ ...lv, start_date: e.target.value })} /><TextField label="To" type="date" value={lv.end_date} onChange={(e) => setLv({ ...lv, end_date: e.target.value })} /></div>
        <TextField label="Reason" value={lv.reason} onChange={(e) => setLv({ ...lv, reason: e.target.value })} />
      </ActionDialog>
      <ActionDialog open={typeof dlg === "object" && dlg !== null} onClose={() => setDlg(null)} title="Reject leave" confirmLabel="Reject" danger reasonLabel="Reason"
        onConfirm={async (r) => { await api(`/leave/${(dlg as any).reject.id}/reject/`, { method: "POST", body: { note: r } }); setDlg(null); leave.reload(); }} />
    </>
  );
}
