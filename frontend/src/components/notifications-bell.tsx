"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";

export function NotificationsBell() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () => api<{ count: number }>("/notifications/unread-count/").then((d) => alive && setCount(d.count)).catch(() => {});
    void load();
    const t = setInterval(load, 60_000);
    return () => { alive = false; clearInterval(t); };
  }, []);
  return (
    <Link href="/notifications" className="relative rounded-md p-2 text-slate-700 hover:bg-slate-100" aria-label={`Notifications${count ? `, ${count} unread` : ""}`}>
      <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden><path d="M18 8a6 6 0 10-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 01-3.4 0" /></svg>
      {count > 0 && <span className="absolute -end-0.5 -top-0.5 min-w-[1.1rem] rounded-full bg-red-600 px-1 text-center text-[10px] font-semibold leading-4 text-white">{count > 99 ? "99+" : count}</span>}
    </Link>
  );
}
