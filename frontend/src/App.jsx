import {
  Activity,
  ArrowLeftRight,
  AlertCircle,
  ArchiveRestore,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  Copy,
  Download,
  Eye,
  ExternalLink,
  FileCheck2,
  Folder,
  History,
  Laptop,
  Library,
  LoaderCircle,
  Mail,
  Pencil,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

const terminalStatuses = new Set(["success", "warning", "failed"]);
const filters = [
  ["all", "全部"],
  ["added", "新增"],
  ["updated", "更新"],
  ["deleted", "删除"],
  ["unchanged", "一致"],
];

async function apiRequest(path, { method = "GET", body, token } = {}) {
  const response = await fetch(path, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { "X-CSRF-Token": token } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
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

function AppLogo() {
  return (
    <div className="app-logo" aria-hidden="true">
      <span className="logo-sheet logo-sheet-back" />
      <span className="logo-sheet logo-sheet-front" />
      <ArrowRight size={15} strokeWidth={2.4} />
    </div>
  );
}

function IconButton({ label, children, ...props }) {
  return (
    <button className="icon-button" title={label} aria-label={label} {...props}>
      {children}
    </button>
  );
}

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
  return (
    <section className={`account-panel ${isSource ? "account-source" : "account-target"}`}>
      <div className="account-heading">
        <span className="account-role">{isSource ? "来源账号" : "目标账号"}</span>
        <IconButton label={isSource ? "编辑来源邮箱" : "编辑目标邮箱"} disabled={disabled || !selected}
          onClick={() => onEditEmail(selected)}><Pencil size={14} /></IconButton>
        <span className={`health-dot ${data?.health?.ok ? "health-ok" : "health-bad"}`}>
          {data?.health?.ok ? <Check size={12} /> : <AlertCircle size={12} />}
          {data ? healthLabel(data.health) : "未选择"}
        </span>
      </div>
      <div className="account-select-wrap">
        <UserRound size={18} />
        <select aria-label={isSource ? "来源账号" : "目标账号"} value={value || ""} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
          <option value="" disabled>选择账号</option>
          {choices.map((account) => (
            <option key={account.key} value={account.key}>
              {accountName(account)} · {account.modeLabel} · {account.sessionCount} 个会话{account.isPackage ? ` · ${formatDate(account.createdAt)}` : ""}
            </option>
          ))}
        </select>
        <ChevronDown size={16} aria-hidden="true" />
      </div>
      <div className="account-identity">
        <span className="account-email">{selected ? accountName(selected) : "待选择邮箱"}</span>
        <span>{selected ? `${selected.modeLabel}${selected.isLoggedIn ? " · 当前登录" : ""}${selected.emailSource === "manual" ? " · 手动绑定" : ""}` : ""}</span>
      </div>
      <label className="profile-picker">
        <span>组织 / 工作区</span>
        <select aria-label={isSource ? "来源工作区" : "目标工作区"} value={data?.profileId || ""} disabled={disabled || !selected}
          onChange={(event) => onProfileChange(event.target.value)}>
          {!selected && <option value="">未选择</option>}
          {selected?.profiles.map((profile) => (
            <option key={profile.profileId} value={profile.profileId}>{shortId(profile.profileId)} · {profile.sessionCount} 个会话</option>
          ))}
        </select>
      </label>
      <div className="account-metrics">
        <div>
          <strong>{data?.sessionCount ?? 0}</strong>
          <span>会话</span>
        </div>
        <div>
          <strong>{formatRelative(data?.newestSessionIso)}</strong>
          <span>最后更新</span>
        </div>
      </div>
    </section>
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
    <div className="summary-strip" aria-label="同步差异摘要">
      {items.map(([kind, label, value]) => (
        <div className={`summary-item summary-${kind}`} key={kind}>
          <span>{label}</span>
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

function SessionTable({ sessions }) {
  if (!sessions.length) {
    return (
      <div className="empty-state">
        <Search size={22} />
        <strong>没有匹配的会话</strong>
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
  if (!plan?.files?.length) return <div className="empty-state"><Library size={22} /><strong>没有关联记忆文件</strong></div>;
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
  const finished = terminalStatuses.has(job.status);
  const successful = job.status === "success" || job.status === "warning";
  return (
    <div className="modal-backdrop" role="presentation">
      <section className="progress-dialog" role="dialog" aria-modal="true" aria-label="同步进度">
        <div className="dialog-header">
          <div className={`dialog-symbol ${successful ? "dialog-success" : job.status === "failed" ? "dialog-failed" : ""}`}>
            {job.status === "failed" ? (
              <AlertCircle size={24} />
            ) : successful ? (
              <CheckCircle2 size={24} />
            ) : (
              <LoaderCircle className="spin" size={24} />
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
              <X size={19} />
            </IconButton>
          )}
        </div>
        <div className="progress-steps">
          {job.steps.map((step) => (
            <div className={`progress-step step-${step.state}`} key={step.id}>
              <span className="step-icon">
                {step.state === "completed" ? (
                  <Check size={15} />
                ) : step.state === "failed" ? (
                  <X size={15} />
                ) : (
                  <Circle size={12} />
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
                <Circle size={12} />
              </span>
              <div>
                <strong>准备任务</strong>
              </div>
            </div>
          )}
        </div>
        {job.error && <div className="job-error">{job.error}</div>}
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
        {job.result?.downloadUrl && (
          <a className="package-download primary-button" href={job.result.downloadUrl} download><Download size={17} />下载加密迁移包</a>
        )}
        {finished && (
          <button className="dialog-close-button" onClick={onClose}>
            完成
          </button>
        )}
      </section>
    </div>
  );
}

function HistoryView({ data, accounts, onReveal, onRestore, busy }) {
  if (!data) {
    return <div className="loading-block"><LoaderCircle className="spin" size={20} />读取运行记录</div>;
  }
  return (
    <div className="history-layout">
      <section className="history-section">
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
                {event.status === "failed" ? <AlertCircle size={16} /> : <CheckCircle2 size={16} />}
              </span>
              <div className="history-main">
                <strong>{({restore: "恢复备份", export: "导出账号", import: "打开迁移包"})[event.operation] || (event.automatic ? "自动同步" : "账号同步")}</strong>
                <span>
                  {accountName(event.source, accounts)} <ArrowRight size={12} /> {event.operation === "export" ? "加密迁移包" : event.operation === "import" ? "已载入" : accountName(event.target, accounts)}
                </span>
              </div>
              <div className="history-counts">
                {event.counts && (
                  <>
                    <span>+{event.counts.added}</span>
                    <span>~{event.counts.updated}</span>
                    <span>−{event.counts.deleted}</span>
                  </>
                )}
              </div>
              <time>{formatDate(event.completedAt)}</time>
            </div>
          ))}
        </div>
      </section>

      <section className="history-section">
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
              <ArchiveRestore size={18} />
              <div className="backup-main">
                <strong>{backup.backupFileCount} 个会话{backup.contextFileCount ? ` · ${backup.contextFileCount} 个上下文文件` : ""}</strong>
                <span>{formatDate(backup.createdAt)} · {accountName(backup.target, accounts)}</span>
              </div>
              <IconButton label="在 Finder 中显示" onClick={() => onReveal(backup.path)}>
                <ExternalLink size={17} />
              </IconButton>
              <button className="secondary-button" disabled={busy} onClick={() => onRestore(backup)}>
                <ArchiveRestore size={16} />
                恢复
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

function SettingsView({ settings, onUpdate, saving }) {
  return (
    <section className="settings-panel">
      <div className="section-heading">
        <div>
          <span className="eyebrow">本机偏好</span>
          <h2>自动化</h2>
        </div>
      </div>
      <div className="setting-row">
        <div className="setting-icon">
          <Activity size={19} />
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
          <Laptop size={19} />
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
        <Clock3 size={16} />
        <span>最近自动同步</span>
        <strong>{settings.lastAutoSyncAt ? formatDate(settings.lastAutoSyncAt) : "尚未运行"}</strong>
      </div>
      {settings.lastAutoError && (
        <div className="settings-error">
          <AlertCircle size={17} />
          {settings.lastAutoError}
        </div>
      )}
    </section>
  );
}

function EmailDialog({ account, token, onClose, onSaved }) {
  const [email, setEmail] = useState(account.email || "");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
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
    <form className="package-dialog email-dialog" role="dialog" aria-modal="true" aria-label="账号邮箱"
      onSubmit={(event) => { event.preventDefault(); save(email.trim()); }}>
      <div className="package-heading"><h2>账号邮箱</h2>
        <IconButton label="关闭" type="button" disabled={pending} onClick={onClose}><X size={18} /></IconButton>
      </div>
      <p className="email-account-meta">{account.displayName} · {account.modeLabel} · {account.sessionCount} 个会话</p>
      <label className="package-password">邮箱地址
        <div className="password-input"><Mail size={17} />
          <input type="email" aria-label="邮箱地址" autoComplete="email" maxLength={254} autoFocus required
            value={email} onChange={(event) => setEmail(event.target.value)} disabled={pending} />
        </div>
      </label>
      <details className="identity-details"><summary>账号标识</summary><code>{account.accountId}</code></details>
      {error && <p className="email-error" role="alert">{error}</p>}
      <div className="email-actions">
        {account.emailSource === "manual" && <button type="button" className="secondary-button" disabled={pending}
          onClick={() => save("")}>清除手动绑定</button>}
        <button className="primary-button" disabled={pending || !email.trim()}>
          {pending ? <LoaderCircle className="spin" size={17} /> : <Check size={17} />}保存邮箱
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
  return <div className="modal-backdrop">
    <form className="package-dialog" role="dialog" aria-modal="true" aria-label={mode === "export" ? "导出账号迁移包" : "导入账号迁移包"}
      onSubmit={(event) => { event.preventDefault(); onSubmit(password); }}>
      <div className="package-heading">
        <h2>{mode === "export" ? "导出账号迁移包" : "导入账号迁移包"}</h2>
        <IconButton label="关闭" type="button" onClick={onClose} disabled={pending}><X size={18} /></IconButton>
      </div>
      {file && <p className="package-file">{file.name}</p>}
      <label className="package-password">迁移口令
        <div className="password-input">
          <input aria-label="迁移口令" type={visible ? "text" : "password"} autoComplete="new-password" value={password}
            minLength={12} required onChange={(event) => { setPassword(event.target.value); setCopied(false); }} />
          <IconButton type="button" label={visible ? "隐藏口令" : "显示口令"} onClick={() => setVisible(!visible)}><Eye size={17} /></IconButton>
          {mode === "export" && <IconButton type="button" label="复制口令" onClick={async () => {
            try { await navigator.clipboard.writeText(password); setCopied(true); } catch { setVisible(true); }
          }}>{copied ? <Check size={17} /> : <Copy size={17} />}</IconButton>}
        </div>
      </label>
      <p className="package-note">{mode === "export" ? "新 Mac 导入时需要此口令，本工具不会保存口令。" : "解密后先比较差异，确认同步后才写入目标账号。"}</p>
      <button className="primary-button" disabled={pending || password.length < 12}>
        {pending ? <LoaderCircle className="spin" size={17} /> : mode === "export" ? <Download size={17} /> : <Upload size={17} />}
        {pending ? "正在提交" : mode === "export" ? "加密并导出" : "打开迁移包"}
      </button>
    </form>
  </div>;
}

export default function App() {
  const [state, setState] = useState(null);
  const [historyData, setHistoryData] = useState(null);
  const [activeTab, setActiveTab] = useState("compare");
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
  const requestNumber = useRef(0);
  const fileInput = useRef(null);
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

  const loadHistory = useCallback(async () => {
    try {
      setHistoryData(await apiRequest("/api/history"));
    } catch (requestError) {
      setError(requestError.message);
    }
  }, []);

  useEffect(() => {
    loadState(selection);
  }, []);

  useEffect(() => {
    if (activeTab === "history") loadHistory();
  }, [activeTab, loadHistory]);

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
          headers: { "Content-Type": "application/octet-stream", "X-CSRF-Token": state.csrfToken } });
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
        <AppLogo />
        <LoaderCircle className="spin" size={24} />
        <strong>正在读取 Claude 本地会话</strong>
      </main>
    );
  }

  if (!state) {
    return (
      <main className="boot-screen">
        <AlertCircle size={28} />
        <strong>{error || "无法读取本地会话"}</strong>
        <button className="primary-button" onClick={() => loadState(selection)}>重试</button>
      </main>
    );
  }

  const sourceValue = selection.source;
  const targetValue = selection.target;
  const busy = job && !terminalStatuses.has(job.status);
  const sameProfile = sourceValue === targetValue && state.source?.profileId === state.target?.profileId;
  const sourcePackage = state.accounts.find((account) => account.key === sourceValue)?.isPackage;
  const changedCount = state.plan.counts.added + state.plan.counts.updated + state.plan.counts.deleted;
  const totalChanges = changedCount + (state.memoryPlan?.changed || 0);

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <AppLogo />
            <div>
              <strong>Claude Recent Sync</strong>
              <span>本机会话控制台</span>
            </div>
          </div>
          <div className="topbar-actions">
            <span className="local-status"><span />本机运行</span>
            <span className="version">v{state.version}</span>
            <IconButton
              label="刷新"
              onClick={() => loadState(selection)}
              disabled={refreshing || busy}
            >
              <RefreshCw className={refreshing ? "spin" : ""} size={18} />
            </IconButton>
          </div>
        </div>
      </header>

      <main className="workspace">
        <div className="workspace-heading">
          <h1>上下文与记忆</h1>
          <div className="package-actions">
            <button className="secondary-button" disabled={busy || refreshing || dirty || !state.source || sourcePackage}
              onClick={() => setPackageDialog({ mode: "export" })}><Download size={16} />导出来源账号</button>
            <button className="secondary-button" disabled={busy || refreshing} onClick={() => fileInput.current.click()}>
              <Upload size={16} />导入迁移包</button>
            <input ref={fileInput} type="file" accept=".crsync" className="file-input" aria-label="账号迁移包文件"
              onChange={(event) => { const file = event.target.files?.[0]; if (file) setPackageDialog({ mode: "import", file }); event.target.value = ""; }} />
          </div>
        </div>
        {error && (
          <div className="error-banner">
            <AlertCircle size={18} />
            <span>{error}</span>
            <IconButton label="关闭" onClick={() => setError(null)}><X size={17} /></IconButton>
          </div>
        )}

        <section className="route-panel">
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
          <div className="route-arrow" aria-label="同步方向">
            {!sourcePackage ? <IconButton label="交换来源和目标" disabled={busy || refreshing} onClick={() => {
              const next = { ...selection, source: selection.target, sourceProfile: selection.targetProfile,
                target: selection.source, targetProfile: selection.sourceProfile, mappings: {} };
              setSelection(next); loadState(next);
            }}><ArrowLeftRight size={20} /></IconButton> : <ArrowRight size={22} />}
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
          <div className="sync-action-panel">
            <div className="sync-state">
              {sameProfile ? <AlertCircle size={17} /> : changedCount ? <Sparkles size={17} /> : <ShieldCheck size={17} />}
              <span>{!state.source || !state.target ? "待选择账号" : sameProfile ? "来源与目标相同" : totalChanges ? `${totalChanges} 项待同步`
                : !state.target?.health?.ok ? "转录待补全" : "两端已一致"}</span>
            </div>
            <button className="primary-button sync-button" onClick={startSync}
              disabled={busy || sameProfile || refreshing || dirty || !state.planId || state.blockers.length > 0}>
              {busy ? <LoaderCircle className="spin" size={18} /> : <ShieldCheck size={18} />}
              {!state.source || !state.target ? "请选择账号" : sameProfile ? "请选择不同账号" : sourcePackage || changedCount ? "同步到目标账号" : "重新校验"}
            </button>
          </div>
        </section>

        <SummaryStrip counts={state.plan.counts} />

        <section className="context-strip" aria-label="记忆范围">
          <div><Library size={18} /><strong>{state.context?.memoryFileCount ?? 0}</strong><span>项目记忆</span></div>
          <div><Folder size={17} /><strong>{state.context?.projectCount ?? 0}</strong><span>关联项目</span></div>
          <div><FileCheck2 size={17} /><strong>{state.context?.transcriptCount ?? 0}</strong><span>会话转录</span></div>
          <span className="storage-label">{sourcePackage ? "迁移包快照" : "项目记忆在本机账号间共享"}</span>
          <label className="global-memory-toggle"><input type="checkbox" checked={selection.includeGlobal} disabled={busy || refreshing}
            onChange={(event) => { const next = { ...selection, includeGlobal: event.target.checked }; setSelection(next); loadState(next); }} />
            包含全局记忆</label>
        </section>
        {state.blockers.length > 0 && !sameProfile && <div className="blocked-state"><AlertCircle size={16} />{state.blockers.join(" ")}</div>}
        {sourcePackage && <div className="scope-note">导入会更新相关项目的共享记忆。全局记忆启用时，影响本机全部账号。</div>}
        {state.projectMappings?.length > 0 && (
          <details className="mapping-panel" open={state.missingPaths?.length > 0}>
            <summary>项目路径映射 <span>{state.missingPaths?.length ? `${state.missingPaths.length} 个目录待调整` : "目录检查通过"}</span></summary>
            {state.projectMappings.map((row) => <label className="mapping-row" key={row.key}>
              <span title={row.source}>{row.source}</span><ArrowRight size={15} />
              <input aria-label={`目标路径 ${row.source}`} value={selection.mappings[row.source] ?? row.target} disabled={busy}
                onChange={(event) => { setDirty(true); setSelection({ ...selection, mappings: { ...selection.mappings, [row.source]: event.target.value } }); }}
                onBlur={() => loadState(selection)} />
            </label>)}
          </details>
        )}

        <nav className="tabs" aria-label="视图">
          <button className={activeTab === "compare" ? "active" : ""} onClick={() => setActiveTab("compare")}>
            <FileCheck2 size={17} />会话比较
          </button>
          <button className={activeTab === "history" ? "active" : ""} onClick={() => setActiveTab("history")}>
            <History size={17} />运行记录
          </button>
          <button className={activeTab === "memory" ? "active" : ""} onClick={() => setActiveTab("memory")}>
            <Library size={17} />记忆比较
          </button>
          <button className={activeTab === "settings" ? "active" : ""} onClick={() => setActiveTab("settings")}>
            <Settings size={17} />设置
          </button>
        </nav>

        {activeTab === "compare" && (
          <section className="comparison-panel">
            <div className="comparison-toolbar">
              <div className="segmented-control">
                {filters.map(([value, label]) => (
                  <button
                    key={value}
                    className={filter === value ? "active" : ""}
                    onClick={() => setFilter(value)}
                  >
                    {label}
                    <span>{state.plan.counts[value]}</span>
                  </button>
                ))}
              </div>
              <label className="search-box">
                <Search size={17} />
                <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索会话或目录" />
                {search && (
                  <button aria-label="清除搜索" title="清除搜索" onClick={() => setSearch("")}>
                    <X size={15} />
                  </button>
                )}
              </label>
            </div>
            <SessionTable sessions={filteredSessions} />
          </section>
        )}

        {activeTab === "history" && (
          <HistoryView
            data={historyData}
            accounts={state.accounts}
            onReveal={revealBackup}
            onRestore={setRestoreCandidate}
            busy={busy}
          />
        )}
        {activeTab === "memory" && <MemoryTable plan={state.memoryPlan} />}

        {activeTab === "settings" && (
          <SettingsView settings={state.settings} onUpdate={updateSettings} saving={settingsSaving} />
        )}
      </main>

      <footer className="app-footer">
        <span><ShieldCheck size={14} />本地处理</span>
        <span>{accountName(state.source, state.accounts)} → {accountName(state.target, state.accounts)}</span>
      </footer>

      <SyncProgress
        job={job}
        onClose={() => {
          setJob(null);
          loadState(selection, true);
        }}
      />

      {restoreCandidate && (
        <div className="modal-backdrop">
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-label="确认恢复备份">
            <div className="confirm-icon"><ArchiveRestore size={24} /></div>
            <h2>恢复这份同步前备份？</h2>
            <p>{restoreCandidate.backupFileCount} 个会话 · {formatDate(restoreCandidate.createdAt)}</p>
            <p>目标账号：{accountName(restoreCandidate.target, state.accounts)}</p>
            {restoreCandidate.contextFileCount > 0 && <p>同时恢复 {restoreCandidate.contextFileCount} 个上下文文件</p>}
            <div className="confirm-actions">
              <button className="secondary-button" onClick={() => setRestoreCandidate(null)}>取消</button>
              <button className="primary-button" onClick={restoreBackup}><ArchiveRestore size={17} />确认恢复</button>
            </div>
          </section>
        </div>
      )}

      {toast && <div className="toast"><Check size={16} />{toast}</div>}
      {packageDialog && <PackageDialog {...packageDialog} pending={packagePending} onClose={() => setPackageDialog(null)} onSubmit={runPackage} />}
      {emailAccount && <EmailDialog account={emailAccount} token={state.csrfToken} onClose={() => setEmailAccount(null)}
        onSaved={() => { setEmailAccount(null); loadState(selection); }} />}
    </div>
  );
}
