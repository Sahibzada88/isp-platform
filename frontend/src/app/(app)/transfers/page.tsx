"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { useFetch } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { ActionDialog, useRun } from "@/components/actions";
import { Alert, Button, Card, DataTable, PageHeader, StatusBadge, type Column } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type R = Record<string, any>;

export default function TransfersPage() {
  const { user, can } = useAuth();
  const run = useRun();
  const office = useFetch<Page<R>>("/transfers/office/");
  const owner = useFetch<Page<R>>("/transfers/ownership/");
  const [rej, setRej] = useState<null | { kind: "office" | "ownership"; id: number }>(null);
  const reload = () => { office.reload(); owner.reload(); };
  const decide = async (kind: "office" | "ownership", id: number, verb: "approve" | "reject", body: object) => { if (await run(() => api(`/transfers/${kind}/${id}/${verb}/`, { method: "POST", body }), verb === "approve" ? "Approved." : "Rejected.")) reload(); };
  const actions = (kind: "office" | "ownership", r: R) => r.status === "pending" && can("transfers.approve") ? (
    r.requested_by === user?.id && !user?.is_owner ? <span className="text-xs text-slate-500">Needs another approver</span> :
      <div className="flex gap-1"><Button variant="secondary" onClick={() => decide(kind, r.id, "approve", {})}>Approve</Button><Button variant="ghost" onClick={() => setRej({ kind, id: r.id })}>Reject</Button></div>) : null;

  const ocols: Column<R>[] = [
    { header: "Connection", cell: (r) => r.connection_id }, { header: "Customer", cell: (r) => r.customer_id }, { header: "From → To", cell: (r) => `${r.from_office_code} → ${r.to_office_code} (${r.to_village_code})` },
    { header: "New address", cell: (r) => r.new_installation_address }, { header: "Reason", cell: (r) => r.reason }, { header: "Requested", cell: (r) => formatDateTime(r.requested_at) },
    { header: "Status", cell: (r) => <StatusBadge status={r.status} /> }, { header: "", cell: (r) => actions("office", r) },
  ];
  const wcols: Column<R>[] = [
    { header: "Connection", cell: (r) => r.connection_id }, { header: "From owner", cell: (r) => r.from_customer_id }, { header: "To owner", cell: (r) => r.to_customer_id },
    { header: "Unpaid balance", cell: (r) => r.balance_handling.replace(/_/g, " ") }, { header: "Reason", cell: (r) => r.reason }, { header: "Status", cell: (r) => <StatusBadge status={r.status} /> },
    { header: "", cell: (r) => actions("ownership", r) },
  ];
  return (
    <>
      <PageHeader title="Transfers" subtitle="Office moves and ownership changes need approval. Nothing is deleted; IDs never change." />
      {(office.error || owner.error) && <Alert>{(office.error || owner.error)!.message}</Alert>}
      <div className="space-y-5">
        <Card title="Office / village transfers"><DataTable columns={ocols} rows={office.data?.results} loading={office.loading} rowKey={(r) => r.id} empty={{ title: "No transfer requests" }} /></Card>
        <Card title="Ownership changes"><DataTable columns={wcols} rows={owner.data?.results} loading={owner.loading} rowKey={(r) => r.id} empty={{ title: "No ownership transfers" }} /></Card>
      </div>
      <ActionDialog open={!!rej} onClose={() => setRej(null)} title="Reject transfer" confirmLabel="Reject" danger reasonLabel="Reason" onConfirm={async (note) => { await api(`/transfers/${rej!.kind}/${rej!.id}/reject/`, { method: "POST", body: { note } }); setRej(null); reload(); }} />
    </>
  );
}
