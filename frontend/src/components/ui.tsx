"use client";

import Link from "next/link";
import {
  createContext, useCallback, useContext, useEffect, useId, useRef, useState,
  type ButtonHTMLAttributes, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from "react";
import { humanize } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

const cx = (...c: (string | false | null | undefined)[]) => c.filter(Boolean).join(" ");

// ------------------------------------------------------------------ buttons
type Variant = "primary" | "secondary" | "danger" | "ghost";
const VARIANTS: Record<Variant, string> = {
  primary: "bg-brand-600 text-white hover:bg-brand-700 disabled:bg-brand-600/50",
  secondary: "bg-white text-slate-800 border border-slate-300 hover:bg-slate-50 disabled:text-slate-400",
  danger: "bg-red-600 text-white hover:bg-red-700 disabled:bg-red-600/50",
  ghost: "text-slate-700 hover:bg-slate-100 disabled:text-slate-400",
};

export function Button({ variant = "primary", loading, className, children, disabled, ...rest }:
  ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; loading?: boolean }) {
  const { tr } = useI18n();
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      className={cx("inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition-colors disabled:cursor-not-allowed", VARIANTS[variant], className)}
    >
      {loading && <Spinner className="h-4 w-4" />}
      {typeof children === "string" ? tr(children) : children}
    </button>
  );
}

export function LinkButton({ href, children, variant = "secondary", className }:
  { href: string; children: ReactNode; variant?: Variant; className?: string }) {
  return <Link href={href} className={cx("inline-flex items-center rounded-md px-3 py-2 text-sm font-medium", VARIANTS[variant], className)}>{children}</Link>;
}

export function Spinner({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg className={cx("animate-spin", className)} viewBox="0 0 24 24" fill="none" role="status" aria-label="Loading">
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" className="opacity-25" />
      <path d="M4 12a8 8 0 018-8" stroke="currentColor" strokeWidth="4" strokeLinecap="round" className="opacity-75" />
    </svg>
  );
}

// ------------------------------------------------------------------ form controls
const CONTROL = "block w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-sm shadow-sm placeholder:text-slate-400 disabled:bg-slate-100";

export function Field({ label, error, hint, children, htmlFor }:
  { label: string; error?: string; hint?: string; children: ReactNode; htmlFor?: string }) {
  const { tr } = useI18n();
  return (
    <div>
      <label htmlFor={htmlFor} className="mb-1 block text-sm font-medium text-slate-700">{tr(label)}</label>
      {children}
      {hint && !error && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      {error && <p className="mt-1 text-xs text-red-600" role="alert">{error}</p>}
    </div>
  );
}

export function TextField({ label, error, hint, className, ...rest }:
  InputHTMLAttributes<HTMLInputElement> & { label: string; error?: string; hint?: string }) {
  const id = useId();
  return (
    <Field label={label} error={error} hint={hint} htmlFor={id}>
      <input id={id} aria-invalid={!!error} {...rest} className={cx(CONTROL, error && "border-red-500", className)} />
    </Field>
  );
}

export function TextAreaField({ label, error, hint, ...rest }:
  TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string; error?: string; hint?: string }) {
  const id = useId();
  return (
    <Field label={label} error={error} hint={hint} htmlFor={id}>
      <textarea id={id} rows={3} aria-invalid={!!error} {...rest} className={cx(CONTROL, error && "border-red-500")} />
    </Field>
  );
}

export function SelectField({ label, error, hint, children, ...rest }:
  SelectHTMLAttributes<HTMLSelectElement> & { label: string; error?: string; hint?: string }) {
  const id = useId();
  return (
    <Field label={label} error={error} hint={hint} htmlFor={id}>
      <select id={id} aria-invalid={!!error} {...rest} className={cx(CONTROL, error && "border-red-500")}>{children}</select>
    </Field>
  );
}

export function FilterSelect({ label, value, onChange, options }:
  { label: string; value: string; onChange: (v: string) => void; options: { value: string; label: string }[] }) {
  return (
    <label className="text-sm">
      <span className="sr-only">{label}</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-label={label} className={cx(CONTROL, "w-auto")}>
        <option value="">{label}: all</option>
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </label>
  );
}

// ------------------------------------------------------------------ display
const BADGE: Record<string, string> = {
  active: "bg-green-100 text-green-800", paid: "bg-green-100 text-green-800", confirmed: "bg-green-100 text-green-800",
  valid: "bg-green-100 text-green-800", reconciled: "bg-green-100 text-green-800",
  pending_installation: "bg-amber-100 text-amber-800", pending_approval: "bg-amber-100 text-amber-800",
  open: "bg-blue-100 text-blue-800", payment_pending: "bg-amber-100 text-amber-800",
  pending_verification: "bg-amber-100 text-amber-800", requested: "bg-amber-100 text-amber-800",
  expired: "bg-orange-100 text-orange-800", suspended: "bg-orange-100 text-orange-800",
  cancelled: "bg-slate-200 text-slate-700", void: "bg-slate-200 text-slate-700", archived: "bg-slate-200 text-slate-700",
  rejected: "bg-red-100 text-red-800", reversed: "bg-red-100 text-red-800", invalidated: "bg-red-100 text-red-800",
};

export function StatusBadge({ status }: { status: string | null | undefined }) {
  const { tr } = useI18n();
  const s = status || "";
  return <span className={cx("inline-block whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium", BADGE[s] || "bg-slate-100 text-slate-700")}>{tr(humanize(s))}</span>;
}

