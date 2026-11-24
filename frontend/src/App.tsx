import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Bell, CheckCircle2,
  ChevronRight, CircleHelp, Clock3, Cpu, ExternalLink, Gauge, LayoutDashboard,
  RefreshCw, Server, ShieldCheck, ShieldAlert, Workflow, Zap,
} from "lucide-react";
import {
  Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

type AnyRecord = Record<string, any>;
type Snapshot = {
  health: AnyRecord | null; ready: AnyRecord | null; model: AnyRecord | null;
  sources: AnyRecord | null; series: AnyRecord[]; alerts: AnyRecord[];
  audit: AnyRecord[]; policy: AnyRecord | null; canary: AnyRecord[];
  errors: string[];
};
const empty: Snapshot = { health:null, ready:null, model:null, sources:null, series:[], alerts:[], audit:[], policy:null, canary:[], errors:[] };
const get = async (path: string) => {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
};
const first = (obj: AnyRecord | null, keys: string[], fallback: any = "—") => {
  if (!obj) return fallback;
  for (const key of keys) if (obj[key] !== undefined && obj[key] !== null) return obj[key];
  return fallback;
};
const pretty = (value: unknown) => typeof value === "number" ? value.toFixed(3) : String(value ?? "—");
const time = (value: unknown) => {
  if (!value) return "—";
  const date = new Date(String(value));
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
};
const riskValue = (series: AnyRecord) => {
  const prediction = series.prediction ?? {};
  const value = first(prediction, ["probability", "failure_probability", "risk", "risk_score"], null);
  return typeof value === "number" ? value : null;
};

function App() {
  const [data, setData] = useState<Snapshot>(empty);
  const [loading, setLoading] = useState(true);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [page, setPage] = useState("Overview");
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const endpoints = [
      ["health", "/health"], ["ready", "/ready"], ["model", "/v1/model"],
      ["sources", "/v1/sources"], ["series", "/v1/series"], ["alerts", "/v1/alerts?limit=50"],
      ["audit", "/v1/audit?limit=50"], ["policy", "/v1/policy"], ["canary", "/v1/canary?limit=20"],
    ] as const;
    const results = await Promise.all(endpoints.map(async ([key, path]) => {
      try { return [key, await get(path), null] as const; }
      catch (error) { return [key, null, error instanceof Error ? error.message : String(error)] as const; }
    }));
    const next: AnyRecord = { ...empty };
    const errors: string[] = [];
    for (const [key, value, error] of results) {
      if (value !== null) next[key] = value;
      if (error) errors.push(error);
    }
    setData({ ...empty, ...next, errors });
    setLastUpdated(new Date());
    setLoading(false);
    setRefreshing(false);
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const prometheus = data.sources?.prometheus;
  const isReady = data.ready?.status === "ready";
  const serviceCount = data.series.length;
  const alertCount = data.alerts.length;
  const remediation = data.policy?.config ?? {};
  const chartData = useMemo(() => data.series.map((item, index) => ({
    name: item.series_id ?? `Series ${index + 1}`,
    risk: riskValue(item) === null ? null : Number((riskValue(item)! * 100).toFixed(1)),
  })).filter(item => item.risk !== null), [data.series]);
  const activePage = (next: string) => setPage(next);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><div className="brand-mark"><Activity size={19}/></div><div><strong>PredictiveOps</strong><span>RELIABILITY CONSOLE</span></div></div>
        <div className="workspace"><span className="workspace-dot"/><div><b>Home server</b><small>Local environment</small></div><ChevronRight size={15}/></div>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <button className={page === "Overview" ? "nav-item active" : "nav-item"} onClick={() => activePage("Overview")}><LayoutDashboard size={17}/>Overview</button>
          <button className={page === "Predictions" ? "nav-item active" : "nav-item"} onClick={() => activePage("Predictions")}><Gauge size={17}/>Predictions<span className="nav-count">{serviceCount}</span></button>
          <button className={page === "Alerts & audit" ? "nav-item active" : "nav-item"} onClick={() => activePage("Alerts & audit")}><Bell size={17}/>Alerts & audit<span className="nav-count">{alertCount}</span></button>
          <button className={page === "Canary" ? "nav-item active" : "nav-item"} onClick={() => activePage("Canary")}><Workflow size={17}/>Canary releases</button>
        </nav>
        <div className="nav-label monitoring-label">MONITORING</div>
        <a className="nav-item link-item" href={`http://${window.location.hostname}:3000`} target="_blank" rel="noreferrer"><Activity size={17}/>Grafana <ExternalLink size={13}/></a>
        <a className="nav-item link-item" href="/docs" target="_blank" rel="noreferrer"><CircleHelp size={17}/>API reference <ExternalLink size={13}/></a>
        <div className="sidebar-bottom"><div className="secure-line"><ShieldCheck size={15}/>Private deployment</div><span>PredictiveOps · v0.8</span></div>
      </aside>

      <main className="main-area">
        <header className="topbar">
          <div className="breadcrumbs"><span>Workspace</span><ChevronRight size={14}/><b>{page}</b></div>
          <div className="top-actions">
            <div className="live-indicator"><i/>API {data.health?.status === "ok" ? "online" : "unavailable"}</div>
            <button className="icon-button" onClick={() => void refresh()} title="Refresh data" disabled={refreshing}><RefreshCw size={16} className={refreshing ? "spin" : ""}/></button>
            <div className="avatar">PO</div>
          </div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div><div className="eyebrow">SYSTEM OVERVIEW <span className="eyebrow-line"/></div><h1>{page}</h1><p>{page === "Overview" ? "Predictive service health, model readiness and operational signals." : page === "Predictions" ? "Current model assessments for monitored service series." : page === "Alerts & audit" ? "Alert history and gated remediation decisions." : "Deployment validation history and candidate release decisions."}</p></div>
            <div className="updated"><Clock3 size={14}/><span>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : "Waiting for data"}</span></div>
          </div>

          {data.errors.length > 0 && <div className="notice warning"><AlertTriangle size={16}/><div><b>Some API endpoints could not be loaded</b><span>{data.errors.join(" · ")}</span></div></div>}

          {page === "Overview" && <>
            <section className="stat-grid">
              <StatCard icon={<ShieldCheck/>} label="MODEL STATUS" value={isReady ? "Ready" : "Not ready"} detail={data.model?.version ? `Version ${data.model.version}` : "Champion model unavailable"} tone={isReady ? "green" : "amber"} />
              <StatCard icon={<Server/>} label="MONITORED SERIES" value={String(serviceCount)} detail="Active in-memory series" tone="blue"/>
              <StatCard icon={<Bell/>} label="RECENT ALERTS" value={String(alertCount)} detail="Latest 50 records" tone={alertCount ? "amber" : "green"}/>
              <StatCard icon={<Zap/>} label="PROMETHEUS" value={prometheus?.enabled ? "Connected" : "Disabled"} detail={prometheus?.enabled ? `${(prometheus.services ?? []).length} configured target(s)` : "Polling is not enabled"} tone={prometheus?.enabled ? "green" : "amber"}/>
            </section>
            <section className="content-grid">
              <div className="panel risk-panel">
                <PanelTitle title="Service risk snapshot" subtitle="Latest model probability per buffered service" action="PREDICTIONS"/>
                {chartData.length ? <div className="chart-wrap"><ResponsiveContainer width="100%" height="100%"><AreaChart data={chartData} margin={{top:12,right:8,left:-22,bottom:0}}><defs><linearGradient id="riskFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#4f8cff" stopOpacity={0.32}/><stop offset="100%" stopColor="#4f8cff" stopOpacity={0.02}/></linearGradient></defs><CartesianGrid stroke="#202a39" vertical={false}/><XAxis dataKey="name" tick={{fill:"#8491a4",fontSize:11}} axisLine={false} tickLine={false}/><YAxis domain={[0,100]} tick={{fill:"#8491a4",fontSize:11}} axisLine={false} tickLine={false} tickFormatter={v=>`${v}%`}/><Tooltip contentStyle={{background:"#111925",border:"1px solid #2a3749",borderRadius:9,color:"#e7edf6"}} formatter={(v)=>[`${v}%`,"Failure probability"]}/><Area type="monotone" dataKey="risk" stroke="#5c96ff" strokeWidth={2.5} fill="url(#riskFill)" connectNulls/></AreaChart></ResponsiveContainer></div> : <EmptyState icon={<Activity/>} title="No scored series yet" text="Once Prometheus polling or metric ingestion supplies enough history, service risk appears here."/>}
                <div className="chart-foot"><span><i className="legend-dot blue"/>Failure probability</span><span>Model horizon: {data.model?.horizon_minutes ?? "—"} min</span></div>
              </div>
              <div className="panel model-panel">
                <PanelTitle title="Model details" subtitle="Active champion metadata"/>
                <div className="model-version"><div className="model-icon"><Cpu size={20}/></div><div><b>{data.model?.model_type ?? "Model unavailable"}</b><span>{data.model?.version ?? "No champion loaded"}</span></div><span className={isReady ? "status-pill success" : "status-pill warning"}>{isReady ? "READY" : "OFFLINE"}</span></div>
                <Detail label="Decision threshold" value={data.model?.threshold !== undefined ? pretty(data.model.threshold) : "—"}/>
                <Detail label="Required history" value={data.model?.required_history_minutes ? `${data.model.required_history_minutes} minutes` : "—"}/>
                <Detail label="Feature count" value={data.model?.n_features ?? "—"}/>
                <Detail label="Training data" value={data.model?.data_source ?? "—"}/>
                <div className="model-note"><ShieldAlert size={15}/><span>Model outputs should be interpreted alongside threshold-based alerts and operational telemetry.</span></div>
              </div>
            </section>
            <section className="content-grid lower-grid">
              <div className="panel">
                <PanelTitle title="Monitored services" subtitle="Series currently held by the API"/>
                <SeriesTable rows={data.series}/>
              </div>
              <div className="panel">
                <PanelTitle title="Operational safeguards" subtitle="Current remediation configuration"/>
                <div className="safeguard"><div className="safeguard-icon"><ShieldCheck size={17}/></div><div><b>Remediation master switch</b><span>{remediation.remediation_enabled ? "Enabled" : "Disabled"}</span></div><span className={remediation.remediation_enabled ? "status-pill warning" : "status-pill success"}>{remediation.remediation_enabled ? "ENABLED" : "OFF"}</span></div>
                <div className="safeguard"><div className="safeguard-icon"><ShieldCheck size={17}/></div><div><b>Dry-run mode</b><span>{remediation.dry_run ? "Actions are simulated" : "Live actions may execute"}</span></div><span className={remediation.dry_run ? "status-pill success" : "status-pill warning"}>{remediation.dry_run ? "ON" : "OFF"}</span></div>
                <div className="safeguard"><div className="safeguard-icon"><Activity size={17}/></div><div><b>Prometheus polling</b><span>{prometheus?.enabled ? "Background polling active" : "Not configured"}</span></div><span className={prometheus?.enabled ? "status-pill success" : "status-pill muted"}>{prometheus?.enabled ? "ACTIVE" : "OFF"}</span></div>
              </div>
            </section>
          </>}

          {page === "Predictions" && <section className="panel full-panel"><PanelTitle title="Prediction results" subtitle="Live series state returned by the model API"/><SeriesTable rows={data.series} expanded/></section>}
          {page === "Alerts & audit" && <section className="content-grid stacked-panels"><div className="panel"><PanelTitle title="Recent alerts" subtitle="Policy-generated service alerts"/><RecordTable rows={data.alerts} empty="No alert records returned."/></div><div className="panel"><PanelTitle title="Remediation audit" subtitle="Executed, dry-run, blocked and skipped decisions"/><RecordTable rows={data.audit} empty="No remediation decisions recorded."/></div></section>}
          {page === "Canary" && <section className="panel full-panel"><PanelTitle title="Canary evaluations" subtitle="Recent baseline-versus-candidate deployment decisions"/><RecordTable rows={data.canary} empty="No canary evaluations yet."/></section>}

          <footer className="footer"><span>PredictiveOps <b>·</b> Reliability intelligence</span><span>Refresh interval 10s <b>·</b> {loading ? "Connecting…" : "API snapshot loaded"}</span></footer>
        </div>
      </main>
    </div>
  );
}

