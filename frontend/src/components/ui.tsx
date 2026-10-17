import { ReactNode, useEffect, useId, useRef, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { ActionCapability } from "../api/operator";
import { ThemeToggle } from "../theme/ThemeToggle";

function CopyIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
      <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export function CopyButton({
  value,
  label,
}: {
  value: string;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(copyTimer.current), []);
  const truncated =
    value.length > 12 ? `${value.slice(0, 8)}…${value.slice(-4)}` : value;

  async function copy() {
    clearTimeout(copyTimer.current);
    setCopied(false);
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      copyTimer.current = setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopyFailed(true);
    }
  }

  return (
    <button
      type="button"
      className="copy-button"
      onClick={copy}
      title={label ? `${label}: ${value}` : value}
      aria-label={label ? `Copiar ${label}` : "Copiar"}
    >
      <code>{truncated}</code>
      {copied ? <CheckIcon /> : <CopyIcon />}
      {copied && <span className="copy-feedback">Copiado</span>}
      {copyFailed && (
        <span className="text-danger" role="status">
          Não foi possível copiar
        </span>
      )}
    </button>
  );
}

export function ScoreBar({
  value,
  max = 1,
  label,
}: {
  value: number;
  max?: number;
  label?: string;
}) {
  const percent = Math.max(0, Math.min(100, (value / max) * 100));
  const isLow = percent < 40;
  const isMedium = percent >= 40 && percent < 70;

  return (
    <div
      className="score-bar-wrap"
      role="meter"
      aria-label={label || "Score"}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
    >
      {label && <span className="score-label">{label}</span>}
      <div className="score-bar">
        <div
          className={`score-bar-fill ${isLow ? "low" : isMedium ? "medium" : "high"}`}
          style={{ width: `${percent}%` }}
        />
      </div>
      <span className="score-value">{value.toFixed(2)}</span>
    </div>
  );
}

export function RelativeTime({ date }: { date: string | Date }) {
  const d = typeof date === "string" ? new Date(date) : date;
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  let relative: string;
  if (diffMins < 1) relative = "agora";
  else if (diffMins < 60) relative = `${diffMins}min atrás`;
  else if (diffHours < 24) relative = `${diffHours}h atrás`;
  else if (diffDays < 7) relative = `${diffDays}d atrás`;
  else relative = d.toLocaleDateString("pt-BR");

  return (
    <span title={d.toLocaleString("pt-BR")} className="relative-time">
      {relative}
    </span>
  );
}

function LayoutGridIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="3" y="3" width="7" height="7" />
      <rect x="14" y="3" width="7" height="7" />
      <rect x="14" y="14" width="7" height="7" />
      <rect x="3" y="14" width="7" height="7" />
    </svg>
  );
}

function ClapperboardIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M4 11V8a4 4 0 0 1 4-4h.5" />
      <path d="M4.5 11h15" />
      <path d="M4.5 11a.5.5 0 0 0-.5.5v7a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7a.5.5 0 0 0-.5-.5" />
      <path d="M9 4h.01" />
      <path d="M14 4h.01" />
      <path d="M19 4h.01" />
    </svg>
  );
}

function ClipboardCheckIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <rect x="8" y="2" width="8" height="4" rx="1" ry="1" />
      <path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2" />
      <path d="m9 14 2 2 4-4" />
    </svg>
  );
}

