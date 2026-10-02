import type { ReactNode } from "react";

export function Card({ title, actions, children }: { title?: ReactNode; actions?: ReactNode; children: ReactNode }) {
  return (
    <section className="rounded-lg border border-slate-200 bg-white shadow-sm">
      {(title || actions) && (
        <header className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
          <h2 className="text-sm font-semibold text-slate-700">{title}</h2>
          <div className="flex gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const SEVERITY: Record<string, string> = {
  sev1: "bg-red-100 text-red-800 ring-red-200",
  sev2: "bg-orange-100 text-orange-800 ring-orange-200",
  sev3: "bg-amber-100 text-amber-800 ring-amber-200",
  sev4: "bg-slate-100 text-slate-600 ring-slate-200",
};

export function Badge({ tone = "slate", children }: { tone?: string; children: ReactNode }) {
  const tones: Record<string, string> = {
    slate: "bg-slate-100 text-slate-700 ring-slate-200",
    green: "bg-emerald-100 text-emerald-800 ring-emerald-200",
    red: "bg-red-100 text-red-800 ring-red-200",
    amber: "bg-amber-100 text-amber-800 ring-amber-200",
    blue: "bg-sky-100 text-sky-800 ring-sky-200",
    ...SEVERITY,
  };
  return (
    <span className={`inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium ring-1 ring-inset ${tones[tone] ?? tones.slate}`}>
      {children}
    </span>
  );
}

export const label = (s: string | null | undefined) => (s ? s.replaceAll("_", " ") : "-");

export function when(iso: string | null | undefined): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function Button({
  children,
  onClick,
  tone = "default",
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  tone?: "default" | "primary" | "danger" | "success";
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  const tones = {
    default: "bg-white text-slate-700 ring-slate-300 hover:bg-slate-50",
    primary: "bg-sky-600 text-white ring-sky-600 hover:bg-sky-700",
    danger: "bg-white text-red-700 ring-red-300 hover:bg-red-50",
    success: "bg-emerald-600 text-white ring-emerald-600 hover:bg-emerald-700",
  };
  return (
    <button
      type={type}
      disabled={disabled}
      onClick={onClick}
      className={`rounded px-3 py-1.5 text-sm font-medium ring-1 ring-inset transition disabled:cursor-not-allowed disabled:opacity-50 ${tones[tone]}`}
    >
      {children}
    </button>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  if (!error) return null;
  return <p className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">{String(error instanceof Error ? error.message : error)}</p>;
}
