"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { useDebounced, useFetch } from "@/lib/hooks";
import type { Customer, Page, Village } from "@/lib/types";
import { formatDate } from "@/lib/format";
import { Alert, DataTable, FilterSelect, LinkButton, PageHeader, Pagination, StatusBadge, type Column } from "@/components/ui";

export default function CustomersPage() {
  const { can } = useAuth();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("");
  const [village, setVillage] = useState("");
  const [page, setPage] = useState(1);
  const q = useDebounced(search);
  useEffect(() => setPage(1), [q, status, village]);

  const villages = useFetch<Page<Village>>("/villages/");
  const { data, loading, error } = useFetch<Page<Customer>>("/customers/", { search: q, status, village, page });

  const columns: Column<Customer>[] = [
    { header: "ID", cell: (c) => <Link href={`/customers/${c.customer_id}`} className="font-medium text-brand-700 hover:underline">{c.customer_id}</Link> },
    { header: "Name", cell: (c) => c.full_name },
    { header: "Phone", cell: (c) => c.phone },
    { header: "Village", cell: (c) => c.village_code },
    { header: "Office", cell: (c) => c.office_code },
    { header: "Registered", cell: (c) => formatDate(c.registered_on) },
    { header: "Status", cell: (c) => <StatusBadge status={c.status} /> },
  ];

  return (
    <>
      <PageHeader title="Customers" subtitle="People who hold one or more connections"
        actions={can("customers.create") && <LinkButton href="/customers/new" variant="primary">New customer</LinkButton>} />
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <input type="search" aria-label="Search customers" placeholder="Search ID, name or phone" value={search} onChange={(e) => setSearch(e.target.value)}
          className="w-full max-w-xs rounded-md border border-slate-300 px-3 py-2 text-sm shadow-sm" />
        <FilterSelect label="Village" value={village} onChange={setVillage} options={(villages.data?.results || []).map((v) => ({ value: String(v.id), label: `${v.name} (${v.code})` }))} />
        <FilterSelect label="Status" value={status} onChange={setStatus} options={[{ value: "active", label: "Active" }, { value: "archived", label: "Archived" }]} />
      </div>
      {error && <Alert>{error.message}</Alert>}
      <DataTable columns={columns} rows={data?.results} loading={loading} rowKey={(c) => c.id}
        empty={{ title: "No customers found", hint: q || status || village ? "Try clearing the filters." : "Register the first customer to get started." }} />
      <Pagination page={page} count={data?.count || 0} onPage={setPage} />
    </>
  );
}