function MusicIcon() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M9 18V5l12-2v13" />
      <circle cx="6" cy="18" r="3" />
      <circle cx="18" cy="16" r="3" />
    </svg>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const area = pathname.startsWith("/productions")
    ? "Produções"
    : pathname.startsWith("/review")
      ? "Revisões"
      : pathname.startsWith("/music")
        ? "Biblioteca musical"
        : pathname.startsWith("/voice")
          ? "Perfis de voz"
          : "Painel de operação";
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Pular para o conteúdo
      </a>
      <aside className="app-sidebar">
        <div className="app-sidebar-header">
          <NavLink
            to="/dashboard"
            className="app-sidebar-brand"
            aria-label="NeoVids"
          >
            <span>N</span>
            <span>
              NeoVids
              <small className="brand-subtitle">ESTÚDIO DE PRODUÇÃO</small>
            </span>
          </NavLink>
        </div>
        <nav className="app-sidebar-nav" aria-label="Navegação principal">
          <NavLink
            to="/dashboard"
            aria-label="Dashboard"
            className={({ isActive }) =>
              isActive ? "nav-item active" : "nav-item"
            }
          >
            <LayoutGridIcon />
            <span>Painel de operação</span>
          </NavLink>
          <NavLink
            to="/productions"
            aria-label="Produções"
            className={({ isActive }) =>
              isActive ? "nav-item active" : "nav-item"
            }
          >
            <ClapperboardIcon />
            <span>Produções</span>
          </NavLink>
          <NavLink
            to="/review"
            aria-label="Revisão"
            className={({ isActive }) =>
              isActive ? "nav-item active" : "nav-item"
            }
          >
            <ClipboardCheckIcon />
            <span>Revisão</span>
          </NavLink>
          <NavLink
            to="/music"
            aria-label="Música"
            className={({ isActive }) =>
              isActive ? "nav-item active" : "nav-item"
            }
          >
            <MusicIcon />
            <span>Música</span>
          </NavLink>
          <NavLink
            to="/voice"
            aria-label="Voz"
            className={({ isActive }) =>
              isActive ? "nav-item active" : "nav-item"
            }
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              aria-hidden="true"
            >
              <rect x="9" y="2" width="6" height="12" rx="3" />
              <path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" />
            </svg>
            <span>Voz</span>
          </NavLink>
        </nav>
        <div className="app-sidebar-footer">
          <span className="studio-signature">Bolas Industries</span>
          <ThemeToggle />
        </div>
      </aside>
      <div className="app-main">
        <header className="app-topbar">
          <div className="topbar-title">
            <span>
              NeoVids <span aria-hidden="true">/</span> <strong>{area}</strong>
            </span>
          </div>
        </header>
        <main id="main-content" className="app-content" tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {description && <p>{description}</p>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Card({
  title,
  children,
  className = "",
}: {
  title?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`}>
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}

export function statusTone(value: string | null | undefined) {
  const safe = value || "não disponível";
  return /failed|erro|rejected|blocked|critical|unavailable|destructive/.test(
    safe,
  )
    ? "danger"
    : /warning|stale|pending|queued|requested|degraded|attention/.test(safe)
      ? "warning"
      : /completed|passed|approved|ready|healthy|concluido|allowed/.test(safe)
        ? "success"
        : "neutral";
}

export function statusLabel(value: string) {
  const labels: Record<string, string> = {
    unknown: "Não informado",
    active: "Em andamento",
    running: "Em processamento",
    queued: "Na fila",
    pending: "Pendente",
    completed: "Concluído",
    failed: "Falhou",
    blocked: "Bloqueado",
    cancelled: "Cancelado",
    approved: "Aprovado",
    rejected: "Rejeitado",
    changes_requested: "Ajustes solicitados",
    stale: "Revisão desatualizada",
    unavailable: "Indisponível",
    available: "Disponível",
    ready: "Pronto",
    healthy: "Operacional",
    degraded: "Degradado",
    passed: "Aprovado",
    warning: "Atenção",
    needs_attention: "Requer atenção",
    needs_review: "Aguarda revisão",
    not_started: "Não iniciado",
    audio: "Narração",
    narration_alignment: "Sincronização",
    visual_plan: "Plano visual",
    production_timeline: "Timeline",
    storyboard: "Storyboard",
    storyboard_assets: "Imagens do storyboard",
    render: "Renderização",
    manual_edit_package: "Pacote para edição",
  };
  return labels[value] || value.replace(/_/g, " ");
}

export function StatusBadge({ value }: { value: string | null | undefined }) {
  const safe = value || "não disponível";
  const tone = statusTone(value);
  return (
    <span className={`status-badge ${tone}`} title={safe}>
      {statusLabel(safe)}
    </span>
  );
}

export function Alert({
  tone = "info",
  children,
}: {
  tone?: "info" | "warning" | "danger" | "success";
  children: ReactNode;
}) {
  return (
    <div
      className={`alert ${tone}`}
      role={tone === "danger" ? "alert" : "status"}
    >
      {children}
    </div>
  );
}

export function LoadingState({ label = "Carregando…" }: { label?: string }) {
  return (
    <div className="state-message" role="status">
      {label}
      <div className="loading-lines" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>
    </div>
  );
}

export function ErrorState({ message }: { message: string }) {
  return <Alert tone="danger">{message}</Alert>;
}

export function EmptyState({
  title,
  detail,
}: {
  title: string;
  detail?: string;
}) {
  return (
    <div className="state-message">
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
    </div>
  );
}

export function Progress({ value, label }: { value: number; label?: string }) {
  const percent = Math.max(0, Math.min(100, Math.round(value * 100)));
  return (
    <div className="progress-wrap">
      <div className="progress-label">
        <span>{label || "Progresso"}</span>
        <strong>{percent}%</strong>
      </div>
      <progress max={100} value={percent}>
        {percent}%
      </progress>
    </div>
  );
}

export function DefinitionList({
  items,
}: {
  items: Array<[ReactNode, ReactNode]>;
}) {
  return (
    <dl className="definition-list">
      {items.map(([term, value], index) => (
        <div key={index}>
          <dt>{term}</dt>
          <dd>{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

export function TableWrap({ children }: { children: ReactNode }) {
  return (
    <div
      className="table-wrap"
      role="region"
      aria-label="Tabela com rolagem horizontal"
      tabIndex={0}
    >
      {children}
    </div>
  );
}

export function Diagnostics({
  value,
  label = "Diagnóstico técnico",
}: {
  value: unknown;
  label?: string;
}) {
  if (value == null) return null;
  return (
    <details className="diagnostics">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function ActionButton({
  capability,
  children,
  onClick,
  disabled,
}: {
  capability?: ActionCapability;
  children: ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  const state = actionAvailability(capability, disabled);
  const isDisabled = state.disabled;
  return (
    <span className="action-with-reason">
      <button
        disabled={isDisabled}
        onClick={onClick}
        title={capability?.reason || undefined}
      >
        {children}
      </button>
      {capability?.allowed === false && capability.reason && (
        <small>{capability.reason}</small>
      )}
    </span>
  );
}

export function actionAvailability(
  capability?: ActionCapability,
  disabled = false,
) {
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
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (props.open && !dialog.open) dialog.showModal();
    if (!props.open && dialog.open) dialog.close();
  }, [props.open]);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        props.onClose();
      }}
      onKeyDown={(event) => {
        if (event.key === "Escape") {
          event.preventDefault();
          props.onClose();
        }
      }}
      onClose={props.onClose}
    >
      <h2 id={titleId}>{props.title}</h2>
      <p>{props.consequence}</p>
      {props.invalidates?.length ? (
        <div>
          <strong>Será reconstruído:</strong>
          <ul>
            {props.invalidates.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <div className="dialog-actions">
        <button autoFocus onClick={props.onClose}>
          Cancelar
        </button>
        <button
          className={props.dangerous ? "danger-button" : ""}
          onClick={props.onConfirm}
        >
          {props.confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
