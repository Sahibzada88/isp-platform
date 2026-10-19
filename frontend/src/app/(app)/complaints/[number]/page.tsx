"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import { formatDateTime, humanize } from "@/lib/format";
import { ActionDialog } from "@/components/actions";
import { ImageAttachments } from "@/components/image-upload";
import { Alert, Button, Card, KeyValue, PageHeader, SelectField, Spinner, StatusBadge, TextAreaField, TextField, useToast } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Dlg = null | "assign" | "priority" | "wait" | "work" | "resolve" | "close" | "reopen";

export default function ComplaintDetail() {
  const { number } = useParams<{ number: string }>();
  const { user, can } = useAuth();
  const toast = useToast();
  const c = useFetch<Record<string, any>>(`/complaints/${number}/`);
  const [dlg, setDlg] = useState<Dlg>(null);
  const [tech, setTech] = useState(""); const [prio, setPrio] = useState("medium");
  const [log, setLog] = useState({ notes: "", materials_used: "", equipment_used: "" });
  const people = useFetch<any[]>(dlg === "assign" && c.data ? "/employees/assignable/" : null, { office: c.data?.office, for: "complaints" });

  if (c.loading && !c.data) return <Spinner className="h-6 w-6 text-brand-600" />;
  if (c.error) return <Alert>{c.error.status === 404 ? "Complaint not found." : c.error.message}</Alert>;
  const d = c.data!;
  const mine = d.assigned_to === user?.id;
  const manager = can("complaints.assign");
  const canWork = (mine || manager) && can("complaints.work");
  const post = async (path: string, body?: object) => { await api(`/complaints/${number}/${path}/`, { method: "POST", body }); setDlg(null); c.reload(); toast("success", "Updated."); };
  const st = d.status as string;
  const btns = [
    manager && !["resolved", "closed"].includes(st) && <Button key="a" onClick={() => setDlg("assign")}>{d.assigned_to ? "Reassign" : "Assign technician"}</Button>,
    canWork && ["assigned", "reopened"].includes(st) && <Button key="s" onClick={async () => { await post("start"); }}>Start work</Button>,
    canWork && st === "in_progress" && <Button key="w" variant="secondary" onClick={() => setDlg("wait")}>Put on hold</Button>,
    canWork && st === "waiting" && <Button key="r" variant="secondary" onClick={async () => { await post("resume"); }}>Resume</Button>,
    canWork && ["assigned", "in_progress", "waiting", "reopened"].includes(st) && <Button key="l" variant="secondary" onClick={() => setDlg("work")}>Add work note</Button>,
    canWork && st === "in_progress" && <Button key="v" onClick={() => setDlg("resolve")}>Resolve</Button>,
    can("complaints.close") && st === "resolved" && <Button key="c" onClick={() => setDlg("close")}>Close</Button>,
    can("complaints.reopen") && ["resolved", "closed"].includes(st) && <Button key="o" variant="danger" onClick={() => setDlg("reopen")}>Reopen</Button>,
    manager && <Button key="p" variant="ghost" onClick={() => { setPrio(d.priority); setDlg("priority"); }}>Change priority</Button>,
  ].filter(Boolean);

  return (
    <>
      <PageHeader title={d.number} subtitle={`${d.customer_name} · ${d.customer}${d.connection ? ` · ${d.connection}` : ""}`} actions={<StatusBadge status={st} />} />
      <div className="space-y-5">
        <Card title="Details"><KeyValue items={[
          { label: "Category", value: humanize(d.category) }, { label: "Priority", value: humanize(d.priority) },
          { label: "Assigned to", value: d.assigned_to_name || "Unassigned" }, { label: "Received via", value: humanize(d.source) },
          { label: "Opened", value: formatDateTime(d.created_at) }, { label: "Reopened", value: `${d.reopen_count} time(s)` },
          { label: "Description", value: d.description },
          ...(d.waiting_reason ? [{ label: "On hold because", value: d.waiting_reason }] : []),
          ...(d.resolution_notes ? [{ label: "Resolution", value: d.resolution_notes }] : []),
        ]} /></Card>
        {btns.length > 0 && <Card title="Actions"><div className="flex flex-wrap gap-2">{btns}</div></Card>}
        <Card title="Photos & attachments"><ImageAttachments entityType="complaint" entityId={d.id} kind="complaint_attachment" canUpload={canWork || manager} /></Card>
        <Card title="Work log">
          {d.work_logs.length === 0 ? <p className="text-sm text-slate-500">No work logged yet.</p> : (
            <ul className="space-y-3">{d.work_logs.map((w: any) => (
              <li key={w.id} className="rounded-md border border-slate-100 p-3 text-sm"><div className="text-xs text-slate-500">{w.author} · {formatDateTime(w.created_at)}</div><p className="mt-1">{w.notes}</p>
                {w.materials_used && <p className="mt-1 text-xs text-slate-600">Materials: {w.materials_used}</p>}{w.equipment_used && <p className="text-xs text-slate-600">Equipment: {w.equipment_used}</p>}</li>
            ))}</ul>)}
        </Card>
        <Card title="History">
          <ol className="space-y-1.5 text-sm">{d.events.map((e: any) => (
            <li key={e.id} className="flex flex-wrap gap-x-2 text-slate-700"><span className="text-xs text-slate-500">{formatDateTime(e.created_at)}</span><span className="font-medium">{humanize(e.kind)}</span>
              {(e.from_value || e.to_value) && <span>{e.from_value && `${humanize(e.from_value)} → `}{humanize(e.to_value)}</span>}<span className="text-slate-500">by {e.actor_label}</span>{e.note && <span className="text-slate-600">— {e.note}</span>}</li>
          ))}</ol>
        </Card>
        <Link href="/complaints" className="text-sm text-brand-700 hover:underline">← All complaints</Link>
      </div>
      <ActionDialog open={dlg === "assign"} onClose={() => setDlg(null)} title="Assign technician" confirmLabel="Assign" confirmDisabled={!tech} onConfirm={async () => { await post("assign", { technician: Number(tech) }); }}>
        <SelectField label="Technician" value={tech} onChange={(e) => setTech(e.target.value)}><option value="">Select…</option>{people.data?.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.role.replace("_", " ")})</option>)}</SelectField>
      </ActionDialog>
      <ActionDialog open={dlg === "priority"} onClose={() => setDlg(null)} title="Change priority" confirmLabel="Save" onConfirm={async () => { await post("priority", { priority: prio }); }}>
        <SelectField label="Priority" value={prio} onChange={(e) => setPrio(e.target.value)}>{["low", "medium", "high", "urgent"].map((p) => <option key={p} value={p}>{humanize(p)}</option>)}</SelectField>
      </ActionDialog>
      <ActionDialog open={dlg === "wait"} onClose={() => setDlg(null)} title="Put on hold" confirmLabel="Put on hold" reasonLabel="Why is it waiting?" onConfirm={async (reason) => { await post("wait", { reason }); }} />
      <ActionDialog open={dlg === "work"} onClose={() => setDlg(null)} title="Add work note" confirmLabel="Save note" confirmDisabled={!log.notes.trim()}
        description="Stock is deducted separately on the Inventory page, so retries never deduct twice."
        onConfirm={async () => { await post("work-log", log); setLog({ notes: "", materials_used: "", equipment_used: "" }); }}>
        <TextAreaField label="What was done" value={log.notes} onChange={(e) => setLog({ ...log, notes: e.target.value })} />
        <TextField label="Materials used (note)" value={log.materials_used} onChange={(e) => setLog({ ...log, materials_used: e.target.value })} />
        <TextField label="Equipment used (note)" value={log.equipment_used} onChange={(e) => setLog({ ...log, equipment_used: e.target.value })} />
      </ActionDialog>
      <ActionDialog open={dlg === "resolve"} onClose={() => setDlg(null)} title="Resolve complaint" confirmLabel="Mark resolved" reasonLabel="Resolution notes" onConfirm={async (r) => { await post("resolve", { resolution_notes: r }); }} />
      <ActionDialog open={dlg === "close"} onClose={() => setDlg(null)} title="Close complaint" confirmLabel="Close" description="Confirms the customer's issue is fixed." onConfirm={async () => { await post("close", {}); }} />
      <ActionDialog open={dlg === "reopen"} onClose={() => setDlg(null)} title="Reopen complaint" confirmLabel="Reopen" danger reasonLabel="Why is it reopened?" description="The original record, assignment and notes are kept." onConfirm={async (r) => { await post("reopen", { reason: r }); }} />
    </>
  );
}