function StatCard({icon,label,value,detail,tone}:{icon:ReactNode;label:string;value:string;detail:string;tone:string}) {
  return <div className="stat-card"><div className={`stat-icon ${tone}`}>{icon}</div><div className="stat-label">{label}</div><div className="stat-value">{value}</div><div className="stat-detail">{detail}</div></div>;
}
function PanelTitle({title,subtitle,action}:{title:string;subtitle:string;action?:string}) {
  return <div className="panel-title"><div><h2>{title}</h2><p>{subtitle}</p></div>{action && <span className="panel-action">{action} <ArrowUpRight size={13}/></span>}</div>;
}
function Detail({label,value}:{label:string;value:unknown}) { return <div className="detail-row"><span>{label}</span><b>{String(value)}</b></div>; }
function EmptyState({icon,title,text}:{icon:React.ReactNode;title:string;text:string}) { return <div className="empty-state"><div>{icon}</div><b>{title}</b><span>{text}</span></div>; }
function SeriesTable({rows,expanded=false}:{rows:AnyRecord[];expanded?:boolean}) {
  if (!rows.length) return <EmptyState icon={<Server/>} title="No monitored series" text="The API has not buffered any metric series yet."/>;
  return <div className="table-scroll"><table><thead><tr><th>SERVICE</th><th>BUFFERED</th><th>POLICY</th><th>RISK / MODEL</th>{expanded && <th>LAST SAMPLE</th>}</tr></thead><tbody>{rows.map((row,i)=>{const risk=riskValue(row);return <tr key={row.series_id ?? i}><td><span className="service-name"><span className="service-dot"/>{row.series_id ?? "Unnamed"}</span></td><td>{row.buffered ?? "—"}</td><td><span className={`status-pill ${row.policy_level === "alert" ? "danger" : row.policy_level === "warn" ? "warning" : "success"}`}>{row.policy_level ?? "none"}</span></td><td>{risk === null ? first(row.prediction ?? {},["label","status"],"Awaiting score") : `${(risk*100).toFixed(1)}%`}</td>{expanded && <td>{time(row.last_timestamp)}</td>}</tr>})}</tbody></table></div>;
}
function RecordTable({rows,empty:emptyText}:{rows:AnyRecord[];empty:string}) {
  if (!rows.length) return <EmptyState icon={<CheckCircle2/>} title="Nothing to show" text={emptyText}/>;
  const columns = Array.from(new Set(rows.slice(0,12).flatMap(row=>Object.keys(row)))).filter(key=>!["id","payload","details","metadata"].includes(key)).slice(0,5);
  return <div className="table-scroll"><table><thead><tr>{columns.map(c=><th key={c}>{c.replaceAll("_"," ").toUpperCase()}</th>)}</tr></thead><tbody>{rows.slice(0,30).map((row,i)=><tr key={row.id ?? i}>{columns.map(c=><td key={c}>{typeof row[c] === "object" ? JSON.stringify(row[c]) : String(row[c] ?? "—")}</td>)}</tr>)}</tbody></table></div>;
}
export default App;
