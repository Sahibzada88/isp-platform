"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Page, PackageT } from "@/lib/types";
import { formatPKR } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { Alert, Button, DataTable, PageHeader, StatusBadge, TextField, type Column } from "@/components/ui";

export default function PackagesPage() {
  const { can } = useAuth();
  const run = useRun();
  const list = useFetch<Page<PackageT>>("/packages/");
  const [dlg, setDlg] = useState<null | { new: true } | { edit: PackageT }>(null);
  const [f, setF] = useState({ code: "", name: "", speed_mbps: "", price_pkr: "" });
  const open = (p?: PackageT) => { setF(p ? { code: p.code, name: p.name, speed_mbps: String(p.speed_mbps), price_pkr: p.price_pkr } : { code: "", name: "", speed_mbps: "", price_pkr: "" }); setDlg(p ? { edit: p } : { new: true }); };
  const toggle = async (p: PackageT) => { if (await run(() => api(`/packages/${p.id}/${p.is_active ? "deactivate" : "activate"}/`, { method: "POST" }), "Package updated.")) list.reload(); };

  const cols: Column<PackageT>[] = [
    { header: "Code", cell: (p) => p.code }, { header: "Name", cell: (p) => p.name }, { header: "Speed", cell: (p) => `${p.speed_mbps} Mbps` }, { header: "Price", cell: (p) => formatPKR(p.price_pkr), className: "text-end" },
    { header: "Validity", cell: () => "30 days" }, { header: "Status", cell: (p) => <StatusBadge status={p.is_active ? "active" : "cancelled"} /> },
    { header: "", cell: (p) => can("packages.manage") ? <div className="flex gap-1"><Button variant="secondary" onClick={() => open(p)}>Edit</Button><Button variant="ghost" onClick={() => toggle(p)}>{p.is_active ? "Deactivate" : "Activate"}</Button></div> : null },
  ];
  return (
    <>
      <PageHeader title="Packages" subtitle="Shared by all offices. Price changes affect future invoices only; every package lasts 30 days."
        actions={can("packages.manage") && <Button onClick={() => open()}>New package</Button>} />
      {list.error && <Alert>{list.error.message}</Alert>}
      <DataTable columns={cols} rows={list.data?.results} loading={list.loading} rowKey={(p) => p.id} empty={{ title: "No packages yet", hint: "Create one, or import a CSV on the Data import page." }} />
      <ActionDialog open={dlg !== null} onClose={() => setDlg(null)} title={dlg && "edit" in dlg ? `Edit ${dlg.edit.code}` : "New package"} confirmLabel="Save" confirmDisabled={!f.name || !f.speed_mbps || !f.price_pkr || (!!dlg && "new" in dlg && !f.code)}
        description="Changing a package here does not change any router configuration."
        onConfirm={async () => {
          if (dlg && "edit" in dlg) await api(`/packages/${dlg.edit.id}/`, { method: "PATCH", body: { name: f.name, speed_mbps: Number(f.speed_mbps), price_pkr: f.price_pkr } });
          else await api("/packages/", { method: "POST", body: { code: f.code.toUpperCase(), name: f.name, speed_mbps: Number(f.speed_mbps), price_pkr: f.price_pkr } });
          setDlg(null); list.reload();
        }}>
        {dlg && "new" in dlg && <TextField label="Code" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value })} />}
        <TextField label="Name" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        <div className="grid grid-cols-2 gap-3"><TextField label="Speed (Mbps)" inputMode="numeric" value={f.speed_mbps} onChange={(e) => setF({ ...f, speed_mbps: e.target.value })} /><TextField label="Price (PKR)" value={f.price_pkr} onChange={(e) => setF({ ...f, price_pkr: e.target.value })} /></div>
      </ActionDialog>
    </>
  );
}
