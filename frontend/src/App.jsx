import {
  Activity,
  AlertCircle,
  ArchiveRestore,
  ArrowLeftRight,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  Copy,
  Download,
  Eye,
  EyeOff,
  ExternalLink,
  FileCheck2,
  Folder,
  History,
  Laptop,
  Library,
  LoaderCircle,
  Lock,
  Mail,
  Menu,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Pencil,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  Sun,
  Upload,
  UserRound,
  X,
} from "lucide-react";
import { forwardRef, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

const terminalStatuses = new Set(["success", "warning", "failed"]);
const filters = [
  ["all", "全部"],
  ["added", "新增"],
  ["updated", "更新"],
  ["deleted", "删除"],
  ["unchanged", "一致"],
];
const views = [
  { id: "sync", label: "账号同步", icon: ArrowLeftRight },
  { id: "history", label: "运行记录", icon: History },
  { id: "settings", label: "设置", icon: Settings },
];
const viewIntro = {
  sync: ["上下文与记忆", "选择来源与目标账号，先比较差异，再同步会话、上下文与项目记忆。"],
  history: ["运行记录", "同步、导出、导入与恢复的执行结果，以及每次写入前自动保存的备份。"],
  settings: ["设置", "本机自动化偏好与界面外观。"],
};
const historyLabels = { restore: "恢复备份", export: "导出账号", import: "打开迁移包" };
const themeChoices = [
  ["light", "浅色", Sun],
  ["system", "跟随系统", Monitor],
  ["dark", "深色", Moon],
];
const THEME_KEY = "crs-theme";
const SIDEBAR_KEY = "crs-sidebar";

function readStored(key, fallback) {
  try {
    return window.localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writeStored(key, value) {
  try {
    if (value == null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    // Storage can be unavailable in private windows; the choice then lasts for this visit only.
  }
}

function normalizeTheme(value) {
  return value === "light" || value === "dark" ? value : "system";
}

function applyTheme(theme) {
  const root = document.documentElement;
  const forced = theme === "light" || theme === "dark";
  if (forced) root.dataset.theme = theme;
  else delete root.dataset.theme;
  // Keep the browser chrome (Safari tab bar) in step with an explicit choice.
  document.querySelectorAll('meta[name="theme-color"][data-scheme]').forEach((meta) => {
    const scheme = meta.dataset.scheme;
    meta.setAttribute("media", forced ? (scheme === theme ? "all" : "not all") : `(prefers-color-scheme: ${scheme})`);
  });
}

function canFocus(element) {
  if (!(element instanceof HTMLElement) || !element.isConnected || element.disabled || element.closest("[inert]")) return false;
  return element.checkVisibility
    ? element.checkVisibility({ visibilityProperty: true })
    : element.getClientRects().length > 0;
}

// The element that opened the outermost dialog, handed on when one dialog replaces another.
let dialogTrigger = null;

function restoreFocus(previous) {
  const heading = () => document.querySelector(".page-intro h1");
  let tries = 0;
  const attempt = () => {
    if (document.querySelector(".modal-backdrop")) return; // A follow-up dialog owns focus now.
    const active = document.activeElement;
    if (active && active !== document.body && active !== heading()) return; // Focus already moved on.
    if (canFocus(previous)) {
      previous.focus();
      return;
    }
    // A trigger disabled while the page refreshes comes back shortly; wait on the page heading.
    if (previous?.isConnected && previous.disabled && tries++ < 30) {
      heading()?.focus({ preventScroll: true });
      window.setTimeout(attempt, 100);
      return;
    }
    [document.querySelector(".menu-button"), heading()].find(canFocus)?.focus({ preventScroll: true });
  };
  window.requestAnimationFrame(attempt);
}

function useDialogFocus(ref, onEscape) {
  // Captured during render, before the page behind the dialog becomes inert.
  const [previous] = useState(() => {
    const active = document.activeElement;
    if (active?.closest?.(".modal-backdrop")) return dialogTrigger;
    dialogTrigger = active;
    return active;
  });
  const escapeRef = useRef(onEscape);
  escapeRef.current = onEscape;
  useEffect(() => {
    const root = ref.current;
    (root?.querySelector("[data-autofocus]") || root)?.focus();
    const onKey = (event) => {
      if (event.key === "Escape") escapeRef.current?.();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      restoreFocus(previous);
    };
  }, [ref, previous]);
}

const offlineMessage = "无法连接本机服务，请确认 Claude Recent Sync 仍在运行后重试。";

async function apiRequest(path, { method = "GET", body, token } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: {
        ...(body ? { "Content-Type": "application/json" } : {}),
        ...(token ? { "X-CSRF-Token": token } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new Error(offlineMessage);
  }
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `请求失败 (${response.status})`);
  }
  return payload;
}

function shortId(value) {
  if (!value) return "未识别";
  if (value.length <= 12) return value;
  return `${value.slice(0, 8)}…`;
}

function accountName(data, accounts = []) {
  if (!data) return "未选择";
  const key = data.accountKey || data.key;
  const id = data.accountId || data.account_id;
  const deployment = data.deployment || (data.session_dir?.includes("/Claude-3p/") ? "Claude-3p"
    : data.session_dir?.includes("/Claude/") ? "Claude" : null);
  const matches = key ? accounts.filter((row) => row.key === key)
    : accounts.filter((row) => !row.isPackage && row.accountId === id && (!deployment || row.deployment === deployment));
  const row = matches.length === 1 ? matches[0] : null;
  return row?.email || data.email || row?.displayName || data.displayName || "未绑定邮箱";
}

function formatDate(value, includeTime = true) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(date);
}

function formatRelative(value) {
  if (!value) return "暂无记录";
  const delta = Date.now() - new Date(value).getTime();
  if (delta < 60_000) return "刚刚";
  if (delta < 3_600_000) return `${Math.max(1, Math.floor(delta / 60_000))} 分钟前`;
  if (delta < 86_400_000) return `${Math.floor(delta / 3_600_000)} 小时前`;
  return formatDate(value);
}

function eventTitle(event) {
  return historyLabels[event.operation] || (event.automatic ? "自动同步" : "账号同步");
}

function eventDestination(event, accounts) {
  if (event.operation === "export") return "加密迁移包";
  if (event.operation === "import") return "已载入";
  return accountName(event.target, accounts);
}

function healthLabel(health) {
  if (!health) return "检查中";
  if (health.ok) return "记录完整";
  return `${health.invalidIndexes.length + health.missingTranscripts.length} 项异常`;
}

function StatusBadge({ kind, branchChanged = false }) {
  const labels = {
    added: "新增",
    updated: branchChanged ? "分支更新" : "更新",
    deleted: "删除",
    unchanged: "一致",
    shared: "共享",
  };
  return <span className={`status-badge status-${kind}`}>{labels[kind] || kind}</span>;
}

function AppMark({ size = 24, className = "" }) {
  return (
    <svg className={`app-mark ${className}`} width={size} height={size} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <rect x="2.75" y="2.75" width="12.5" height="14.5" rx="3.25" fill="none" stroke="currentColor" strokeWidth="1.6" opacity="0.55" />
      <rect x="8.5" y="6.75" width="13" height="14.5" rx="3.25" fill="currentColor" />
      <path d="M11.9 14h5.6m-2.1-2.3 2.3 2.3-2.3 2.3" fill="none" stroke="var(--mark-contrast)" strokeWidth="1.7"
        strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function Avatar({ label }) {
  const initial = label && /^[A-Za-z0-9]/.test(label) ? label[0].toUpperCase() : null;
  return (
    <span className="avatar" aria-hidden="true">
      {initial || <UserRound size={14} strokeWidth={2} />}
    </span>
  );
}

const IconButton = forwardRef(function IconButton({ label, children, className = "", ...props }, ref) {
  return (
    <button ref={ref} type="button" className={`icon-button ${className}`} title={label} aria-label={label} {...props}>
      {children}
    </button>
  );
});

function Toggle({ checked, onChange, label, disabled = false }) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      className={`toggle ${checked ? "toggle-on" : ""}`}
      onClick={() => onChange(!checked)}
      disabled={disabled}
      aria-label={label}
      title={label}
    >
      <span />
    </button>
  );
}

function AccountPanel({ role, data, accounts, value, onChange, onProfileChange, onEditEmail, disabled }) {
  const isSource = role === "source";
  const choices = accounts.filter((account) => isSource || !account.isPackage);
  const selected = accounts.find((account) => account.key === value);
  const name = selected ? accountName(selected) : null;
  return (
    <div className={`account-panel ${isSource ? "account-source" : "account-target"}`}>
      <div className="account-heading">
        <span className="account-role">{isSource ? "来源账号" : "目标账号"}</span>
        <IconButton className="account-edit" label={isSource ? "编辑来源邮箱" : "编辑目标邮箱"} disabled={disabled || !selected}
          onClick={() => onEditEmail(selected)}><Pencil size={13} /></IconButton>
        <span className={`health-pill ${!data ? "health-idle" : data.health?.ok ? "health-ok" : "health-bad"}`}>
          {data?.health?.ok ? <Check size={12} strokeWidth={2.4} /> : <AlertCircle size={12} />}
          {data ? healthLabel(data.health) : "未选择"}
        </span>
      </div>
      <div className="account-select">
        <Avatar label={name} />
        <select aria-label={isSource ? "来源账号" : "目标账号"} value={value || ""} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
          <option value="" disabled>选择账号</option>
          {choices.map((account) => (
            <option key={account.key} value={account.key}>
              {accountName(account)} · {account.modeLabel} · {account.sessionCount} 个会话{account.isPackage ? ` · ${formatDate(account.createdAt)}` : ""}
            </option>
          ))}
        </select>
        <ChevronDown className="select-chevron" size={16} aria-hidden="true" />
      </div>
      <div className="account-identity">
        <span className="account-email">{name || "待选择邮箱"}</span>
        {selected && (
          <span className="account-tags">
            <span className={`tag ${selected.isPackage ? "tag-accent" : ""}`}>{selected.modeLabel}</span>
            {selected.isLoggedIn && <span className="tag tag-live"><span className="live-dot" />当前登录</span>}
            {selected.emailSource === "manual" && <span className="tag">手动绑定</span>}
          </span>
        )}
      </div>
      <label className="profile-picker">
        <span>组织 / 工作区</span>
        <span className="profile-select">
          <select aria-label={isSource ? "来源工作区" : "目标工作区"} value={data?.profileId || ""} disabled={disabled || !selected}
            onChange={(event) => onProfileChange(event.target.value)}>
            {!selected && <option value="">未选择</option>}
            {selected?.profiles.map((profile) => (
              <option key={profile.profileId} value={profile.profileId}>{shortId(profile.profileId)} · {profile.sessionCount} 个会话</option>
            ))}
          </select>
          <ChevronDown className="select-chevron" size={14} aria-hidden="true" />
        </span>
      </label>
      <div className="account-metrics">
        <div>
          <strong>{data?.sessionCount ?? 0}</strong>
          <span>会话</span>
        </div>
        <div className="metric-time">
          <strong>{formatRelative(data?.newestSessionIso)}</strong>
          <span>最后更新</span>
        </div>
      </div>
    </div>
  );
}

function SummaryStrip({ counts }) {
  const items = [
    ["added", "新增", counts.added],
    ["updated", "更新", counts.updated],
    ["deleted", "删除", counts.deleted],
    ["unchanged", "一致", counts.unchanged],
  ];
  return (
    <div className="summary-strip" role="group" aria-label="同步差异摘要">
      {items.map(([kind, label, value]) => (
        <div className={`summary-item summary-${kind}`} key={kind}>
          <span><i aria-hidden="true" />{label}</span>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}

function SessionSide({ data, emptyLabel }) {
  if (!data) {
    return <span className="empty-side">{emptyLabel}</span>;
  }
  return (
    <div className="session-side">
      <div className="session-side-primary">
        <span>{data.completedTurns ?? 0} 轮</span>
        <span>{formatDate(data.lastActivityAtIso)}</span>
      </div>
      <code title={data.cliSessionId}>{shortId(data.cliSessionId)}</code>
    </div>
  );
}

function SessionTable({ sessions, filtered, ready, noAccounts }) {
  if (!sessions.length) {
    return (
      <div className="empty-state">
        <span className="empty-icon">{filtered ? <Search size={20} /> : <FileCheck2 size={20} />}</span>
        <strong>{filtered ? "没有匹配的会话" : noAccounts ? "未检测到可用账号" : !ready ? "选择来源与目标账号后显示差异" : "两端都还没有会话"}</strong>
      </div>
    );
  }
  return (
    <div className="session-table">
      <div className="session-table-head">
        <span>状态</span>
        <span>会话</span>
        <span>来源记录</span>
        <span>目标记录</span>
      </div>
      {sessions.map((item) => {
        const main = item.source || item.target;
        return (
          <div className="session-row" key={item.indexName}>
            <div className="session-status-cell">
              <StatusBadge kind={item.kind} branchChanged={item.branchChanged} />
            </div>
            <div className="session-identity">
              <strong title={main?.title}>{main?.title || "无标题会话"}</strong>
              <span title={main?.cwd}>
                <Folder size={13} />
                {main?.cwd || "未记录目录"}
              </span>
            </div>
            <SessionSide data={item.source} emptyLabel="来源无此会话" />
            <SessionSide data={item.target} emptyLabel="目标无此会话" />
          </div>
        );
      })}
    </div>
  );
}

function MemoryTable({ plan }) {
  if (!plan?.files?.length) {
    return (
      <div className="memory-comparison">
        <div className="empty-state"><span className="empty-icon"><Library size={20} /></span><strong>没有关联记忆文件</strong></div>
      </div>
    );
  }
  return <section className="memory-comparison" aria-label="记忆文件比较">
    <div className="memory-counts">
      <span>新增 <strong>{plan.counts.added}</strong></span>
      <span>更新 <strong>{plan.counts.updated}</strong></span>
      <span>删除 <strong>{plan.counts.deleted}</strong></span>
      <span>{plan.counts.shared ? "共享" : "一致"} <strong>{plan.counts.shared || plan.counts.unchanged}</strong></span>
    </div>
    {plan.files.map((file) => <div className="memory-file" key={file.path}>
      <StatusBadge kind={file.kind} />
      <span>{file.path}</span>
      <small>{file.size < 1024 ? `${file.size} B` : `${(file.size / 1024).toFixed(1)} KB`}</small>
    </div>)}
  </section>;
}

function SyncProgress({ job, onClose }) {
  if (!job) return null;
  return <SyncProgressDialog job={job} onClose={onClose} />;
}

function SyncProgressDialog({ job, onClose }) {
  const dialog = useRef(null);
  const finished = terminalStatuses.has(job.status);
  const successful = job.status === "success" || job.status === "warning";
  const errorShownInStep = job.steps.some((step) => step.state === "failed" && step.detail === job.error);
  useDialogFocus(dialog, () => { if (finished) onClose(); });
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="dialog progress-dialog" role="dialog" aria-modal="true" aria-label="同步进度" ref={dialog} tabIndex={-1}>
        <div className="dialog-header">
          <div className={`dialog-symbol ${successful ? "dialog-success" : job.status === "failed" ? "dialog-failed" : ""}`}>
            {job.status === "failed" ? (
              <AlertCircle size={22} />
            ) : successful ? (
              <CheckCircle2 size={22} />
            ) : (
              <LoaderCircle className="spin" size={22} />
            )}
          </div>
          <div>
            <span className="eyebrow">{({ restore: "备份恢复", export: "导出账号", import: "打开迁移包" })[job.operation] || "账号同步"}</span>
            <h2>
              {job.status === "failed"
                ? "任务未完成"
                : successful
                  ? job.status === "warning"
                    ? "会话已同步，元数据有刷新"
                    : job.operation === "export" ? "账号迁移包已导出"
                      : job.operation === "import" ? "迁移包已就绪"
                      : job.operation === "restore" ? "备份已恢复" : "账号上下文已同步"
                  : "正在处理本地会话"}
            </h2>
          </div>
          {finished && (
            <IconButton label="关闭" onClick={onClose}>
              <X size={18} />
            </IconButton>
          )}
        </div>
        <div className="progress-steps">
          {job.steps.map((step) => (
            <div className={`progress-step step-${step.state}`} key={step.id}>
              <span className="step-icon">
                {step.state === "completed" ? (
                  <Check size={14} strokeWidth={2.4} />
                ) : step.state === "failed" ? (
                  <X size={14} strokeWidth={2.4} />
                ) : step.state === "running" ? (
                  <LoaderCircle className="spin" size={14} />
                ) : (
                  <Circle size={10} />
                )}
              </span>
              <div>
                <strong>{step.label}</strong>
                {step.detail && <span>{step.detail}</span>}
              </div>
            </div>
          ))}
          {!job.steps.length && (
            <div className="progress-step step-running">
              <span className="step-icon">
                <LoaderCircle className="spin" size={14} />
              </span>
              <div>
                <strong>准备任务</strong>
              </div>
            </div>
          )}
        </div>
        {job.error && !errorShownInStep && <div className="job-error">{job.error}</div>}
        {job.result && (
          <div className="result-grid">
            <div>
              <strong>{job.result.targetCountAfter}</strong>
              <span>会话</span>
            </div>
            <div>
              <strong>{job.result.targetHealth?.missingTranscripts?.length ?? 0}</strong>
              <span>缺失记录</span>
            </div>
            <div>
              <strong>{job.result.context?.memoryFileCount ?? 0}</strong>
              <span>项目记忆</span>
            </div>
          </div>
        )}
        {(job.result?.downloadUrl || finished) && (
          <div className="dialog-actions">
            {job.result?.downloadUrl && (
              <a className="primary-button" href={job.result.downloadUrl} download><Download size={16} />下载加密迁移包</a>
            )}
            {finished && (
              <button type="button" className="secondary-button" onClick={onClose}>
                完成
              </button>
            )}
          </div>
        )}
      </section>
    </div>
  );
}

function HistoryView({ data, accounts, onReveal, onRestore, busy }) {
  if (!data) {
    return <div className="loading-block"><LoaderCircle className="spin" size={18} />读取运行记录</div>;
  }
  return (
    <div className="history-layout">
      <section className="card history-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">运行日志</span>
            <h2>最近任务</h2>
          </div>
          <span className="section-count">{data.events.length}</span>
        </div>
        <div className="history-list">
          {data.events.length === 0 && <div className="empty-row">尚无运行记录</div>}
          {data.events.map((event) => (
            <div className="history-row" key={event.jobId || event.id}>
              <span className={`history-status history-${event.status}`}>
                {event.status === "failed" ? <AlertCircle size={15} /> : <CheckCircle2 size={15} />}
              </span>
              <div className="history-main">
                <strong>{eventTitle(event)}</strong>
                <span className="route-line" title={`${accountName(event.source, accounts)} → ${eventDestination(event, accounts)}`}>
                  <span className="route-name">{accountName(event.source, accounts)}</span>
                  <ArrowRight size={12} aria-hidden="true" />
                  <span className="route-name">{eventDestination(event, accounts)}</span>
                </span>
              </div>
              <div className="history-meta">
                {event.counts && (
                  <div className="history-counts">
                    <span className="count-added">+{event.counts.added}</span>
                    <span className="count-updated">~{event.counts.updated}</span>
                    <span className="count-deleted">−{event.counts.deleted}</span>
                  </div>
                )}
                <time>{formatDate(event.completedAt)}</time>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="card history-section">
        <div className="section-heading">
          <div>
            <span className="eyebrow">安全快照</span>
            <h2>同步前备份</h2>
          </div>
          <span className="section-count">{data.backups.length}</span>
        </div>
        <div className="backup-list">
          {data.backups.length === 0 && <div className="empty-row">尚无备份</div>}
          {data.backups.map((backup) => (
            <div className="backup-row" key={backup.id}>
              <span className="backup-icon"><ArchiveRestore size={16} /></span>
              <div className="backup-main">
                <strong>{backup.backupFileCount} 个会话{backup.contextFileCount ? ` · ${backup.contextFileCount} 个上下文文件` : ""}</strong>
                <span title={`${formatDate(backup.createdAt)} · ${accountName(backup.target, accounts)}`}>{formatDate(backup.createdAt)} · {accountName(backup.target, accounts)}</span>
              </div>
              <IconButton label="在 Finder 中显示" onClick={() => onReveal(backup.path)}>
                <ExternalLink size={16} />
              </IconButton>
              <button type="button" className="secondary-button" disabled={busy} onClick={() => onRestore(backup)}>
                <ArchiveRestore size={15} />
                恢复
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function SettingsView({ settings, onUpdate, saving, theme, onThemeChange }) {
  return (
    <div className="settings-layout">
      <section className="card settings-panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">本机偏好</span>
            <h2>自动化</h2>
          </div>
        </div>
        <div className="setting-row">
          <div className="setting-icon">
            <Activity size={18} />
          </div>
          <div className="setting-copy">
            <strong>普通模式换号自动同步</strong>
            <span>{settings.autoSync ? "已开启" : "已关闭"}</span>
          </div>
          <Toggle
            checked={settings.autoSync}
            onChange={(value) => onUpdate({ autoSync: value })}
            label="普通模式换号自动同步"
            disabled={saving}
          />
        </div>
        <div className="setting-row">
          <div className="setting-icon">
            <Laptop size={18} />
          </div>
          <div className="setting-copy">
            <strong>登录时运行</strong>
            <span>{settings.launchAtLogin ? "已开启" : "已关闭"}</span>
          </div>
          <Toggle
            checked={settings.launchAtLogin}
            onChange={(value) => onUpdate({ launchAtLogin: value })}
            label="登录时运行"
            disabled={saving}
          />
        </div>
        <div className="setting-status">
          <Clock3 size={15} />
          <span>最近自动同步</span>
          <strong>{settings.lastAutoSyncAt ? formatDate(settings.lastAutoSyncAt) : "尚未运行"}</strong>
        </div>
        {settings.lastAutoError && (
          <div className="settings-error">
            <AlertCircle size={16} />
            {settings.lastAutoError}
          </div>
        )}
      </section>

      <section className="card settings-panel">
        <div className="section-heading">
          <div>
            <span className="eyebrow">界面</span>
            <h2>外观</h2>
          </div>
        </div>
        <div className="theme-options" role="radiogroup" aria-label="界面外观">
          {themeChoices.map(([value, label, Icon]) => (
            <label key={value} className={`theme-option ${theme === value ? "active" : ""}`}>
              <input type="radio" name="crs-theme" value={value} className="visually-hidden"
                checked={theme === value} onChange={() => onThemeChange(value)} />
              <span className={`theme-preview theme-preview-${value}`} aria-hidden="true">
                <span className="preview-sidebar" />
                <span className="preview-main"><i /><i /><i /></span>
              </span>
              <span className="theme-label"><Icon size={14} aria-hidden="true" />{label}</span>
            </label>
          ))}
        </div>
      </section>
    </div>
  );
}

function EmailDialog({ account, token, onClose, onSaved }) {
  const [email, setEmail] = useState(account.email || "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const dialog = useRef(null);
  useDialogFocus(dialog, () => { if (!pending) onClose(); });
  const save = async (value) => {
    setPending(true);
    setError(null);
    try {
      await apiRequest("/api/accounts/email", { method: "POST", token,
        body: { accountKey: account.key, email: value } });
      onSaved();
    } catch (err) { setError(err.message); }
    finally { setPending(false); }
  };
  return <div className="modal-backdrop">
    <form className="dialog form-dialog email-dialog" role="dialog" aria-modal="true" aria-label="账号邮箱" ref={dialog} tabIndex={-1}
      onSubmit={(event) => { event.preventDefault(); save(email.trim()); }}>
      <div className="form-dialog-heading"><h2>账号邮箱</h2>
        <IconButton label="关闭" disabled={pending} onClick={onClose}><X size={18} /></IconButton>
      </div>
      <p className="dialog-meta">{account.displayName} · {account.modeLabel} · {account.sessionCount} 个会话</p>
      <label className="field-label">邮箱地址
        <div className="input-field"><Mail size={16} />
          <input type="email" aria-label="邮箱地址" autoComplete="email" maxLength={254} data-autofocus required
            value={email} onChange={(event) => setEmail(event.target.value)} disabled={pending} />
        </div>
      </label>
      <p className="dialog-note">只修改显示标签，不改变 Claude 登录状态或同步目标。</p>
      <details className="identity-details"><summary>账号标识</summary><code>{account.accountId}</code></details>
      {error && <p className="form-error" role="alert">{error}</p>}
      <div className="dialog-actions">
        {account.emailSource === "manual" && <button type="button" className="secondary-button" disabled={pending}
          onClick={() => save("")}>清除手动绑定</button>}
        <button className="primary-button" disabled={pending || !email.trim()}>
          {pending ? <LoaderCircle className="spin" size={16} /> : <Check size={16} />}保存邮箱
        </button>
      </div>
    </form>
  </div>;
}

function PackageDialog({ mode, file, onClose, onSubmit, pending }) {
  const [password, setPassword] = useState(() => mode === "export"
    ? Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => n.toString(16).padStart(2, "0")).join("") : "");
  const [visible, setVisible] = useState(false);
  const [copied, setCopied] = useState(false);
  const dialog = useRef(null);
  useDialogFocus(dialog, () => { if (!pending) onClose(); });
  return <div className="modal-backdrop">
    <form className="dialog form-dialog package-dialog" role="dialog" aria-modal="true" aria-label={mode === "export" ? "导出账号迁移包" : "导入账号迁移包"}
      ref={dialog} tabIndex={-1} onSubmit={(event) => { event.preventDefault(); onSubmit(password); }}>
      <div className="form-dialog-heading">
        <h2>{mode === "export" ? "导出账号迁移包" : "导入账号迁移包"}</h2>
        <IconButton label="关闭" onClick={onClose} disabled={pending}><X size={18} /></IconButton>
      </div>
      {file && <p className="dialog-meta package-file"><Lock size={13} />{file.name}</p>}
      <label className="field-label">迁移口令
        <div className="input-field">
          <input className="mono-input" aria-label="迁移口令" data-autofocus type={visible ? "text" : "password"} autoComplete="new-password" value={password}
            minLength={12} required onChange={(event) => { setPassword(event.target.value); setCopied(false); }} />
          <IconButton label={visible ? "隐藏口令" : "显示口令"} onClick={() => setVisible(!visible)}>
            {visible ? <EyeOff size={16} /> : <Eye size={16} />}
          </IconButton>
          {mode === "export" && <IconButton label="复制口令" onClick={async () => {
            try { await navigator.clipboard.writeText(password); setCopied(true); } catch { setVisible(true); }
          }}>{copied ? <Check size={16} /> : <Copy size={16} />}</IconButton>}
        </div>
      </label>
      <p className="dialog-note">{mode === "export" ? "新 Mac 导入时需要此口令，本工具不会保存口令。" : "解密后先比较差异，确认同步后才写入目标账号。"}</p>
      <button className="primary-button dialog-submit" disabled={pending || password.length < 12}>
        {pending ? <LoaderCircle className="spin" size={16} /> : mode === "export" ? <Download size={16} /> : <Upload size={16} />}
        {pending ? "正在提交" : mode === "export" ? "加密并导出" : "打开迁移包"}
      </button>
    </form>
  </div>;
}

function RestoreDialog({ backup, targetName, onCancel, onConfirm }) {
  const dialog = useRef(null);
  useDialogFocus(dialog, onCancel);
  return (
    <div className="modal-backdrop">
      <section className="dialog confirm-dialog" role="dialog" aria-modal="true" aria-label="确认恢复备份" ref={dialog} tabIndex={-1}>
        <div className="confirm-icon"><ArchiveRestore size={22} /></div>
        <h2>恢复这份同步前备份？</h2>
        <p>{backup.backupFileCount} 个会话 · {formatDate(backup.createdAt)}</p>
        <p>目标账号：{targetName}</p>
        {backup.contextFileCount > 0 && <p>同时恢复 {backup.contextFileCount} 个上下文文件</p>}
        <div className="confirm-actions">
          <button type="button" className="secondary-button" data-autofocus onClick={onCancel}>取消</button>
          <button type="button" className="primary-button" onClick={onConfirm}><ArchiveRestore size={16} />确认恢复</button>
        </div>
      </section>
    </div>
  );
}

export default function App() {
  const [state, setState] = useState(null);
  const [historyData, setHistoryData] = useState(null);
  const [view, setView] = useState("sync");
  const [syncTab, setSyncTab] = useState("sessions");
  const [filter, setFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);
  const [job, setJob] = useState(null);
  const [settingsSaving, setSettingsSaving] = useState(false);
  const [restoreCandidate, setRestoreCandidate] = useState(null);
  const [toast, setToast] = useState(null);
  const [packageDialog, setPackageDialog] = useState(null);
  const [packagePending, setPackagePending] = useState(false);
  const [emailAccount, setEmailAccount] = useState(null);
  const [dirty, setDirty] = useState(true);
  const [theme, setTheme] = useState(() => normalizeTheme(readStored(THEME_KEY, "system")));
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => readStored(SIDEBAR_KEY, "") === "collapsed");
  const [drawerOpen, setDrawerOpen] = useState(false);
  const requestNumber = useRef(0);
  const fileInput = useRef(null);
  const menuButton = useRef(null);
  const drawerClose = useRef(null);
  const collapseButton = useRef(null);
  const expandButton = useRef(null);
  const sidebarRef = useRef(null);
  const mainRef = useRef(null);
  const sidebarToggled = useRef(false);
  const drawerWasOpen = useRef(false);
  const [selection, setSelection] = useState({
    source: null,
    sourceProfile: null,
    target: null,
    targetProfile: null,
    includeGlobal: true,
    mappings: {},
  });
  const pollRef = useRef(null);

  const loadState = useCallback(async (nextSelection = selection, silent = false) => {
    const request = ++requestNumber.current;
    setDirty(true);
    if (!silent) setRefreshing(true);
    try {
      const query = new URLSearchParams();
      if (nextSelection.source) query.set("source", nextSelection.source);
      if (nextSelection.target) query.set("target", nextSelection.target);
      if (nextSelection.sourceProfile) query.set("sourceProfile", nextSelection.sourceProfile);
      if (nextSelection.targetProfile) query.set("targetProfile", nextSelection.targetProfile);
      query.set("includeGlobal", String(nextSelection.includeGlobal));
      query.set("mappings", JSON.stringify(nextSelection.mappings));
      const payload = await apiRequest(`/api/state?${query}`);
      if (request !== requestNumber.current) return;
      setState(payload);
      setSelection({ ...nextSelection, source: payload.source?.accountKey || nextSelection.source,
        sourceProfile: payload.source?.profileId || nextSelection.sourceProfile,
        target: payload.target?.accountKey || nextSelection.target,
        targetProfile: payload.target?.profileId || nextSelection.targetProfile });
      setDirty(false);
      setError(null);
      if (payload.latestJob && !terminalStatuses.has(payload.latestJob.status)) {
        setJob(payload.latestJob);
      }
    } catch (requestError) {
      if (request !== requestNumber.current) return;
      setError(requestError.message);
    } finally {
      if (request === requestNumber.current) {
        setLoading(false);
        setRefreshing(false);
      }
    }
  }, [selection]);

  const loadHistory = useCallback(async ({ quiet = false } = {}) => {
    try {
      setHistoryData(await apiRequest("/api/history"));
    } catch (requestError) {
      if (!quiet) setError(requestError.message);
    }
  }, []);

  useEffect(() => {
    loadState(selection);
    // The sidebar's recent tasks need history once; the history page reloads it on entry.
    loadHistory({ quiet: true });
  }, []);

  useEffect(() => {
    if (view === "history") loadHistory();
  }, [view, loadHistory]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const chooseTheme = (value) => {
    setTheme(value);
    writeStored(THEME_KEY, value === "system" ? null : value);
  };

  useEffect(() => {
    const onStorage = (event) => {
      if (event.key === THEME_KEY) setTheme(normalizeTheme(event.newValue));
    };
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, []);

  useEffect(() => {
    writeStored(SIDEBAR_KEY, sidebarCollapsed ? "collapsed" : null);
    if (sidebarToggled.current) {
      sidebarToggled.current = false;
      (sidebarCollapsed ? expandButton : collapseButton).current?.focus();
    }
  }, [sidebarCollapsed]);

  useEffect(() => {
    const narrow = window.matchMedia("(max-width: 900px)");
    const onChange = (event) => {
      if (event.matches) return;
      const hadFocus = sidebarRef.current?.contains(document.activeElement);
      setDrawerOpen(false);
      if (hadFocus) {
        window.requestAnimationFrame(() => {
          if (canFocus(document.activeElement)) return;
          [collapseButton.current, expandButton.current, document.querySelector(".sidebar-nav .active")].find(canFocus)?.focus();
        });
      }
    };
    narrow.addEventListener("change", onChange);
    return () => narrow.removeEventListener("change", onChange);
  }, []);

  const modalOpen = Boolean(job || restoreCandidate || packageDialog || emailAccount);

  // Layout effect so the page is interactive again before a closing dialog restores focus.
  useLayoutEffect(() => {
    if (mainRef.current) mainRef.current.inert = drawerOpen || modalOpen;
    if (sidebarRef.current) sidebarRef.current.inert = modalOpen;
    document.documentElement.classList.toggle("drawer-lock", drawerOpen);
  }, [drawerOpen, modalOpen, loading, state === null]);

  useEffect(() => {
    if (!drawerOpen) {
      if (drawerWasOpen.current) {
        drawerWasOpen.current = false;
        if (window.matchMedia("(max-width: 900px)").matches && !document.querySelector(".modal-backdrop")) {
          menuButton.current?.focus();
        }
      }
      return undefined;
    }
    drawerWasOpen.current = true;
    drawerClose.current?.focus();
    const onKey = (event) => {
      if (event.key === "Escape" && !document.querySelector(".modal-backdrop")) setDrawerOpen(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  useEffect(() => {
    if (!job?.id || terminalStatuses.has(job.status)) return undefined;
    pollRef.current = window.setInterval(async () => {
      try {
        const nextJob = await apiRequest(`/api/jobs/${job.id}`);
        setJob(nextJob);
        if (terminalStatuses.has(nextJob.status)) {
          window.clearInterval(pollRef.current);
          const next = nextJob.result?.sourceAccountKey && nextJob.operation === "import"
            ? { ...selection, source: nextJob.result.sourceAccountKey, sourceProfile: null, mappings: {} } : selection;
          await loadState(next, true);
          await loadHistory();
        }
      } catch (requestError) {
        setError(requestError.message);
        window.clearInterval(pollRef.current);
      }
    }, 450);
    return () => window.clearInterval(pollRef.current);
  }, [job?.id, job?.status, loadHistory, loadState, selection]);

  useEffect(() => {
    if (!toast) return undefined;
    const timeout = window.setTimeout(() => setToast(null), 2600);
    return () => window.clearTimeout(timeout);
  }, [toast]);

  const changeSelection = async (side, value, profileOnly = false) => {
    const next = {
      ...selection,
      ...(profileOnly ? { [`${side}Profile`]: value } : { [side]: value, [`${side}Profile`]: null }),
      mappings: {},
    };
    setSelection(next);
    await loadState(next);
  };

  const startSync = async () => {
    try {
      const payload = await apiRequest("/api/sync", {
        method: "POST",
        token: state.csrfToken,
        body: {
          planId: state.planId,
        },
      });
      setJob({
        id: payload.jobId,
        operation: "sync",
        status: "queued",
        steps: [],
      });
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const runPackage = async (password) => {
    setPackagePending(true);
    try {
      let payload;
      if (packageDialog.mode === "export") {
        payload = await apiRequest("/api/packages/export", { method: "POST", token: state.csrfToken,
          body: { source: selection.source, sourceProfile: selection.sourceProfile, password, includeGlobal: selection.includeGlobal } });
      } else {
        const upload = await fetch("/api/packages/upload", { method: "POST", body: packageDialog.file,
          headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": state.csrfToken } })
          .catch(() => { throw new Error(offlineMessage); });
        const uploaded = await upload.json();
        if (!upload.ok) throw new Error(uploaded.error || "迁移包上传失败");
        payload = await apiRequest("/api/packages/open", { method: "POST", token: state.csrfToken,
          body: { uploadId: uploaded.uploadId, password } });
      }
      setJob({ id: payload.jobId, operation: packageDialog.mode, status: "queued", steps: [] });
      setPackageDialog(null);
    } catch (requestError) {
      setError(requestError.message);
      setPackageDialog(null);
    } finally { setPackagePending(false); }
  };

  const updateSettings = async (updates) => {
    setSettingsSaving(true);
    try {
      const payload = await apiRequest("/api/settings", {
        method: "POST",
        token: state.csrfToken,
        body: updates,
      });
      setState((current) => ({ ...current, settings: payload.settings }));
      setToast("设置已保存");
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setSettingsSaving(false);
    }
  };

  const revealBackup = async (path) => {
    try {
      await apiRequest("/api/reveal", {
        method: "POST",
        token: state.csrfToken,
        body: { path },
      });
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const restoreBackup = async () => {
    const backup = restoreCandidate;
    setRestoreCandidate(null);
    try {
      const payload = await apiRequest("/api/restore", {
        method: "POST",
        token: state.csrfToken,
        body: { backupId: backup.id },
      });
      setJob({
        id: payload.jobId,
        operation: "restore",
        status: "queued",
        steps: [],
      });
    } catch (requestError) {
      setError(requestError.message);
    }
  };

  const filteredSessions = useMemo(() => {
    if (!state) return [];
    const normalized = search.trim().toLowerCase();
    return state.plan.sessions.filter((item) => {
      if (filter !== "all" && item.kind !== filter) return false;
      if (!normalized) return true;
      const source = item.source || {};
      const target = item.target || {};
      return [source.title, target.title, source.cwd, target.cwd, source.cliSessionId, target.cliSessionId]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(normalized));
    });
  }, [filter, search, state]);

  if (loading) {
    return (
      <main className="boot-screen">
        <AppMark size={40} className="boot-mark" />
        <span className="boot-status"><LoaderCircle className="spin" size={16} />正在读取 Claude 本地会话</span>
      </main>
    );
  }

  if (!state) {
    return (
      <main className="boot-screen">
        <AppMark size={40} className="boot-mark" />
        <span className="boot-status boot-error"><AlertCircle size={16} />{error || "无法读取本地会话"}</span>
        <button type="button" className="primary-button" onClick={() => loadState(selection)}>重试</button>
      </main>
    );
  }

  const sourceValue = selection.source;
  const targetValue = selection.target;
  const busy = job && !terminalStatuses.has(job.status);
  const noAccounts = state.accounts.length === 0;
  const bothChosen = Boolean(state.source && state.target);
  const sameProfile = bothChosen && sourceValue === targetValue && state.source.profileId === state.target.profileId;
  const sourcePackage = state.accounts.find((account) => account.key === sourceValue)?.isPackage;
  const changedCount = state.plan.counts.added + state.plan.counts.updated + state.plan.counts.deleted;
  const totalChanges = changedCount + (state.memoryPlan?.changed || 0);
  const loggedInAccounts = state.accounts.filter((account) => account.isLoggedIn && !account.isPackage);
  const loggedIn = loggedInAccounts[0];
  const recentEvents = historyData?.events.slice(0, 6) || [];
  const [title, subtitle] = viewIntro[view];
  const exportDisabled = busy || refreshing || dirty || !state.source || sourcePackage;
  const railLabel = (label) => (sidebarCollapsed ? { "aria-label": label, title: label } : {});

  const navigate = (next) => {
    setView(next);
    setDrawerOpen(false);
    window.scrollTo({ top: 0 });
  };

  const toggleSidebar = (collapsed) => {
    sidebarToggled.current = true;
    setSidebarCollapsed(collapsed);
  };

  return (
    <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""} ${drawerOpen ? "drawer-open" : ""}`}>
      <aside className="sidebar" id="app-sidebar" aria-label="侧栏" ref={sidebarRef}
        role={drawerOpen ? "dialog" : undefined} aria-modal={drawerOpen || undefined}>
        <div className="sidebar-header">
          <div className="brand" title="Claude Recent Sync">
            <AppMark size={22} />
            <span className="brand-name">Claude Recent Sync</span>
          </div>
          <IconButton className="sidebar-collapse" label="收起侧栏" ref={collapseButton} aria-controls="app-sidebar"
            aria-expanded={!sidebarCollapsed} onClick={() => toggleSidebar(true)}>
            <PanelLeftClose size={18} />
          </IconButton>
          <IconButton className="drawer-close" label="关闭侧栏" ref={drawerClose} onClick={() => setDrawerOpen(false)}>
            <X size={18} />
          </IconButton>
        </div>

        <div className="sidebar-actions">
          <button type="button" className="sidebar-primary" disabled={busy || refreshing} {...railLabel("导入迁移包")}
            onClick={() => { setDrawerOpen(false); fileInput.current.click(); }}>
            <span className="sidebar-primary-icon"><Upload size={14} strokeWidth={2.2} /></span>
            <span className="sidebar-text">导入迁移包</span>
          </button>
          <button type="button" className="sidebar-item" disabled={exportDisabled} {...railLabel("导出来源账号")}
            onClick={() => { setDrawerOpen(false); setPackageDialog({ mode: "export" }); }}>
            <Download size={16} /><span className="sidebar-text">导出来源账号</span>
          </button>
          <input ref={fileInput} type="file" accept=".crsync" className="file-input" aria-label="账号迁移包文件"
            onChange={(event) => { const file = event.target.files?.[0]; if (file) setPackageDialog({ mode: "import", file }); event.target.value = ""; }} />
        </div>

        <nav className="sidebar-nav" aria-label="视图">
          {views.map(({ id, label, icon: Icon }) => (
            <button type="button" key={id} className={`sidebar-item ${view === id ? "active" : ""}`}
              aria-current={view === id ? "page" : undefined} onClick={() => navigate(id)}
              {...railLabel(id === "sync" && totalChanges > 0 ? `${label}，${totalChanges} 项待同步` : label)}>
              <Icon size={16} />
              <span className="sidebar-text">{label}</span>
              {id === "sync" && totalChanges > 0 && (
                <>
                  <span className="nav-count" aria-hidden="true">{totalChanges}</span>
                  <span className="visually-hidden">，{totalChanges} 项待同步</span>
                </>
              )}
            </button>
          ))}
        </nav>

        <div className="sidebar-recents">
          <span className="sidebar-label">最近任务</span>
          {recentEvents.length === 0 && <span className="sidebar-empty">尚无运行记录</span>}
          {recentEvents.map((event) => (
            <button type="button" className="recent-item" key={event.jobId || event.id}
              title={`${formatDate(event.completedAt)} · ${accountName(event.source, state.accounts)} → ${eventDestination(event, state.accounts)}`}
              onClick={() => navigate("history")}>
              <span className={`recent-dot recent-${event.status}`} aria-hidden="true" />
              <span className="recent-title">{eventTitle(event)}</span>
              <span className="recent-time">{formatRelative(event.completedAt)}</span>
            </button>
          ))}
        </div>

        <div className="sidebar-footer">
          <div className="sidebar-account"
            title={loggedInAccounts.map((account) => `${accountName(account)} · ${account.modeLabel}`).join("\n") || undefined}>
            <Avatar label={loggedIn ? accountName(loggedIn) : null} />
            <div>
              <strong>{loggedIn ? accountName(loggedIn) : "未检测到登录账号"}</strong>
              <span>{loggedIn
                ? `${loggedInAccounts.map((account) => account.modeLabel).join(" / ")} · 当前登录`
                : "打开 Claude Code 后刷新"}</span>
            </div>
          </div>
          <div className="sidebar-meta">
            <span><ShieldCheck size={13} />本地处理</span>
            <span>v{state.version}</span>
          </div>
        </div>
      </aside>
      <div className="drawer-scrim" aria-hidden="true" onClick={() => setDrawerOpen(false)} />

      <div className="main-column" ref={mainRef}>
        <header className="main-header">
          <IconButton className="menu-button" label="打开侧栏" ref={menuButton} aria-controls="app-sidebar"
            aria-expanded={drawerOpen} onClick={() => setDrawerOpen(true)}>
            <Menu size={18} />
          </IconButton>
          {sidebarCollapsed && (
            <IconButton className="expand-button" label="展开侧栏" ref={expandButton} aria-controls="app-sidebar"
              aria-expanded={false} onClick={() => toggleSidebar(false)}>
              <PanelLeftOpen size={18} />
            </IconButton>
          )}
          <div className="header-title">
            <AppMark size={18} className="header-mark" />
            <span className="header-view">{views.find((item) => item.id === view)?.label}</span>
            {view === "sync" && state.source && state.target && (
              <span className="header-route">{accountName(state.source, state.accounts)} → {accountName(state.target, state.accounts)}</span>
            )}
          </div>
          <div className="header-actions">
            <span className="local-status"><span />本机运行</span>
            <IconButton
              label="刷新"
              onClick={() => { loadState(selection); loadHistory({ quiet: view !== "history" }); }}
              disabled={refreshing || busy}
            >
              <RefreshCw className={refreshing ? "spin" : ""} size={17} />
            </IconButton>
          </div>
        </header>

        <main className="workspace">
          <div className="page-intro">
            <h1 tabIndex={-1}>
              {view === "sync" && <AppMark size={30} className="intro-mark" />}
              {title}
            </h1>
            <p>{subtitle}</p>
          </div>

          {error && (
            <div className="error-banner" role="alert">
              <AlertCircle size={17} />
              <span>{error}</span>
              <IconButton label="关闭" onClick={() => setError(null)}><X size={16} /></IconButton>
            </div>
          )}

          {view === "sync" && (
            <>
              <section className="route-card" aria-label="同步方向">
                <div className="route-accounts">
                  <AccountPanel
                    role="source"
                    data={state.source}
                    accounts={state.accounts}
                    value={sourceValue}
                    onChange={(value) => changeSelection("source", value)}
                    onProfileChange={(value) => changeSelection("source", value, true)}
                    onEditEmail={setEmailAccount}
                    disabled={busy || refreshing}
                  />
                  <div className="route-arrow">
                    {!sourcePackage ? <IconButton className="swap-button" label="交换来源和目标" disabled={busy || refreshing} onClick={() => {
                      const next = { ...selection, source: selection.target, sourceProfile: selection.targetProfile,
                        target: selection.source, targetProfile: selection.sourceProfile, mappings: {} };
                      setSelection(next); loadState(next);
                    }}><ArrowLeftRight size={16} /></IconButton> : <span className="route-direction" role="img" aria-label="从迁移包同步到目标账号"><ArrowRight size={16} /></span>}
                  </div>
                  <AccountPanel
                    role="target"
                    data={state.target}
                    accounts={state.accounts}
                    value={targetValue}
                    onChange={(value) => changeSelection("target", value)}
                    onProfileChange={(value) => changeSelection("target", value, true)}
                    onEditEmail={setEmailAccount}
                    disabled={busy || refreshing}
                  />
                </div>
                <div className="route-footer">
                  <div className="context-chips" role="group" aria-label="记忆范围">
                    <span className="chip"><Library size={14} /><strong>{state.context?.memoryFileCount ?? 0}</strong>项目记忆</span>
                    <span className="chip"><Folder size={14} /><strong>{state.context?.projectCount ?? 0}</strong>关联项目</span>
                    <span className="chip"><FileCheck2 size={14} /><strong>{state.context?.transcriptCount ?? 0}</strong>会话转录</span>
                    <label className="chip chip-toggle"><input type="checkbox" checked={selection.includeGlobal} disabled={busy || refreshing}
                      onChange={(event) => { const next = { ...selection, includeGlobal: event.target.checked }; setSelection(next); loadState(next); }} />
                      包含全局记忆</label>
                    <span className="storage-label">{sourcePackage ? "迁移包快照" : "项目记忆在本机账号间共享"}</span>
                  </div>
                  <div className="sync-action">
                    <span className="sync-state">
                      {!bothChosen ? <Circle size={15} /> : sameProfile ? <AlertCircle size={15} /> : changedCount ? <Sparkles size={15} /> : <ShieldCheck size={15} />}
                      {!state.source || !state.target ? "待选择账号" : sameProfile ? "来源与目标相同" : totalChanges ? `${totalChanges} 项待同步`
                        : !state.target?.health?.ok ? "转录待补全" : "两端已一致"}
                    </span>
                    <button type="button" className="send-button" onClick={startSync}
                      disabled={busy || sameProfile || refreshing || dirty || !state.planId || state.blockers.length > 0}>
                      {busy ? <LoaderCircle className="spin" size={17} /> : <ShieldCheck size={17} />}
                      {!state.source || !state.target ? "请选择账号" : sameProfile ? "请选择不同账号" : sourcePackage || changedCount ? "同步到目标账号" : "重新校验"}
                    </button>
                  </div>
                </div>
              </section>

              {state.blockers.length > 0 && !sameProfile && (
                <div className={`callout ${noAccounts ? "callout-warning" : bothChosen ? "callout-danger" : "callout-neutral"}`}>
                  {noAccounts || bothChosen ? <AlertCircle size={16} /> : <Circle size={14} />}{state.blockers.join(" ")}
                </div>
              )}
              {sourcePackage && <div className="callout callout-warning"><AlertCircle size={16} />导入会更新相关项目的共享记忆。全局记忆启用时，影响本机全部账号。</div>}
              {state.projectMappings?.length > 0 && (
                <details className="mapping-panel" open={state.missingPaths?.length > 0}>
                  <summary>
                    <ChevronDown className="mapping-chevron" size={16} />
                    项目路径映射
                    <span className={state.missingPaths?.length ? "mapping-warning" : ""}>{state.missingPaths?.length ? `${state.missingPaths.length} 个目录待调整` : "目录检查通过"}</span>
                  </summary>
                  {state.projectMappings.map((row) => <label className="mapping-row" key={row.key}>
                    <span title={row.source}>{row.source}</span><ArrowRight size={15} />
                    <input aria-label={`目标路径 ${row.source}`} value={selection.mappings[row.source] ?? row.target} disabled={busy}
                      onChange={(event) => { setDirty(true); setSelection({ ...selection, mappings: { ...selection.mappings, [row.source]: event.target.value } }); }}
                      onBlur={() => loadState(selection)} />
                  </label>)}
                </details>
              )}

              <SummaryStrip counts={state.plan.counts} />

              <div className="view-switch" role="group" aria-label="比较内容">
                <button type="button" aria-pressed={syncTab === "sessions"} className={syncTab === "sessions" ? "active" : ""}
                  onClick={() => setSyncTab("sessions")}>
                  <FileCheck2 size={15} />会话比较<span>{state.plan.counts.all}</span>
                </button>
                <button type="button" aria-pressed={syncTab === "memory"} className={syncTab === "memory" ? "active" : ""}
                  onClick={() => setSyncTab("memory")}>
                  <Library size={15} />记忆比较<span>{state.memoryPlan?.files?.length ?? 0}</span>
                </button>
              </div>

              {syncTab === "sessions" && (
                <section className="comparison-panel" aria-label="会话比较">
                  <div className="comparison-toolbar">
                    <div className="filter-pills" role="group" aria-label="按状态筛选">
                      {filters.map(([value, label]) => (
                        <button
                          type="button"
                          key={value}
                          className={filter === value ? "active" : ""}
                          aria-pressed={filter === value}
                          onClick={() => setFilter(value)}
                        >
                          {label}
                          <span>{state.plan.counts[value]}</span>
                        </button>
                      ))}
                    </div>
                    <label className="search-box">
                      <Search size={16} />
                      <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索会话或目录" aria-label="搜索会话或目录" />
                      {search && (
                        <button type="button" aria-label="清除搜索" title="清除搜索" onClick={() => setSearch("")}>
                          <X size={14} />
                        </button>
                      )}
                    </label>
                  </div>
                  <SessionTable sessions={filteredSessions} filtered={Boolean(search.trim()) || filter !== "all"} ready={bothChosen} noAccounts={noAccounts} />
                </section>
              )}
              {syncTab === "memory" && <MemoryTable plan={state.memoryPlan} />}
            </>
          )}

          {view === "history" && (
            <HistoryView
              data={historyData}
              accounts={state.accounts}
              onReveal={revealBackup}
              onRestore={setRestoreCandidate}
              busy={busy}
            />
          )}

          {view === "settings" && (
            <SettingsView settings={state.settings} onUpdate={updateSettings} saving={settingsSaving}
              theme={theme} onThemeChange={chooseTheme} />
          )}

          <p className="workspace-footnote">
            <ShieldCheck size={13} />
            所有读取与写入都在本机完成。非 Anthropic 官方工具。
          </p>
        </main>
      </div>

      <SyncProgress
        job={job}
        onClose={() => {
          setJob(null);
          loadState(selection, true);
        }}
      />

      {restoreCandidate && (
        <RestoreDialog backup={restoreCandidate} targetName={accountName(restoreCandidate.target, state.accounts)}
          onCancel={() => setRestoreCandidate(null)} onConfirm={restoreBackup} />
      )}

      {toast && <div className="toast" role="status"><Check size={15} />{toast}</div>}
      {packageDialog && <PackageDialog {...packageDialog} pending={packagePending} onClose={() => setPackageDialog(null)} onSubmit={runPackage} />}
      {emailAccount && <EmailDialog account={emailAccount} token={state.csrfToken} onClose={() => setEmailAccount(null)}
        onSaved={() => { setEmailAccount(null); loadState(selection); }} />}
    </div>
  );
}
