"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Office, Page, PackageT, Village } from "@/lib/types";
import { formatDate, humanize } from "@/lib/format";
import { ActionDialog } from "@/components/actions";
import { Alert, Button, DataTable, FilterSelect, PageHeader, Pagination, SelectField, StatusBadge, TextAreaField, TextField, useToast, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;

export default function LeadsPage() {
  const { can } = useAuth();
  const toast = useToast();
  const router = useRouter();
  const [search, setSearch] = useState(""); const [status, setStatus] = useState(""); const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, status]);
  const list = useFetch<Page<R>>("/leads/", { search: q, status, page });
  const offices = useFetch<Page<Office>>("/offices/");
  const villages = useFetch<Page<Village>>("/villages/");
  const packages = useFetch<Page<PackageT>>("/packages/", { active: "true" });
  const [dlg, setDlg] = useState<null | { k: "new" } | { k: "convert" | "lost" | "note"; lead: R }>(null);
  const [f, setF] = useState<Record<string, string>>({});
  const [dups, setDups] = useState<any[] | null>(null);
  const set = (k: string) => (e: { target: { value: string } }) => setF((x) => ({ ...x, [k]: e.target.value }));
  const close = () => { setDlg(null); setF({}); setDups(null); };

  async function convert(confirm: boolean) {
    const lead = (dlg as any).lead;
    try {
      const r = await api<R>(`/leads/${lead.id}/convert/`, { method: "POST", body: { village: Number(f.village), package: Number(f.package), installation_address: f.address, permanent_address: f.permanent || "", cnic: f.cnic || "", existing_customer: (f.existing || "").toUpperCase(), ...(confirm ? { confirm_duplicates: true, override_reason: f.override || "" } : {}) } });
      toast("success", `Created ${r.customer_id} / ${r.connection_id}.`); close(); router.push(`/customers/${r.customer_id}`);
    } catch (e) { if (e instanceof ApiError && e.code === "possible_duplicate") setDups((e.details as any)?.duplicates || []); else throw e; }
  }

  const cols: Column<R>[] = [
    { header: "Name", cell: (l) => <div><div className="font-medium">{l.name}</div><div className="text-xs text-slate-500">{l.phone}</div></div> }, { header: "Office", cell: (l) => l.office_code },
    { header: "Package", cell: (l) => l.package_code || "-" }, { header: "Source", cell: (l) => humanize(l.source) }, { header: "Created", cell: (l) => formatDate(l.created_at) },
    { header: "Status", cell: (l) => <StatusBadge status={l.status} /> },
    { header: "", cell: (l) => l.status === "converted" ? <Link href={`/customers/${l.customer_id}`} className="text-brand-700 hover:underline">{l.customer_id}</Link> : ["new", "contacted", "interested"].includes(l.status) ? (
      <div className="flex flex-wrap gap-1">
        {can("leads.manage") && <Button variant="ghost" onClick={() => setDlg({ k: "note", lead: l })}>Note</Button>}
        {can("leads.manage") && l.status === "new" && <Button variant="ghost" onClick={async () => { await api(`/leads/${l.id}/`, { method: "PATCH", body: { status: "contacted" } }); list.reload(); }}>Contacted</Button>}
        {can("leads.convert") && <Button variant="secondary" onClick={() => { setF({ package: String(l.interested_package || ""), address: l.address || "", village: "" }); setDlg({ k: "convert", lead: l }); }}>Convert</Button>}
        {can("leads.manage") && <Button variant="ghost" onClick={() => setDlg({ k: "lost", lead: l })}>Lost</Button>}
      </div>) : <span className="text-xs text-slate-500">{l.lost_reason}</span> },
  ];
  return (
    <>
      <PageHeader title="Leads" subtitle="A permanent customer ID is created only when a lead is converted" actions={can("leads.manage") && <Button onClick={() => setDlg({ k: "new" })}>New lead</Button>} />
      <div className="mb-3 flex flex-wrap gap-2">
        <input type="search" aria-label="Search leads" placeholder="Search name, phone or address" value={search} onChange={(e) => setSearch(e.target.value)} className="w-full max-w-xs rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm" />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={["new", "contacted", "interested", "converted", "lost"].map((s) => ({ value: s, label: humanize(s) }))} />
      </div>
      {list.error && <Alert>{list.error.message}</Alert>}
      <DataTable columns={cols} rows={list.data?.results} loading={list.loading} rowKey={(l) => l.id} empty={{ title: "No leads" }} />
      <Pagination page={page} count={list.data?.count || 0} onPage={setPage} />
      <ActionDialog open={dlg?.k === "new"} onClose={close} title="New lead" confirmLabel="Save lead" confirmDisabled={!f.office || !f.name || !f.phone}
        onConfirm={async () => { await api("/leads/", { method: "POST", body: { office: Number(f.office), name: f.name, phone: f.phone, source: f.source || "office", address: f.address || "", notes: f.notes || "", interested_package: f.package ? Number(f.package) : null } }); close(); list.reload(); }}>
        <SelectField label="Office" value={f.office || ""} onChange={set("office")}><option value="">Select…</option>{offices.data?.results.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</SelectField>
        <div className="grid grid-cols-2 gap-3"><TextField label="Name" value={f.name || ""} onChange={set("name")} /><TextField label="Phone" value={f.phone || ""} onChange={set("phone")} /></div>
        <div className="grid grid-cols-2 gap-3"><SelectField label="Source" value={f.source || "office"} onChange={set("source")}><option value="office">Office</option><option value="phone">Phone</option></SelectField>
          <SelectField label="Interested package" value={f.package || ""} onChange={set("package")}><option value="">Not sure</option>{packages.data?.results.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</SelectField></div>
        <TextField label="Village / address" value={f.address || ""} onChange={set("address")} /><TextAreaField label="Notes" value={f.notes || ""} onChange={set("notes")} />
      </ActionDialog>
      <ActionDialog open={dlg?.k === "note"} onClose={close} title="Follow-up note" confirmLabel="Add note" confirmDisabled={!f.note}
        onConfirm={async () => { await api(`/leads/${(dlg as any).lead.id}/note/`, { method: "POST", body: { note: f.note } }); close(); list.reload(); }}><TextAreaField label="Note" value={f.note || ""} onChange={set("note")} /></ActionDialog>
      <ActionDialog open={dlg?.k === "lost"} onClose={close} title="Mark lead as lost" confirmLabel="Mark lost" danger reasonLabel="Why was it lost?"
        onConfirm={async (r) => { await api(`/leads/${(dlg as any).lead.id}/lost/`, { method: "POST", body: { reason: r } }); close(); list.reload(); }} />
      <ActionDialog open={dlg?.k === "convert"} onClose={close} title="Convert lead to customer" confirmLabel={dups ? "Create anyway" : "Create customer & connection"} confirmDisabled={!f.village || !f.package || !f.address || (!!dups && !f.override)}
        description="Creates the permanent customer ID and a connection waiting for installation. Leave 'existing customer' empty for a new customer."
        onConfirm={() => convert(!!dups)}>
        {dups && <Alert kind="info">Possible duplicates: {dups.map((d) => `${d.customer_id} (${d.matches.join(", ")})`).join("; ")}. Explain below to continue.</Alert>}
        <SelectField label="Village" value={f.village || ""} onChange={set("village")}><option value="">Select…</option>{villages.data?.results.map((v) => <option key={v.id} value={v.id}>{v.name} ({v.code})</option>)}</SelectField>
        <SelectField label="Package" value={f.package || ""} onChange={set("package")}><option value="">Select…</option>{packages.data?.results.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</SelectField>
        <TextAreaField label="Installation address" value={f.address || ""} onChange={set("address")} />
        <div className="grid grid-cols-2 gap-3"><TextField label="CNIC (optional)" value={f.cnic || ""} onChange={set("cnic")} /><TextField label="Existing customer ID (optional)" value={f.existing || ""} onChange={set("existing")} /></div>
        {dups && <TextAreaField label="Why is this a different customer?" value={f.override || ""} onChange={set("override")} />}
      </ActionDialog>
    </>
  );
}
