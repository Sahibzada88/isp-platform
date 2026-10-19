"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useFetch } from "@/lib/hooks";
import type { Page } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { Alert, Button, Card, EmptyState, PageHeader, Spinner } from "@/components/ui";

interface N { id: number; title: string; body: string; link: string; created_at: string; read_at: string | null }

export default function NotificationsPage() {
  const { data, loading, error, reload } = useFetch<Page<N>>("/notifications/");
  const markAll = async () => { await api("/notifications/read-all/", { method: "POST" }); reload(); };
  const open = async (n: N) => { if (!n.read_at) await api(`/notifications/${n.id}/read/`, { method: "POST" }); reload(); };
  return (
    <>
      <PageHeader title="Notifications" subtitle="Operational alerts for your offices and permissions" actions={<Button variant="secondary" onClick={markAll}>Mark all read</Button>} />
      {error && <Alert>{error.message}</Alert>}
      <Card>
        {loading && !data ? <Spinner /> : data?.results.length === 0 ? <EmptyState title="You're all caught up" /> : (
          <ul className="divide-y divide-slate-100">
            {data?.results.map((n) => (
              <li key={n.id} className={`flex items-start justify-between gap-3 py-3 ${n.read_at ? "opacity-60" : ""}`}>
                <div>
                  {n.link ? <Link href={n.link} onClick={() => open(n)} className="text-sm font-medium text-brand-700 hover:underline">{n.title}</Link> : <span className="text-sm font-medium">{n.title}</span>}
                  {n.body && <p className="text-sm text-slate-600">{n.body}</p>}
                  <p className="text-xs text-slate-500">{formatDateTime(n.created_at)}</p>
                </div>
                {!n.read_at && <Button variant="ghost" onClick={() => open(n)}>Mark read</Button>}
              </li>
            ))}
          </ul>)}
      </Card>
    </>
  );
}