export function Card({ title, actions, children, className }:
  { title?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  const { tr } = useI18n();
  return (
    <section className={cx("rounded-lg border border-slate-200 bg-white shadow-sm", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 px-4 py-3">
          {title && <h2 className="text-sm font-semibold text-slate-900">{tr(title)}</h2>}
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: string; actions?: ReactNode }) {
  const { tr } = useI18n();
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold text-slate-900">{tr(title)}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-slate-500">{tr(subtitle)}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function KeyValue({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-1 gap-x-6 gap-y-3 sm:grid-cols-2">
      {items.map((i) => (
        <div key={i.label}>
          <dt className="text-xs uppercase tracking-wide text-slate-500">{i.label}</dt>
          <dd className="mt-0.5 text-sm text-slate-900 break-words">{i.value ?? "-"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Alert({ kind = "error", children }: { kind?: "error" | "info" | "success"; children: ReactNode }) {
  const style = { error: "bg-red-50 text-red-800 border-red-200", info: "bg-blue-50 text-blue-800 border-blue-200", success: "bg-green-50 text-green-800 border-green-200" }[kind];
  return <div role={kind === "error" ? "alert" : "status"} className={cx("rounded-md border px-3 py-2 text-sm", style)}>{children}</div>;
}

export function EmptyState({ title, hint }: { title: string; hint?: string }) {
  const { tr } = useI18n();
  return (
    <div className="px-4 py-10 text-center">
      <p className="text-sm font-medium text-slate-700">{tr(title)}</p>
      {hint && <p className="mt-1 text-xs text-slate-500">{tr(hint)}</p>}
    </div>
  );
}

export function SkeletonRows({ rows = 5, cols = 4 }: { rows?: number; cols?: number }) {
  return (
    <tbody aria-hidden>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className="border-t border-slate-100">
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-4 py-3"><div className="h-4 animate-pulse rounded bg-slate-200" /></td>
          ))}
        </tr>
      ))}
    </tbody>
  );
}

// ------------------------------------------------------------------ table
export interface Column<T> { header: string; cell: (row: T) => ReactNode; className?: string }

export function DataTable<T>({ columns, rows, loading, empty, rowKey }:
  { columns: Column<T>[]; rows: T[] | undefined; loading: boolean; empty: { title: string; hint?: string }; rowKey: (r: T) => string | number }) {
  const { tr } = useI18n();
  return (
    <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white shadow-sm">
      <table className="min-w-full text-start text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wide text-slate-500">
          <tr>{columns.map((c) => <th key={c.header} scope="col" className={cx("px-4 py-2.5 font-medium", c.className)}>{tr(c.header)}</th>)}</tr>
        </thead>
        {loading && !rows?.length ? <SkeletonRows cols={columns.length} /> : (
          <tbody>
            {rows?.map((r) => (
              <tr key={rowKey(r)} className="border-t border-slate-100 hover:bg-slate-50/60">
                {columns.map((c) => <td key={c.header} className={cx("px-4 py-2.5 align-middle", c.className)}>{c.cell(r)}</td>)}
              </tr>
            ))}
          </tbody>
        )}
      </table>
      {!loading && rows?.length === 0 && <EmptyState {...empty} />}
    </div>
  );
}

export function Pagination({ page, count, pageSize = 25, onPage }:
  { page: number; count: number; pageSize?: number; onPage: (p: number) => void }) {
  const { tr } = useI18n();
  const pages = Math.max(1, Math.ceil(count / pageSize));
  if (count === 0) return null;
  return (
    <nav aria-label="Pagination" className="mt-3 flex items-center justify-between text-sm text-slate-600">
      <span>{count.toLocaleString()} record{count === 1 ? "" : "s"}</span>
      <div className="flex items-center gap-2">
        <Button variant="secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>Previous</Button>
        <span aria-live="polite">Page {page} of {pages}</span>
        <Button variant="secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>Next</Button>
      </div>
    </nav>
  );
}

// ------------------------------------------------------------------ modal (native <dialog>: focus trap + Esc for free)
export function Modal({ open, onClose, title, children, footer }:
  { open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const { tr } = useI18n();
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} aria-labelledby={titleId} onClose={onClose} className="w-full max-w-lg rounded-lg p-0 shadow-xl backdrop:bg-slate-900/40">
      {open && (
        <div>
          <div className="border-b border-slate-100 px-5 py-3"><h2 id={titleId} className="text-base font-semibold">{tr(title)}</h2></div>
          <div className="space-y-4 px-5 py-4">{children}</div>
          {footer && <div className="flex justify-end gap-2 border-t border-slate-100 bg-slate-50 px-5 py-3">{footer}</div>}
        </div>
      )}
    </dialog>
  );
}

// ------------------------------------------------------------------ toasts
interface Toast { id: number; kind: "success" | "error"; text: string }
const ToastCtx = createContext<(kind: Toast["kind"], text: string) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((t) => [...t, { id, kind, text }]);
    setTimeout(() => setItems((t) => t.filter((x) => x.id !== id)), 6000);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div aria-live="polite" className="pointer-events-none fixed bottom-4 end-4 z-50 flex w-80 max-w-[90vw] flex-col gap-2">
        {items.map((t) => (
          <div key={t.id} className={cx("pointer-events-auto rounded-md border px-3 py-2 text-sm shadow", t.kind === "success" ? "border-green-200 bg-green-50 text-green-900" : "border-red-200 bg-red-50 text-red-900")}>{t.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
