"use client";

import Link from "next/link";
import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Office, Page, Village } from "@/lib/types";
import { ActionDialog } from "@/components/actions";
import { Alert, Button, Card, EmptyState, PageHeader, StatusBadge, TextField, useToast } from "@/components/ui";

type Dlg = null | { k: "office"; office?: Office } | { k: "village"; office: Office; village?: Village };

export default function OfficesPage() {
  const { can } = useAuth();
  const toast = useToast();
  const offices = useFetch<Page<Office>>("/offices/", { page_size: 100 });
  const villages = useFetch<Page<Village>>("/villages/", { page_size: 100 });
  const [dlg, setDlg] = useState<Dlg>(null);
  const [f, setF] = useState({ code: "", name: "", head: false, active: true });
  const reload = () => { offices.reload(); villages.reload(); };
  const open = (d: Dlg) => {
    if (d?.k === "office") setF({ code: d.office?.code ?? "", name: d.office?.name ?? "", head: !!(d.office as any)?.is_head_office, active: d.office?.is_active ?? true });
    if (d?.k === "village") setF({ code: d.village?.code ?? "", name: d.village?.name ?? "", head: false, active: d.village?.is_active ?? true });
    setDlg(d);
  };
  const manage = can("offices.create");

  async function save() {
    if (!dlg) return;
    const code = f.code.trim().toUpperCase();
    if (dlg.k === "office") {
      const body = { code, name: f.name.trim(), is_head_office: f.head, is_active: f.active };
      if (dlg.office) await api(`/offices/${dlg.office.id}/`, { method: "PATCH", body });
      else await api("/offices/", { method: "POST", body });
    } else {
      const body = { code, name: f.name.trim(), is_active: f.active };
      if (dlg.village) await api(`/villages/${dlg.village.id}/`, { method: "PATCH", body });
      else await api("/villages/", { method: "POST", body: { ...body, office: dlg.office.id } });
    }
    setDlg(null); reload(); toast("success", "Saved.");
  }

  const list = offices.data?.results ?? [];
  return (
    <>
      <PageHeader title="Offices & villages" subtitle="Set these up first: every customer, employee, invoice and report belongs to an office."
        actions={manage && <Button onClick={() => open({ k: "office" })}>New office</Button>} />
      {(offices.error || villages.error) && <Alert>{(offices.error || villages.error)!.message}</Alert>}
      {!manage && <Alert kind="info">Only the owner (or someone with the “create offices” permission) can change offices and villages.</Alert>}
      {offices.data && list.length === 0 && (
        <Card><EmptyState title="No offices yet" hint="Create your head office first, then add the villages it serves." />
          {manage && <div className="pb-4 text-center"><Button onClick={() => open({ k: "office" })}>Create the first office</Button></div>}</Card>
      )}
      <div className="space-y-5">
        {list.map((o) => {
          const vs = (villages.data?.results ?? []).filter((v) => v.office === o.id);
          return (
            <Card key={o.id} title={`${o.name} · ${o.code}`} actions={<>
              <StatusBadge status={o.is_active ? "active" : "cancelled"} />
              {manage && <Button variant="secondary" onClick={() => open({ k: "office", office: o })}>Edit</Button>}
              {manage && <Button onClick={() => open({ k: "village", office: o })}>Add village</Button>}</>}>
              {vs.length === 0 ? <p className="text-sm text-slate-500">No villages yet. Customers are registered per village, and the village code becomes the start of their ID (e.g. <b>KHT</b>001).</p> : (
                <table className="min-w-full text-sm"><thead className="text-start text-xs uppercase text-slate-500"><tr><th className="py-1 pe-4 text-start font-medium">Code</th><th className="pe-4 text-start font-medium">Village</th><th className="pe-4 text-start font-medium">Status</th><th /></tr></thead>
                  <tbody>{vs.map((v) => (
                    <tr key={v.id} className="border-t border-slate-100"><td className="py-1.5 pe-4 font-mono">{v.code}</td><td className="pe-4">{v.name}</td><td className="pe-4"><StatusBadge status={v.is_active ? "active" : "cancelled"} /></td>
                      <td className="text-end">{manage && <Button variant="ghost" onClick={() => open({ k: "village", office: o, village: v })}>Edit</Button>}</td></tr>))}</tbody></table>)}
            </Card>);
        })}
      </div>
      {list.length > 0 && <p className="mt-6 text-sm text-slate-600">Next: <Link className="text-brand-700 underline" href="/admin/packages">create packages</Link>, then <Link className="text-brand-700 underline" href="/admin/employees">add employees</Link>.</p>}

      <ActionDialog open={dlg !== null} onClose={() => setDlg(null)} confirmLabel="Save" confirmDisabled={!f.code.trim() || !f.name.trim()}
        title={dlg?.k === "office" ? (dlg.office ? `Edit office ${dlg.office.code}` : "New office") : dlg?.k === "village" ? (dlg.village ? `Edit village ${dlg.village.code}` : `New village in ${dlg.office.name}`) : ""}
        description={dlg?.k === "village" ? "The code (2–6 letters/digits, e.g. KHT) starts every customer ID in this village. It cannot be changed once customers exist." : "The code (2–10 letters/digits) is shown on documents. Invoice and receipt numbers keep the code the office had when it was created."}
        onConfirm={save}>
        <TextField label={dlg?.k === "village" ? "Village code" : "Office code"} value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} placeholder={dlg?.k === "village" ? "KHT" : "KOT"} maxLength={dlg?.k === "village" ? 6 : 10} />
        <TextField label={dlg?.k === "village" ? "Village name" : "Office name"} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        {dlg?.k === "office" && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.head} onChange={(e) => setF({ ...f, head: e.target.checked })} /> This is the head / central office</label>}
        {((dlg?.k === "office" && dlg.office) || (dlg?.k === "village" && dlg.village)) && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active</label>}
      </ActionDialog>
    </>
  );
}
