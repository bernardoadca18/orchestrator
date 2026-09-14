import { ReactNode, useEffect, useRef } from "react";
import { NavLink } from "react-router-dom";
import { ActionCapability } from "../api/operator";

export function AppShell({ children }: { children: ReactNode }) {
  return <div className="app-shell">
    <header className="app-topbar">
      <NavLink className="brand" to="/dashboard">NeoVids <span>Operator Console</span></NavLink>
      <nav aria-label="Navegação principal">
        <NavLink to="/dashboard">Dashboard</NavLink>
        <NavLink to="/productions">Produções</NavLink>
        <NavLink to="/review">Revisão</NavLink>
        <NavLink to="/music">Música</NavLink>
      </nav>
    </header>
    <main>{children}</main>
  </div>;
}

export function PageHeader({ title, description, actions }: { title: string; description?: ReactNode; actions?: ReactNode }) {
  return <header className="page-header">
    <div><h1>{title}</h1>{description && <p>{description}</p>}</div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>;
}

export function Card({ title, children, className = "" }: { title?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{title && <h2>{title}</h2>}{children}</section>;
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const safe = value || "não disponível";
  const tone = /failed|erro|rejected|blocked|critical/.test(safe)
    ? "danger"
    : /warning|stale|pending|queued|requested/.test(safe)
      ? "warning"
      : /completed|passed|approved|ready|healthy|concluido/.test(safe)
        ? "success"
        : "neutral";
  return <span className={`status-badge ${tone}`}>{safe.replace(/_/g, " ")}</span>;
}

export function Alert({ tone = "info", children }: { tone?: "info" | "warning" | "danger" | "success"; children: ReactNode }) {
  return <div className={`alert ${tone}`} role={tone === "danger" ? "alert" : "status"}>{children}</div>;
}

export function LoadingState({ label = "Carregando…" }: { label?: string }) {
  return <div className="state-message" role="status">{label}</div>;
}

export function ErrorState({ message }: { message: string }) {
  return <Alert tone="danger">{message}</Alert>;
}

export function EmptyState({ title, detail }: { title: string; detail?: string }) {
  return <div className="state-message"><strong>{title}</strong>{detail && <p>{detail}</p>}</div>;
}

export function Progress({ value, label }: { value: number; label?: string }) {
  const percent = Math.max(0, Math.min(100, Math.round(value * 100)));
  return <div className="progress-wrap">
    <div className="progress-label"><span>{label || "Progresso"}</span><strong>{percent}%</strong></div>
    <progress max={100} value={percent}>{percent}%</progress>
  </div>;
}

export function DefinitionList({ items }: { items: Array<[ReactNode, ReactNode]> }) {
  return <dl className="definition-list">{items.map(([term, value], index) => <div key={index}><dt>{term}</dt><dd>{value ?? "—"}</dd></div>)}</dl>;
}

export function TableWrap({ children }: { children: ReactNode }) {
  return <div className="table-wrap">{children}</div>;
}

export function Diagnostics({ value, label = "Diagnóstico técnico" }: { value: unknown; label?: string }) {
  if (value == null) return null;
  return <details className="diagnostics"><summary>{label}</summary><pre>{JSON.stringify(value, null, 2)}</pre></details>;
}

export function ActionButton({ capability, children, onClick, disabled }: {
  capability?: ActionCapability;
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  const state = actionAvailability(capability, disabled);
  const isDisabled = state.disabled;
  return <span className="action-with-reason">
    <button disabled={isDisabled} onClick={onClick} title={capability?.reason || undefined}>{children}</button>
    {capability?.allowed === false && capability.reason && <small>{capability.reason}</small>}
  </span>;
}

export function actionAvailability(capability?: ActionCapability, disabled = false) {
  return {
    disabled: disabled || capability?.allowed === false,
    reason: capability?.allowed === false ? capability.reason : null,
  };
}

export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  consequence: string;
  invalidates?: string[];
  confirmLabel: string;
  dangerous?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

export function ConfirmDialog(props: ConfirmDialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (props.open && !dialog.open) dialog.showModal();
    if (!props.open && dialog.open) dialog.close();
  }, [props.open]);
  return <dialog
    ref={ref}
    onCancel={(event) => { event.preventDefault(); props.onClose(); }}
    onKeyDown={(event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        props.onClose();
      }
    }}
    onClose={props.onClose}
  >
    <h2>{props.title}</h2>
    <p>{props.consequence}</p>
    {props.invalidates?.length ? <div><strong>Será reconstruído:</strong><ul>{props.invalidates.map((item) => <li key={item}>{item}</li>)}</ul></div> : null}
    <div className="dialog-actions">
      <button autoFocus onClick={props.onClose}>Cancelar</button>
      <button className={props.dangerous ? "danger-button" : ""} onClick={props.onConfirm}>{props.confirmLabel}</button>
    </div>
  </dialog>;
}
