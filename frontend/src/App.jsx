import {
  Activity,
  AlertCircle,
  ArchiveRestore,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  ExternalLink,
  FileCheck2,
  Folder,
  History,
  Laptop,
  LoaderCircle,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Sparkles,
  UserRound,
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
  return `${value.slice(0, 8)}…`;
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

function accountOptionValue(accountId, profileId) {
  return `${accountId}|${profileId}`;
}

function parseAccountOption(value) {
  const [account, profile] = value.split("|");
  return { account, profile };
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

function AccountPanel({ role, data, profiles, value, onChange, disabled }) {
  const isSource = role === "source";
  const choices = profiles.filter(
    (profile) => profile.sessionCount > 0 || profile.isCurrentAccount || profile.label === data.label,
  );
  return (
    <section className={`account-panel ${isSource ? "account-source" : "account-target"}`}>
      <div className="account-heading">
        <span className="account-role">{isSource ? "上个账号 · 来源" : "当前账号 · 目标"}</span>
        <span className={`health-dot ${data.health?.ok ? "health-ok" : "health-bad"}`}>
          {data.health?.ok ? <Check size={12} /> : <AlertCircle size={12} />}
          {healthLabel(data.health)}
        </span>
      </div>
      <div className="account-select-wrap">
        <UserRound size={18} />
        <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
          {choices.map((profile) => (
            <option
              key={profile.label}
              value={accountOptionValue(profile.accountId, profile.profileId)}
            >
              {profile.isCurrentAccount ? "当前 · " : profile.isPreviousAccount ? "上个 · " : ""}
              {shortId(profile.accountId)} / {shortId(profile.profileId)}
            </option>
          ))}
        </select>
        <ChevronDown size={16} aria-hidden="true" />
      </div>
      <div className="account-metrics">
        <div>
          <strong>{data.sessionCount}</strong>
          <span>会话</span>
        </div>
        <div>
          <strong>{formatRelative(data.newestSessionIso)}</strong>
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
            <span className="eyebrow">{job.operation === "restore" ? "备份恢复" : "安全同步"}</span>
            <h2>
              {job.status === "failed"
                ? "任务未完成"
                : successful
                  ? job.status === "warning"
                    ? "会话已同步，元数据有刷新"
                    : "全部会话已同步"
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
              <span>目标会话</span>
            </div>
            <div>
              <strong>{job.result.targetHealth?.missingTranscripts?.length ?? 0}</strong>
              <span>缺失记录</span>
            </div>
            <div>
              <strong>{job.result.stoppedBackends?.length ?? 0}</strong>
              <span>旧后台已停止</span>
            </div>
          </div>
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

function HistoryView({ data, onReveal, onRestore, busy }) {
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
                <strong>{event.operation === "restore" ? "恢复备份" : event.automatic ? "自动同步" : "手动同步"}</strong>
                <span>
                  {shortId(event.source?.accountId)} <ArrowRight size={12} /> {shortId(event.target?.accountId)}
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
                <strong>{backup.backupFileCount} 个会话</strong>
                <span>{formatDate(backup.createdAt)} · {shortId(backup.target?.account_id || backup.target?.accountId)}</span>
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
          <strong>账号切换后自动同步</strong>
          <span>{settings.autoSync ? "已开启" : "已关闭"}</span>
        </div>
        <Toggle
          checked={settings.autoSync}
          onChange={(value) => onUpdate({ autoSync: value })}
          label="账号切换后自动同步"
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
  const [selection, setSelection] = useState({
    source: "previous",
    sourceProfile: null,
    target: "current",
    targetProfile: null,
  });
  const pollRef = useRef(null);

  const loadState = useCallback(async (nextSelection = selection, silent = false) => {
    if (!silent) setRefreshing(true);
    try {
      const query = new URLSearchParams();
      query.set("source", nextSelection.source);
      query.set("target", nextSelection.target);
      if (nextSelection.sourceProfile) query.set("sourceProfile", nextSelection.sourceProfile);
      if (nextSelection.targetProfile) query.set("targetProfile", nextSelection.targetProfile);
      const payload = await apiRequest(`/api/state?${query}`);
      setState(payload);
      setError(null);
      if (payload.latestJob && !terminalStatuses.has(payload.latestJob.status)) {
        setJob(payload.latestJob);
      }
    } catch (requestError) {
      setError(requestError.message);
    } finally {
      setLoading(false);
      setRefreshing(false);
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
          await loadState(selection, true);
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

  const changeSelection = async (side, value) => {
    const parsed = parseAccountOption(value);
    const next = {
      ...selection,
      [side]: parsed.account,
      [`${side}Profile`]: parsed.profile,
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
          source: selection.source,
          sourceProfile: selection.sourceProfile,
          target: selection.target,
          targetProfile: selection.targetProfile,
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

  const sourceValue = accountOptionValue(state.source.accountId, state.source.profileId);
  const targetValue = accountOptionValue(state.target.accountId, state.target.profileId);
  const busy = job && !terminalStatuses.has(job.status);
  const sameProfile = state.source.label === state.target.label;
  const changedCount = state.plan.counts.added + state.plan.counts.updated + state.plan.counts.deleted;

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
            profiles={state.profiles}
            value={sourceValue}
            onChange={(value) => changeSelection("source", value)}
            disabled={busy}
          />
          <div className="route-arrow" aria-label="同步方向">
            <ArrowRight size={22} />
          </div>
          <AccountPanel
            role="target"
            data={state.target}
            profiles={state.profiles}
            value={targetValue}
            onChange={(value) => changeSelection("target", value)}
            disabled={busy}
          />
          <div className="sync-action-panel">
            <div className="sync-state">
              {sameProfile ? <AlertCircle size={17} /> : changedCount ? <Sparkles size={17} /> : <ShieldCheck size={17} />}
              <span>{sameProfile ? "来源与目标相同" : changedCount ? `${changedCount} 项待同步` : "两端已一致"}</span>
            </div>
            <button className="primary-button sync-button" onClick={startSync} disabled={busy || sameProfile}>
              {busy ? <LoaderCircle className="spin" size={18} /> : <ShieldCheck size={18} />}
              {sameProfile ? "请选择不同账号" : changedCount ? "开始安全同步" : "重新校验"}
            </button>
          </div>
        </section>

        <SummaryStrip counts={state.plan.counts} />

        <nav className="tabs" aria-label="视图">
          <button className={activeTab === "compare" ? "active" : ""} onClick={() => setActiveTab("compare")}>
            <FileCheck2 size={17} />会话比较
          </button>
          <button className={activeTab === "history" ? "active" : ""} onClick={() => setActiveTab("history")}>
            <History size={17} />运行记录
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
            onReveal={revealBackup}
            onRestore={setRestoreCandidate}
            busy={busy}
          />
        )}

        {activeTab === "settings" && (
          <SettingsView settings={state.settings} onUpdate={updateSettings} saving={settingsSaving} />
        )}
      </main>

      <footer className="app-footer">
        <span><ShieldCheck size={14} />本地处理</span>
        <span>{shortId(state.source.accountId)} → {shortId(state.target.accountId)}</span>
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
            <div className="confirm-actions">
              <button className="secondary-button" onClick={() => setRestoreCandidate(null)}>取消</button>
              <button className="primary-button" onClick={restoreBackup}><ArchiveRestore size={17} />确认恢复</button>
            </div>
          </section>
        </div>
      )}

      {toast && <div className="toast"><Check size={16} />{toast}</div>}
    </div>
  );
}
