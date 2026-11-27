import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Bell, CheckCircle2,
  ChevronRight, CircleHelp, Clock3, Cpu, ExternalLink, Gauge, LayoutDashboard,
  RefreshCw, Server, ShieldCheck, ShieldAlert, Zap, FileText, ScrollText, Terminal, Play, RotateCcw, Square, Skull,
} from "lucide-react";
import {
  Area, AreaChart, Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

type AnyRecord = Record<string, any>;
type Snapshot = {
  health: AnyRecord | null; ready: AnyRecord | null; model: AnyRecord | null;
  liveForecast: AnyRecord | null;
  experimentalPrediction: AnyRecord | null;
  sources: AnyRecord | null; series: AnyRecord[]; containers: AnyRecord[]; containerStatus: AnyRecord | null; alerts: AnyRecord[];
  audit: AnyRecord[]; policy: AnyRecord | null;
  errors: string[];
};
const empty: Snapshot = { health:null, ready:null, model:null, liveForecast:null, experimentalPrediction:null, sources:null, series:[], containers:[], containerStatus:null, alerts:[], audit:[], policy:null, errors:[] };
const get = async (path: string) => {
  const response = await fetch(path, { headers: { Accept: "application/json" } });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    if (path === "/ready") return body;
    throw new Error(`${path}: HTTP ${response.status}${body?.detail ? ": " + body.detail : ""}`);
  }
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
  const [stressMaxUsers, setStressMaxUsers] = useState(5);
  const [stressDuration, setStressDuration] = useState(30);
  const [rangeMinutes, setRangeMinutes] = useState(60);
  const [history, setHistory] = useState<AnyRecord | null>(null);
  const [containerForecasts, setContainerForecasts] = useState<AnyRecord[]>([]);
  const [selectedForecastContainer, setSelectedForecastContainer] = useState("");
  const [infraAlerts, setInfraAlerts] = useState<AnyRecord | null>(null);
  const [coverage, setCoverage] = useState<AnyRecord | null>(null);
  const [managedContainers, setManagedContainers] = useState<AnyRecord[]>([]);
  const [incidents, setIncidents] = useState<AnyRecord[]>([]);
  const [selectedLogContainer, setSelectedLogContainer] = useState("");
  const [liveLogs, setLiveLogs] = useState("");
  const [opsError, setOpsError] = useState<string | null>(null);
  const [stressRun, setStressRun] = useState<AnyRecord | null>(null);
  const [stressBusy, setStressBusy] = useState(false);
  const [loadTargets, setLoadTargets] = useState<AnyRecord[]>([]);
  const [loadTargetId, setLoadTargetId] = useState("");
  const [loadPort, setLoadPort] = useState(8000);
  const [loadPath, setLoadPath] = useState("/health");
  const [loadConcurrency, setLoadConcurrency] = useState(5);
  const [loadRate, setLoadRate] = useState(10);
  const [loadDuration, setLoadDuration] = useState(30);
  const [loadWarningLatency, setLoadWarningLatency] = useState(500);
  const [loadCriticalLatency, setLoadCriticalLatency] = useState(1500);
  const [loadWarningErrors, setLoadWarningErrors] = useState(2);
  const [loadCriticalErrors, setLoadCriticalErrors] = useState(10);
  const [loadJob, setLoadJob] = useState<AnyRecord | null>(null);
  const [loadBusy, setLoadBusy] = useState(false);
  const [logsPaused, setLogsPaused] = useState(false);
  const [logSearch, setLogSearch] = useState("");

  const refreshLive = useCallback(async () => {
    const [nextHistory, nextAlerts, nextCoverage, nextContainerForecasts] = await Promise.all([
      get(`/v1/host/history?minutes=${rangeMinutes}`).catch(() => null),
      get("/v1/infra/alerts").catch(() => null),
      get("/v1/telemetry/coverage").catch(() => null),
      get("/v1/containers/forecast").catch(() => null),
    ]);
    setHistory(nextHistory);
    setInfraAlerts(nextAlerts);
    setCoverage(nextCoverage);
    const forecastRows: AnyRecord[] = nextContainerForecasts?.containers ?? [];
    setContainerForecasts(forecastRows);
    setSelectedForecastContainer(current =>
      current && forecastRows.some(row => row.name === current) ? current : (forecastRows[0]?.name ?? "")
    );
  }, [rangeMinutes]);

  useEffect(() => {
    void refreshLive();
    const timer = window.setInterval(() => void refreshLive(), 10000);
    return () => window.clearInterval(timer);
  }, [refreshLive]);

  const refreshOperations = useCallback(async () => {
    try {
      const [containerResult, incidentResult, stressResult] = await Promise.all([
        get("/v1/ops/containers"),
        get("/v1/ops/incidents?limit=100"),
        get("/v1/ops/stress?limit=1"),
      ]);
      setManagedContainers(containerResult.containers ?? []);
      setIncidents(incidentResult.incidents ?? []);
      const latestRun = stressResult.runs?.[0];
      if (latestRun) setStressRun({ ...latestRun, run_id: latestRun.id });
      setOpsError(containerResult.error ?? null);
    } catch (error) {
      setOpsError(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    void refreshOperations();
    const timer = window.setInterval(() => void refreshOperations(), 10000);
    return () => window.clearInterval(timer);
  }, [refreshOperations]);

  useEffect(() => {
    if (!stressRun?.run_id || stressRun.status !== "running") return;
    const timer = window.setInterval(async () => {
      try { setStressRun({ ...(await get(`/v1/ops/stress/${stressRun.run_id}`)), run_id: stressRun.run_id }); }
      catch { /* keep the last visible run state */ }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [stressRun?.run_id, stressRun?.status]);

  const loadContainerLogs = useCallback(async (id: string) => {
    setSelectedLogContainer(id);
    setPage("Live logs");
    try {
      const result = await get(`/v1/ops/containers/${id}/logs?tail=500`);
      setLiveLogs(result.logs ?? "");
    } catch (error) {
      setLiveLogs(error instanceof Error ? error.message : String(error));
    }
  }, []);

  useEffect(() => {
    if (page !== "Live logs" || selectedLogContainer || !managedContainers.length) return;
    void loadContainerLogs(managedContainers[0].id);
  }, [page, selectedLogContainer, managedContainers, loadContainerLogs]);

  const runContainerAction = useCallback(async (id: string, action: string) => {
    if (!window.confirm(`Are you sure you want to ${action} this container?`)) return;
    try {
      const response = await fetch(`/v1/ops/containers/${id}/${action}`, { method: "POST" });
      const body = await response.json().catch(() => null);
      if (!response.ok) throw new Error(body?.detail ?? `Action failed: HTTP ${response.status}`);
      await refreshOperations();
    } catch (error) {
      window.alert(error instanceof Error ? error.message : String(error));
    }
  }, [refreshOperations]);

  const startStressTest = useCallback(async () => {
    setStressBusy(true);
    try {
      const response = await fetch("/v1/ops/stress", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_users: stressMaxUsers, duration_seconds: stressDuration })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.detail ?? "Could not start stress test");
      setStressRun(body);
    } catch (error) {
      window.alert(error instanceof Error ? error.message : String(error));
    } finally { setStressBusy(false); }
  }, [stressMaxUsers, stressDuration]);


  useEffect(() => {
    if (page !== "Live logs" || !selectedLogContainer || logsPaused) return;
    const source = new EventSource(`/v1/ops/containers/${selectedLogContainer}/logs/stream?tail=100`);
    source.onmessage = event => {
      try {
        const item = JSON.parse(event.data);
        if (item.line) setLiveLogs(previous => [...previous.split("\n"), item.line].slice(-3000).join("\n"));
        if (item.error) setLiveLogs(previous => previous + "\n[stream error] " + item.error);
      } catch { /* ignore malformed stream frames */ }
    };
    return () => source.close();
  }, [page, selectedLogContainer, logsPaused]);

  useEffect(() => {
    if (page !== "Load testing") return;
    let cancelled = false;
    void get("/v1/ops/load-tests/targets").then(result => {
      if (cancelled) return;
      const targets: AnyRecord[] = result.targets ?? [];
      setLoadTargets(targets);
      if (!loadTargetId || !targets.some(target => target.id === loadTargetId)) {
        const preferred = targets.find(target => target.service === "api") ?? targets[0];
        setLoadTargetId(preferred?.id ?? "");
        setLoadPort(preferred?.service === "api" ? 8000 : preferred?.ports?.[0] ?? 8000);
      }
    }).catch(error => { if (!cancelled) setOpsError(error instanceof Error ? error.message : String(error)); });
    return () => { cancelled = true; };
  }, [page]);

  useEffect(() => {
    if (!loadJob?.job_id || !["queued", "running"].includes(loadJob.status)) return;
    const timer = window.setInterval(async () => {
      try { setLoadJob(await get(`/v1/ops/load-tests/${loadJob.job_id}`)); }
      catch { /* retain last known run state */ }
    }, 1500);
    return () => window.clearInterval(timer);
  }, [loadJob?.job_id, loadJob?.status]);

  const startLoadTest = useCallback(async () => {
    if (!loadTargetId) return;
    setLoadBusy(true);
    try {
      const response = await fetch("/v1/ops/load-tests", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          container_id: loadTargetId, port: loadPort, path: loadPath,
          concurrency: loadConcurrency, request_rate: loadRate, duration_seconds: loadDuration,
          warning_latency_ms: loadWarningLatency, critical_latency_ms: loadCriticalLatency,
          warning_error_rate: loadWarningErrors / 100, critical_error_rate: loadCriticalErrors / 100
        })
      });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.detail ?? "Could not start application load test");
      setLoadJob(body);
    } catch (error) { window.alert(error instanceof Error ? error.message : String(error)); }
    finally { setLoadBusy(false); }
  }, [loadTargetId, loadPort, loadPath, loadConcurrency, loadRate, loadDuration, loadWarningLatency, loadCriticalLatency, loadWarningErrors, loadCriticalErrors]);

  const cancelLoadTest = useCallback(async () => {
    if (!loadJob?.job_id) return;
    try {
      const response = await fetch(`/v1/ops/load-tests/${loadJob.job_id}/cancel`, { method: "POST" });
      const body = await response.json();
      if (!response.ok) throw new Error(body?.detail ?? "Could not cancel load test");
      setLoadJob(previous => previous ? { ...previous, status: "cancelling" } : previous);
    } catch (error) { window.alert(error instanceof Error ? error.message : String(error)); }
  }, [loadJob?.job_id]);

  const historyData = useMemo(
    () => (history?.points ?? []).map((point: AnyRecord) => ({ ...point, time: point.t * 1000 })),
    [history],
  );
  const hostChartData = useMemo(() => {
    const byTime = new Map<number, AnyRecord>();
    historyData.forEach(point => byTime.set(point.time, { ...point }));
    (history?.forecast_points ?? []).forEach((point: AnyRecord) => {
      const timestamp = Number(point.t) * 1000;
      const merged = byTime.get(timestamp) ?? { time: timestamp };
      Object.entries(point).forEach(([key, value]) => { if (key !== "t") merged[key] = value; });
      byTime.set(timestamp, merged);
    });
    return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  }, [historyData, history?.forecast_points]);
  const selectedContainerForecast = containerForecasts.find(row => row.name === selectedForecastContainer)
    ?? containerForecasts[0] ?? null;
  const containerForecastChartData = useMemo(() => {
    if (!selectedContainerForecast) return [];
    const byTime = new Map<number, AnyRecord>();
    (selectedContainerForecast.points ?? []).forEach((point: AnyRecord) => {
      byTime.set(Number(point.t) * 1000, { ...point, time: Number(point.t) * 1000 });
    });
    (selectedContainerForecast.forecast_points ?? []).forEach((point: AnyRecord) => {
      const timestamp = Number(point.t) * 1000;
      const merged = byTime.get(timestamp) ?? { time: timestamp };
      Object.entries(point).forEach(([key, value]) => { if (key !== "t") merged[key] = value; });
      byTime.set(timestamp, merged);
    });
    return Array.from(byTime.values()).sort((a, b) => a.time - b.time);
  }, [selectedContainerForecast]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    const endpoints = [
      ["health", "/health"], ["ready", "/ready"], ["model", "/v1/model"],
      ["liveForecast", "/v1/live-forecast"], ["experimentalPrediction", "/v1/experimental/host/prediction"], ["sources", "/v1/sources"], ["series", "/v1/series"], ["containerStatus", "/v1/containers"], ["alerts", "/v1/alerts?limit=50"],
      ["audit", "/v1/audit?limit=50"], ["policy", "/v1/policy"],
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
  const scopedModels: AnyRecord | null = data.model?.models ?? null;
  const modelCount = scopedModels ? Object.keys(scopedModels).length : 0;
  const primaryModel: AnyRecord | null = scopedModels?.host ?? scopedModels?.combined ?? data.model;
  const modelVersionLabel = modelCount
    ? Object.entries(scopedModels!).map(([scope, model]: [string, any]) =>
        scope + ": " + (model.version ?? model.status ?? "not installed")).join(" · ")
    : "Live telemetry forecasting active";
  const sourceScopes: [string, AnyRecord][] = Object.entries(prometheus?.scopes ?? {});
  const sourceTargets = sourceScopes.flatMap(([, scope]) => Object.values(scope.services ?? {}) as AnyRecord[]);
  const configuredTargets = sourceTargets.length
    ? sourceTargets
    : Object.values(prometheus?.services ?? {}) as AnyRecord[];
  const healthyTargets = configuredTargets.filter(target => {
    if (target.error || !target.last_ok) return false;
    const ageMs = Date.now() - new Date(target.last_ok).getTime();
    return Number.isFinite(ageMs) && ageMs <= 180000;
  }).length;
  const sourceCount = configuredTargets.length;
  const applicationScopeConfigured = Object.keys(
    sourceScopes.find(([name]) => name === "application")?.[1]?.services ?? {}
  ).length > 0;
  const sourceHealth = prometheus?.enabled !== true ? "Disabled"
    : sourceCount === 0 ? "Unconfigured"
    : healthyTargets === sourceCount ? "Healthy" : "Degraded";
  const sourceDetail = prometheus?.enabled !== true ? "Polling is not enabled"
    : sourceCount === 0 ? "No targets configured"
    : `${healthyTargets}/${sourceCount} configured targets healthy${
        applicationScopeConfigured ? "" : " · Application monitoring not configured"
      }`;
  const hostModel = scopedModels?.host ?? scopedModels?.combined;
  const applicationModel = scopedModels?.application;
  const serviceCount = data.series.length;
  const containerRows: AnyRecord[] = data.containerStatus?.containers ?? [];
  const containerCount = containerRows.length;
  const containerCpu = containerRows.length && containerRows.every(row => typeof row.cpu_cores === "number")
    ? containerRows.reduce((sum, row) => sum + row.cpu_cores, 0) : null;
  const containerCpuPct = containerRows.length && containerRows.every(row => typeof row.cpu_pct === "number")
    ? containerRows.reduce((sum, row) => sum + row.cpu_pct, 0) : null;
  const containerMemory = containerRows.length && containerRows.every(row => typeof row.memory_bytes === "number")
    ? containerRows.reduce((sum, row) => sum + row.memory_bytes, 0) : null;
  const alertCount = data.alerts.length;
  const remediation = data.policy?.config ?? {};
  const containerChartData = useMemo(() => containerRows
    .filter(row => typeof row.memory_bytes === "number" && typeof row.cpu_cores === "number" && typeof row.cpu_pct === "number")
    .sort((a, b) => b.cpu_pct - a.cpu_pct)
    .slice(0, 10)
    .map(row => ({
      name: row.service && row.service !== "—" ? row.service : row.name,
      memoryMiB: Number((row.memory_bytes / (1024 * 1024)).toFixed(1)),
      cpuCores: Number(row.cpu_cores.toFixed(3)),
      cpuPct: Number(row.cpu_pct.toFixed(2)),
    })), [containerRows]);

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
          <button className={page === "Containers" ? "nav-item active" : "nav-item"} onClick={() => activePage("Containers")}><Server size={17}/>Containers<span className="nav-count">{containerCount}</span></button>
          <button className={page === "Crash reports" ? "nav-item active" : "nav-item"} onClick={() => activePage("Crash reports")}><FileText size={17}/>Crash reports<span className="nav-count">{incidents.length}</span></button>
          <button className={page === "Live logs" ? "nav-item active" : "nav-item"} onClick={() => activePage("Live logs")}><ScrollText size={17}/>Live logs</button>
          <button className={page === "Load testing" ? "nav-item active" : "nav-item"} onClick={() => activePage("Load testing")}><Gauge size={17}/>Application load test</button>
          <button className={page === "Alerts & audit" ? "nav-item active" : "nav-item"} onClick={() => activePage("Alerts & audit")}><Bell size={17}/>Alerts & audit<span className="nav-count">{alertCount}</span></button>
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
            <button className="icon-button" onClick={() => { void refresh(); void refreshLive(); }} title="Refresh data" disabled={refreshing}><RefreshCw size={16} className={refreshing ? "spin" : ""}/></button>
            <div className="avatar">PO</div>
          </div>
        </header>

        <div className="page-content">
          <div className="page-heading">
            <div><div className="eyebrow">SYSTEM OVERVIEW <span className="eyebrow-line"/></div><h1>{page}</h1><p>{page === "Overview" ? "Predictive service health, model readiness and operational signals." : page === "Predictions" ? "Current model assessments for monitored service series." : page === "Alerts & audit" ? "Alert history and gated remediation decisions." : page === "Containers" ? "Live resources, container controls and bounded stress testing." : page === "Crash reports" ? "Historical container failure incidents with exit details and captured logs." : page === "Live logs" ? "Live output from a selected container." : "Operational monitoring."}</p></div>
            <div className="updated"><Clock3 size={14}/><span>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : "Waiting for data"}</span></div>
          </div>

          {data.errors.length > 0 && <div className="notice warning"><AlertTriangle size={16}/><div><b>Some API endpoints could not be loaded</b><span>{data.errors.join(" · ")}</span></div></div>}

          {page === "Overview" && <>
            <section className="stat-grid">
              <StatCard icon={<ShieldCheck/>} label="MODEL STATUS" value={isReady ? "Ready" : "Monitoring"} detail={isReady ? "Qualified models loaded" : "Live telemetry and container operations active"} tone={isReady ? "green" : "blue"} />
              <StatCard icon={<Server/>} label="CONTAINERS" value={String(containerCount)} detail="Live cAdvisor resource inventory" tone="blue"/>
              <StatCard icon={<Bell/>} label="RECENT ALERTS" value={String(alertCount)} detail="Latest 50 records" tone={alertCount ? "amber" : "green"}/>
              <StatCard icon={<Zap/>} label="PROMETHEUS" value={sourceHealth} detail={sourceDetail} tone={sourceHealth === "Healthy" ? "green" : "amber"}/>
            </section>
            <section className="panel full-panel">
              <PanelTitle
                title="Live host telemetry"
                subtitle={`Real Prometheus data${history?.series_id ? " from " + history.series_id : ""} · the same queries the host model reads · refreshed every 10 seconds`}
              />
              <div className="range-tabs" role="group" aria-label="Time range">
                {RANGES.map(([minutes, label]) => (
                  <button key={minutes} type="button" className={rangeMinutes === minutes ? "range-tab active" : "range-tab"} onClick={() => setRangeMinutes(minutes)}>{label}</button>
                ))}
              </div>
              <div className="forecast-legend"><span><i className="forecast-solid"/>Measured telemetry</span><span><i className="forecast-dotted"/>20-minute projection</span><span className="forecast-now">NOW marks the forecast boundary</span><span className="forecast-samples">History: {history?.forecast_samples_available ?? 0}/{history?.forecast_required_samples ?? 30} samples · {history?.forecast_status ?? "warming up"}</span></div>
              {history?.available ? (
                <div className="metric-grid">
                  <MetricChart title="CPU utilisation" unit="%" data={hostChartData} latest={history.latest} domain={[0, 100]} forecastStart={Number(history?.forecast_anchor_timestamp) * 1000} series={[{ key: "cpu_pct", name: "CPU", color: "#5c96ff", forecastKey: "cpu_pct_forecast" }]}/>
                  <MetricChart title="Memory used" unit="%" data={hostChartData} latest={history.latest} domain={[0, 100]} forecastStart={Number(history?.forecast_anchor_timestamp) * 1000} series={[{ key: "memory_pct", name: "Memory", color: "#45c49a", forecastKey: "memory_pct_forecast" }]}/>
                  <MetricChart title="Disk I/O utilisation" unit="%" data={hostChartData} latest={history.latest} domain={[0, 100]} forecastStart={Number(history?.forecast_anchor_timestamp) * 1000} series={[{ key: "disk_io_util_pct", name: "Disk I/O", color: "#e8b45b", forecastKey: "disk_io_util_pct_forecast" }]}/>
                  <MetricChart title="Network (physical NICs)" unit=" Mbps" data={hostChartData} latest={history.latest} forecastStart={Number(history?.forecast_anchor_timestamp) * 1000} series={[{ key: "network_rx_mbps", name: "Receive", color: "#5c96ff", forecastKey: "network_rx_mbps_forecast" }, { key: "network_tx_mbps", name: "Transmit", color: "#f07878", forecastKey: "network_tx_mbps_forecast" }]}/>
                  <MetricChart title="Load average (1m)" unit="" data={hostChartData} latest={history.latest} forecastStart={Number(history?.forecast_anchor_timestamp) * 1000} series={[{ key: "load1", name: "Load", color: "#b794f6", forecastKey: "load1_forecast" }]}/>
                  <MetricChart title="Root filesystem used" unit="%" data={hostChartData} latest={history.latest} domain={[0, 100]} forecastStart={Number(history?.forecast_anchor_timestamp) * 1000} series={[{ key: "root_disk_used_pct", name: "Root disk", color: "#f0a36b", forecastKey: "root_disk_used_pct_forecast" }]}/>
                </div>
              ) : (
                <EmptyState icon={<Activity/>} title="No host telemetry yet" text={history?.error ? `Prometheus query failed: ${history.error}` : "Waiting for Node Exporter samples from Prometheus."}/>
              )}
            </section>
            <section className="panel full-panel">
              <PanelTitle title="Infrastructure alerts" subtitle="Pending and firing Prometheus alerts, including the static-threshold safety net"/>
              <InfraAlerts data={infraAlerts}/>
            </section>
            <LiveForecastPanel forecast={data.liveForecast}/>
            <section className="content-grid">
              <div className="panel risk-panel">
                <PanelTitle
                  title="Live container resource usage"
                  subtitle="Top 10 containers by CPU share of total host capacity · refreshed every 10 seconds"
                />
                {containerChartData.length ? (
                  <div className="chart-wrap">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={containerChartData} margin={{top:12,right:12,left:0,bottom:8}}>
                        <CartesianGrid stroke="#202a39" vertical={false}/>
                        <XAxis dataKey="name" tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false}/>
                        <YAxis yAxisId="memory" tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false} tickFormatter={v => `${v} MB`}/>
                        <YAxis yAxisId="cpu" orientation="right" domain={[0, "auto"]} tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false} tickFormatter={v => `${v}%`}/>
                        <Tooltip
                          contentStyle={{background:"#111925",border:"1px solid #2a3749",borderRadius:9,color:"#e7edf6"}}
                          formatter={(value, name) => [
                            name === "Memory" ? `${value} MiB` : name === "CPU" ? `${value}% host capacity` : `${value} CPU cores`,
                            name
                          ]}
                        />
                        <Legend wrapperStyle={{fontSize:11,color:"#aab6c8"}}/>
                        <Bar yAxisId="memory" dataKey="memoryMiB" name="Memory" fill="#4f8cff" fillOpacity={0.75} radius={[4,4,0,0]}/>
                        <Line yAxisId="cpu" type="monotone" dataKey="cpuPct" name="CPU" stroke="#36c9a0" strokeWidth={2} dot={{r:3}}/>
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState icon={<Activity/>} title="Waiting for container metrics" text="Resource usage will appear when cAdvisor reports container CPU and memory readings."/>
                )}
                <div className="chart-foot">
                  <span><i className="legend-dot blue"/>Memory working set (MiB)</span>
                  <span>CPU share of host capacity (%)</span>
                </div>
              </div>
              <div className="panel model-panel">
                <PanelTitle title="Model & forecast engine" subtitle="Runtime models, artifact diagnostics and always-on capacity forecasting"/>
                <div className="model-version"><div className="model-icon"><Cpu size={20}/></div><div><b>{primaryModel?.model_type ?? "Live host capacity forecast"}</b><span>{modelVersionLabel}</span></div><span className={isReady ? "status-pill success" : "status-pill muted"}>{isReady ? "ML READY" : "FORECASTING"}</span></div>
                <Detail label="Host model" value={hostModel?.version ?? hostModel?.status ?? "Not configured"}/>
                <Detail label="Application model" value={applicationModel?.version ?? applicationModel?.status ?? "Not configured"}/>
                <Detail label="Experimental host model" value={data.model?.experimental_host?.version ?? data.model?.experimental_host?.status ?? "Not configured"}/>
                <Detail label="Forecast engine" value={data.model?.forecast_engine?.status === "available" ? "Available · 30 min horizon" : "Live telemetry engine"}/>
                <Detail label="Decision threshold" value={primaryModel?.threshold !== undefined ? pretty(primaryModel.threshold) : "Not applicable"}/>
                <Detail label="Required history" value={primaryModel?.required_history_minutes ? `${primaryModel.required_history_minutes} minutes` : "30 minutes for capacity forecast"}/>
                <Detail label="Feature count" value={primaryModel?.n_features ?? "Awaiting trained model"}/>
                <Detail label="Training data" value={primaryModel?.data_source ?? "No ML artifact loaded"}/>
                <div className="model-note"><ShieldAlert size={15}/><span>{isReady ? "Qualified ML inference is active. Capacity forecasting remains a separate guardrail-based signal." : (primaryModel?.reason ?? "Live capacity forecasting is active. ML inference stays disabled until a compatible, qualified artifact is installed.") + " Capacity forecasts are trend projections, not calibrated failure probabilities."}</span></div>
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

          {page === "Predictions" && <>
            <LiveForecastPanel forecast={data.liveForecast}/>
            <section className="panel full-panel stress-panel">
              <PanelTitle title="Host capacity stress test" subtitle="Apply bounded memory pressure with isolated temporary workers and observe the host response"/>
              <div className="stress-config">
                <label>MAX WORKERS
                  <input type="number" min={1} max={10} step={1} value={stressMaxUsers}
                    onChange={event => setStressMaxUsers(Math.max(1, Math.min(10, Number(event.target.value) || 1)))}
                    disabled={stressBusy || stressRun?.status === "running"}/>
                </label>
                <label>OBSERVATION WINDOW (SECONDS)
                  <input type="number" min={5} max={60} step={5} value={stressDuration}
                    onChange={event => setStressDuration(Math.max(5, Math.min(60, Number(event.target.value) || 5)))}
                    disabled={stressBusy || stressRun?.status === "running"}/>
                </label>
                <button className="primary-action" onClick={() => void startStressTest()} disabled={stressBusy || stressRun?.status === "running"}>
                  <Play size={14}/>{stressBusy ? "Starting…" : stressRun?.status === "running" ? "Test running…" : "Start stress test"}
                </button>
              </div>
              <p className="stress-help">Safety limits: 1–10 isolated workers, 5–60 seconds, 32 MB RAM and 0.1 CPU per worker. This is a host memory-pressure probe, not an HTTP request/load test. Workers have no network access, are hidden from the normal container inventory and are removed at completion. Capacity failures are written to Alerts & audit.</p>
              {stressRun && <div className="stress-result">
                <span className={stressRun.status === "running" ? "status-pill warning" : stressRun.status === "error" ? "status-pill danger" : "status-pill success"}>{String(stressRun.status).replace(/_/g," ")}</span>
                <b>{stressRun.users_started ?? 0} / {stressRun.max_users ?? stressMaxUsers} workers started</b>
                <span>{stressRun.failure ?? (stressRun.status === "running" ? "Measuring capacity…" : "Run completed")}</span>
              </div>}
            </section>
            <section className="panel full-panel">
              <PanelTitle title="Container controls" subtitle="Manage containers and inspect logs from the Containers tab"/>
              <ManagedContainerTable rows={managedContainers} onAction={runContainerAction} onLogs={loadContainerLogs}/>
            </section>
          </>}
          {page === "Containers" && <>
            <section className="stat-grid container-summary">
              <StatCard icon={<Server/>} label="RUNNING CONTAINERS" value={String(containerCount)} detail={data.containerStatus?.available ? "Reporting through cAdvisor" : "Waiting for cAdvisor metrics"} tone="blue"/>
              <StatCard icon={<Cpu/>} label="TOTAL CONTAINER CPU" value={containerCpuPct === null ? "—" : containerCpuPct.toFixed(1) + "%"} detail={containerCpu === null ? "Share of total host CPU capacity" : `${containerCpu.toFixed(2)} core-equivalents · host-normalized`} tone="green"/>
              <StatCard icon={<Activity/>} label="MEMORY WORKING SET" value={formatBytes(containerMemory)} detail="Sum of container working sets" tone="amber"/>
              <StatCard icon={<Zap/>} label="TELEMETRY" value={data.containerStatus?.available ? "Live" : "Unavailable"} detail="Prometheus · cAdvisor · 15s scrape" tone={data.containerStatus?.available ? "green" : "amber"}/>
            </section>
            <section className="panel full-panel">
              <PanelTitle title="Container resource inventory" subtitle="One row per container · refreshed every 10 seconds"/>
              {data.containerStatus?.error && <div className="notice warning"><AlertTriangle size={16}/><div><b>Container telemetry unavailable</b><span>{data.containerStatus.error}</span></div></div>}
              <ContainerTable rows={containerRows}/>
            </section>
            <section className="panel full-panel">
              <PanelTitle title="Per-container resource forecast" subtitle="Independent 20-minute CPU and memory projections from each container's cAdvisor history"/>
              {containerForecasts.length ? <>
                <div className="forecast-toolbar">
                  <label>CONTAINER
                    <select value={selectedContainerForecast?.name ?? ""} onChange={event => setSelectedForecastContainer(event.target.value)}>
                      {containerForecasts.map(row => <option key={row.name} value={row.name}>{row.service} · {row.name}</option>)}
                    </select>
                  </label>
                  <span className={selectedContainerForecast?.status === "ready" ? "status-pill success" : "status-pill muted"}>{selectedContainerForecast?.status ?? "warming up"}</span>
                  <span className="forecast-samples">{selectedContainerForecast?.samples_available ?? 0}/{selectedContainerForecast?.required_samples ?? 30} one-minute samples</span>
                </div>
                <div className="forecast-kpis">
                  <div><span>CPU NOW</span><b>{selectedContainerForecast?.metrics?.cpu_pct?.current == null ? "—" : Number(selectedContainerForecast.metrics.cpu_pct.current).toFixed(2) + "%"}</b><small>+20m {selectedContainerForecast?.metrics?.cpu_pct?.forecast == null ? "—" : Number(selectedContainerForecast.metrics.cpu_pct.forecast).toFixed(2) + "%"}</small></div>
                  <div><span>MEMORY NOW</span><b>{selectedContainerForecast?.metrics?.memory_gib?.current == null ? "—" : Number(selectedContainerForecast.metrics.memory_gib.current).toFixed(2) + " GiB"}</b><small>+20m {selectedContainerForecast?.metrics?.memory_gib?.forecast == null ? "—" : Number(selectedContainerForecast.metrics.memory_gib.forecast).toFixed(2) + " GiB"}</small></div>
                </div>
                {containerForecastChartData.length ? <div className="metric-grid">
                  <MetricChart title="Container CPU · host capacity" unit="%" data={containerForecastChartData}
                    latest={selectedContainerForecast?.points?.slice(-1)[0]} domain={[0, 100]}
                    forecastStart={Number(selectedContainerForecast?.points?.slice(-1)[0]?.t) * 1000}
                    series={[{key:"cpu_pct",name:"CPU",color:"#5c96ff",forecastKey:"cpu_pct_forecast"}]}/>
                  <MetricChart title="Container memory working set" unit=" GiB" data={containerForecastChartData}
                    latest={selectedContainerForecast?.points?.slice(-1)[0]}
                    forecastStart={Number(selectedContainerForecast?.points?.slice(-1)[0]?.t) * 1000}
                    series={[{key:"memory_gib",name:"Memory",color:"#45c49a",forecastKey:"memory_gib_forecast"}]}/>
                </div> : <EmptyState icon={<Activity/>} title="Building container forecast history" text={selectedContainerForecast?.message ?? "Waiting for 30 contiguous one-minute cAdvisor samples."}/>}
                <div className="model-note"><ShieldCheck size={15}/><span>Forecasts are calculated separately per container. Dotted values are projections, not actual usage; no container action is triggered.</span></div>
              </> : <EmptyState icon={<Activity/>} title="Container forecast telemetry unavailable" text="Waiting for cAdvisor history in Prometheus."/>}
            </section>
            <section className="panel full-panel">
              <PanelTitle title="Container controls" subtitle="Control allowlisted services across Docker Compose projects and inspect their logs"/>
              {opsError && <div className="notice warning"><AlertTriangle size={15}/><div><b>Docker control unavailable</b><span>{opsError}</span></div></div>}
              <ManagedContainerTable rows={managedContainers} onAction={runContainerAction} onLogs={loadContainerLogs}/>
            </section>
            <section className="container-grafana-link"><span>Historical container trends, filesystem I/O and network charts</span><a href={"http://" + window.location.hostname + ":3000/d/predictiveops/predictiveops"} target="_blank" rel="noreferrer">Open infrastructure dashboard <ExternalLink size={13}/></a></section>
          </>}
          {page === "Crash reports" && <section className="panel full-panel">
            <PanelTitle title="Historical crash reports" subtitle="Captured from Docker die and OOM events · newest first"/>
            <IncidentTable rows={incidents}/>
          </section>}
          {page === "Live logs" && <section className="panel full-panel">
            <PanelTitle title="Live container logs" subtitle="Docker log stream · server-sent events"/>
            <div className="log-toolbar">
              <select value={selectedLogContainer} onChange={event => void loadContainerLogs(event.target.value)}>
                {managedContainers.map(container => <option key={container.id} value={container.id}>{container.name} · {container.status}</option>)}
              </select>
              <input aria-label="Search logs" placeholder="Filter log lines…" value={logSearch} onChange={event => setLogSearch(event.target.value)}/>
              <button className="secondary-action" onClick={() => setLogsPaused(value => !value)}>{logsPaused ? "Resume stream" : "Pause stream"}</button>
              <button className="secondary-action" onClick={() => selectedLogContainer && void loadContainerLogs(selectedLogContainer)}><RefreshCw size={13}/>Reload tail</button>
            </div>
            <pre className="live-log-output">{(logSearch ? liveLogs.split("\n").filter(line => line.toLowerCase().includes(logSearch.toLowerCase())).join("\n") : liveLogs) || "No log output available for this container."}</pre>
          </section>}
          {page === "Load testing" && <section className="panel full-panel stress-panel">
            <PanelTitle title="Application HTTP load test" subtitle="Generate bounded traffic against a running managed Compose service"/>
            <div className="stress-config">
              <label>TARGET CONTAINER<select value={loadTargetId} onChange={event => { const target = loadTargets.find(item => item.id === event.target.value); setLoadTargetId(event.target.value); setLoadPort(target?.service === "api" ? 8000 : target?.ports?.[0] ?? 8000); }}>
                {loadTargets.map(target => <option key={target.id} value={target.id}>{target.service} · {target.name}</option>)}</select></label>
              <label>HTTP PORT<select value={loadPort} onChange={event => setLoadPort(Number(event.target.value))}>{(loadTargets.find(target => target.id === loadTargetId)?.ports ?? []).map((port: number) => <option key={port} value={port}>{port}</option>)}</select></label>
              <label>PATH<input value={loadPath} onChange={event => setLoadPath(event.target.value)} maxLength={512}/></label>
              <label>CONCURRENCY<input type="number" min={1} max={100} value={loadConcurrency} onChange={event => setLoadConcurrency(Math.max(1, Math.min(100, Number(event.target.value) || 1)))}/></label>
              <label>REQUESTS / SECOND<input type="number" min={1} max={100} value={loadRate} onChange={event => setLoadRate(Math.max(1, Math.min(100, Number(event.target.value) || 1)))}/></label>
              <label>DURATION (SECONDS)<input type="number" min={5} max={300} value={loadDuration} onChange={event => setLoadDuration(Math.max(5, Math.min(300, Number(event.target.value) || 5)))}/></label>
              <label>WARNING P95 (MS)<input type="number" min={1} value={loadWarningLatency} onChange={event => setLoadWarningLatency(Math.max(1, Number(event.target.value) || 1))}/></label>
              <label>CRITICAL P95 (MS)<input type="number" min={1} value={loadCriticalLatency} onChange={event => setLoadCriticalLatency(Math.max(1, Number(event.target.value) || 1))}/></label>
              <label>WARNING ERROR RATE (%)<input type="number" min={0} max={100} value={loadWarningErrors} onChange={event => setLoadWarningErrors(Math.max(0, Math.min(100, Number(event.target.value) || 0)))}/></label>
              <label>CRITICAL ERROR RATE (%)<input type="number" min={0} max={100} value={loadCriticalErrors} onChange={event => setLoadCriticalErrors(Math.max(0, Math.min(100, Number(event.target.value) || 0)))}/></label>
              <button className="primary-action" onClick={() => void startLoadTest()} disabled={loadBusy || !loadTargetId || ["queued","running"].includes(loadJob?.status)}><Play size={14}/>{loadBusy ? "Starting…" : "Start HTTP load test"}</button>
              {["queued","running"].includes(loadJob?.status) && <button className="secondary-action" onClick={() => void cancelLoadTest()}>Cancel test</button>}
            </div>
            <p className="stress-help">Only running containers in the PredictiveOps Compose project are eligible. Target IPs are discovered from a private Docker network shared with the isolated generator. Arbitrary hostnames, external URLs, redirects, and non-project containers are rejected. Limits: 100 concurrent requests, 100 requests/second, 5 minutes, 0.5 CPU and 256 MB RAM for the generator.</p>
            {loadJob && <div className="stress-result"><span className={loadJob.capacity_level >= 2 ? "status-pill danger" : loadJob.capacity_level === 1 ? "status-pill warning" : loadJob.status === "running" ? "status-pill warning" : "status-pill success"}>{String(loadJob.status).toUpperCase()}</span>
              <b>{loadJob.service ?? "Service"} · {loadJob.completed_requests ?? 0} requests · {loadJob.failed_requests ?? 0} errors</b>
              {loadJob.latency_ms && <span>Latency p50 / p95 / p99: {loadJob.latency_ms.p50 ?? "—"} / {loadJob.latency_ms.p95 ?? "—"} / {loadJob.latency_ms.p99 ?? "—"} ms</span>}
              {loadJob.error_rate !== undefined && <span>Error rate {Number(loadJob.error_rate * 100).toFixed(2)}% · Throughput {loadJob.requests_per_second} req/s</span>}
              {loadJob.capacity_level !== undefined && <span>Capacity status: {loadJob.capacity_level === 2 ? "Critical" : loadJob.capacity_level === 1 ? "Warning" : "Within configured thresholds"}</span>}
            </div>}
          </section>}
          {page === "Alerts & audit" && <section className="content-grid stacked-panels"><div className="panel"><PanelTitle title="Infrastructure alerts" subtitle="Pending and firing Prometheus alerts, including stress-test capacity events"/><InfraAlerts data={infraAlerts}/></div><div className="panel"><PanelTitle title="Recent alerts" subtitle="Policy alerts and recorded stress-test capacity warnings"/><RecordTable rows={data.alerts} empty="No alert records returned."/></div><div className="panel"><PanelTitle title="Remediation audit" subtitle="Container actions, stress-test lifecycle, and executed, dry-run, blocked or failed decisions"/><RecordTable rows={data.audit} empty="No remediation decisions recorded."/></div></section>}

          <footer className="footer"><span>PredictiveOps <b>·</b> Reliability intelligence</span><span>Refresh interval 10s <b>·</b> {loading ? "Connecting…" : "API snapshot loaded"}</span></footer>
        </div>
      </main>
    </div>
  );
}

const RANGES = [[15, "15m"], [60, "1h"], [360, "6h"], [1440, "24h"]] as const;
const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

type MetricSeries = { key: string; name: string; color: string; forecastKey?: string };
function MetricChart({ title, unit, data, latest, series, domain, forecastStart }: {
  title: string; unit: string; data: AnyRecord[]; latest: AnyRecord | undefined;
  series: MetricSeries[]; domain?: [number, number]; forecastStart?: number;
}) {
  const now = series.map(item => {
    const value = latest?.[item.key];
    return typeof value === "number" ? `${value.toFixed(1)}${unit}` : "—";
  }).join(" · ");
  return (
    <div className="metric-card">
      <div className="metric-head"><span>{title}</span><b>{now}</b></div>
      <div className="metric-chart">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 6, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid stroke="#202a39" vertical={false}/>
            <XAxis dataKey="time" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={clock} tick={{ fill: "#8491a4", fontSize: 9 }} axisLine={false} tickLine={false} minTickGap={36}/>
            <YAxis domain={domain ?? [0, "auto"]} tick={{ fill: "#8491a4", fontSize: 9 }} axisLine={false} tickLine={false}/>
            <Tooltip
              contentStyle={{ background: "#111925", border: "1px solid #2a3749", borderRadius: 9, color: "#e7edf6", fontSize: 11 }}
              labelFormatter={value => new Date(Number(value)).toLocaleString()}
              formatter={(value, name) => [typeof value === "number" ? `${value.toFixed(2)}${unit}` : "—", name]}
            />
            {series.map(item => (
              <Area key={item.key} type="monotone" dataKey={item.key} name={item.name} stroke={item.color} fill={item.color} fillOpacity={0.14} strokeWidth={1.6} dot={false} connectNulls={false} isAnimationActive={false}/>
            ))}
            {series.filter(item => item.forecastKey).map(item => (
              <Line key={item.forecastKey} type="monotone" dataKey={item.forecastKey} name={item.name + " forecast"}
                stroke={item.color} strokeWidth={2} strokeDasharray="5 5" dot={false} activeDot={{r:3}}
                connectNulls isAnimationActive={false}/>
            ))}
            {forecastStart && Number.isFinite(forecastStart) && <ReferenceLine x={forecastStart} stroke="#a5b4c7" strokeDasharray="2 4" label={{value:"NOW",fill:"#a5b4c7",fontSize:8,position:"insideTopRight"}}/>}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
function InfraAlerts({ data }: { data: AnyRecord | null }) {
  if (!data) return <EmptyState icon={<AlertTriangle/>} title="Alert status unavailable" text="The API could not reach Prometheus for alert state."/>;
  if (data.available === false) return <EmptyState icon={<AlertTriangle/>} title="Alert status unavailable" text={data.error ?? "Prometheus did not answer."}/>;
  const rows: AnyRecord[] = data.alerts ?? [];
  if (!rows.length) return <EmptyState icon={<CheckCircle2/>} title="No pending or firing alerts" text="All Prometheus alert rules are currently inactive."/>;
  return <div className="table-scroll"><table><thead><tr><th>ALERT</th><th>STATE</th><th>SEVERITY</th><th>SCOPE</th><th>SUMMARY</th><th>ACTIVE SINCE</th></tr></thead><tbody>{rows.map((row, i) => (
    <tr key={`${row.name}-${i}`}><td><span className="service-name">{row.name}</span></td><td><span className={`status-pill ${row.state === "firing" ? "danger" : "warning"}`}>{row.state}</span></td><td>{row.severity}</td><td>{row.scope ?? "—"}</td><td>{row.summary || "—"}</td><td>{time(row.active_at)}</td></tr>
  ))}</tbody></table></div>;
}

function LiveForecastPanel({ forecast }: { forecast: AnyRecord | null }) {
  const metrics: AnyRecord[] = Array.isArray(forecast?.metrics) ? forecast.metrics : [];
  const sourceStatus = forecast?.source_status ?? "waiting";
  const status = forecast?.status ?? "warming_up";
  const statusClass = status === "critical" ? "danger" : status === "warning" ? "warning" : status === "normal" ? "success" : "muted";
  const sourceClass = sourceStatus === "healthy" ? "success" : sourceStatus === "degraded" ? "warning" : "danger";
  return (
    <section className="panel full-panel">
      <PanelTitle
        title="Live host resource forecast"
        subtitle="30-minute trend forecast from your actual Node Exporter telemetry"
      />
      <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap",marginBottom:15}}>
        <span className={`status-pill ${statusClass}`}>{status.replace(/_/g," ").toUpperCase()}</span>
        <span className={`status-pill ${sourceClass}`}>SOURCE {String(sourceStatus).toUpperCase()}</span>
        <span style={{fontSize:10,color:"#8794a7"}}>
          {forecast?.samples_available ?? 0}/{forecast?.required_history ?? 30} samples available
        </span>
        {forecast?.source_age_seconds != null && <span style={{fontSize:10,color:"#8794a7"}}>Last scrape {Math.round(forecast.source_age_seconds)}s ago</span>}
      </div>
      {metrics.length ? (
        <div className="table-scroll">
          <table>
            <thead><tr>
              <th>RESOURCE</th><th>CURRENT</th><th>15-MIN FORECAST</th><th>30-MIN FORECAST</th>
              <th>TREND</th><th>TIME TO WARNING</th><th>STATUS</th>
            </tr></thead>
            <tbody>
              {metrics.map(metric => (
                <tr key={metric.metric}>
                  <td><b>{metric.label}</b></td>
                  <td>{Number(metric.current).toFixed(1)}%</td>
                  <td>{Number(metric.forecast_15m).toFixed(1)}%</td>
                  <td>{Number(metric.forecast_30m).toFixed(1)}%</td>
                  <td>{metric.trend} ({Number(metric.slope_per_minute) > 0 ? "+" : ""}{Number(metric.slope_per_minute).toFixed(3)}/min)</td>
                  <td>{metric.minutes_to_warning == null ? "Not projected" : metric.minutes_to_warning === 0 ? "Already breached" : `${metric.minutes_to_warning} min`}</td>
                  <td><span className={`status-pill ${metric.status === "critical" ? "danger" : metric.status === "warning" ? "warning" : "success"}`}>{metric.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <EmptyState
          icon={<Activity/>}
          title={sourceStatus === "stale" ? "Host telemetry is stale" : "Building live forecast history"}
          text={forecast?.message ?? "Waiting for Prometheus to collect 30 contiguous one-minute host samples."}
        />
      )}
      <div className="model-note">
        <ShieldCheck size={15}/>
        <span>{forecast?.message ?? "Forecast will appear after the host telemetry buffer has 30 contiguous samples."} Guardrails: warning at 85%, critical at 95%. No remediation is triggered.</span>
      </div>
      {forecast?.source_error && <div className="notice warning" style={{marginTop:12}}><AlertTriangle size={15}/><div><b>Prometheus poll warning</b><span>{forecast.source_error}</span></div></div>}
    </section>
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
function formatBytes(value: number | null | undefined) {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return (value / Math.pow(1024, index)).toFixed(index > 1 ? 2 : 1) + " " + units[index];
}
function formatRate(value: number | null | undefined) { return value === null || value === undefined ? "—" : formatBytes(value) + "/s"; }
function ContainerTable({rows}:{rows:AnyRecord[]}) {
  if (!rows.length) return <EmptyState icon={<Server/>} title="No container metrics yet" text="Check cAdvisor health and its Prometheus scrape target. Containers appear when cAdvisor exports resource metrics."/>;
  return <div className="table-scroll"><table><thead><tr><th>CONTAINER</th><th>COMPOSE SERVICE</th><th>CPU CORES</th><th>HOST CPU %</th><th>MEMORY WORKING SET</th><th>NETWORK RX</th><th>NETWORK TX</th></tr></thead><tbody>{rows.map(row=><tr key={row.name}><td><span className="service-name"><span className="service-dot"/>{row.name}</span></td><td>{row.service ?? "—"}</td><td>{row.cpu_cores == null ? "—" : Number(row.cpu_cores).toFixed(3)}</td><td>{row.cpu_pct == null ? "—" : Number(row.cpu_pct).toFixed(2) + "%"}</td><td>{formatBytes(row.memory_bytes)}</td><td>{formatRate(row.network_rx_bytes_s)}</td><td>{formatRate(row.network_tx_bytes_s)}</td></tr>)}</tbody></table></div>;
}
function ManagedContainerTable({rows,onAction,onLogs}:{rows:AnyRecord[];onAction:(id:string,action:string)=>void;onLogs:(id:string)=>void}) {
  if (!rows.length) return <EmptyState icon={<Server/>} title="No managed containers found" text="Add Docker container names or Compose service names to PREDICTIVEOPS_MANAGED_CONTAINER_NAMES." />;
  return <div className="table-scroll"><table><thead><tr><th>CONTAINER</th><th>SERVICE</th><th>PROJECT</th><th>STATUS</th><th>LAST EXIT</th><th>CONTROLS</th></tr></thead><tbody>{rows.map(row=><tr key={row.id}>
    <td><span className="service-name">{row.name}</span><small className="container-id">{row.short_id}</small></td><td>{row.service}</td>
    <td><small className="container-id">{row.project ?? "predictiveops"}</small></td>
    <td><span className={`status-pill ${row.status === "running" ? "success" : "danger"}`}>{row.status}</span>{row.health && <small className="container-id">Health: {row.health}</small>}</td>
    <td>{row.exit_code ?? "—"}{row.oom_killed ? " · OOM" : ""}</td>
    <td className="container-actions"><button onClick={()=>onLogs(row.id)} title="View logs"><Terminal size={13}/>Logs</button>
      {row.can_control && <>{row.status !== "running" && row.status !== "paused" && <button onClick={()=>onAction(row.id,"start")} title="Start"><Play size={13}/>Start</button>}{row.status === "running" && <><button onClick={()=>onAction(row.id,"pause")} title="Pause"><Square size={12}/>Pause</button><button onClick={()=>onAction(row.id,"restart")} title="Restart"><RotateCcw size={13}/>Restart</button><button onClick={()=>onAction(row.id,"stop")} title="Stop"><Square size={12}/>Stop</button><button className="danger-action" onClick={()=>onAction(row.id,"kill")} title="Kill"><Skull size={13}/>Kill</button></>}{row.status === "paused" && <button onClick={()=>onAction(row.id,"unpause")} title="Resume"><Play size={13}/>Resume</button>}</>}
      {!row.can_control && <span className="container-id">API protected</span>}
    </td></tr>)}</tbody></table></div>;
}
function IncidentTable({rows}:{rows:AnyRecord[]}) {
  const [selected,setSelected] = useState<AnyRecord|null>(null);
  if (!rows.length) return <EmptyState icon={<CheckCircle2/>} title="No container crashes recorded" text="Docker die and OOM events will appear here automatically." />;
  return <div className="incident-list">{rows.map(row=><button className={selected?.incident_id===row.incident_id?"incident-row selected":"incident-row"} key={row.incident_id} onClick={()=>setSelected(row)}>
    <span><b>{row.container_name}</b><small>{row.compose_service ?? "container"} · {time(row.finished_at)}</small></span>
    <span className="incident-meta"><i className={row.oom_killed?"incident-oom":"incident-exit"}/>{row.oom_killed?"OOM killed":`Exit ${row.exit_code ?? "—"}`}</span>
  </button>)}
  {selected && <div className="incident-detail"><div className="incident-detail-head"><b>Incident report · {selected.incident_id}</b><span>{selected.event}</span></div>
    <div className="incident-diagnosis"><b>DIAGNOSIS</b>{selected.diagnosis || selected.error || (selected.oom_killed ? "Container was killed by the OOM handler." : `Process exited with code ${selected.exit_code ?? "unknown"}.`)}</div>
    <div className="incident-facts">
      <span>Container <b>{selected.container_name}</b></span><span>Service <b>{selected.compose_service ?? "—"}</b></span>
      <span>Exit code <b>{selected.exit_code ?? "—"}</b></span><span>OOM killed <b>{selected.oom_killed?"Yes":"No"}</b></span>
      <span>Runtime <b>{selected.runtime_seconds == null ? "—" : `${selected.runtime_seconds}s`}</b></span>
      <span>Restart count <b>{selected.restart_count ?? "—"}</b></span>
      <span>Image <b>{selected.image ?? "—"}</b></span><span>Error <b>{selected.error || "None reported by Docker"}</b></span>
      <span>Started <b>{time(selected.started_at)}</b></span><span>Finished <b>{time(selected.finished_at)}</b></span>
      <span>Captured <b>{time(selected.captured_at)}</b></span><span>Event <b>{selected.event}</b></span>
    </div>
    <div className="incident-logs-label">CONTAINER OUTPUT · LAST 250 LINES</div>
    <pre className="live-log-output">{selected.logs_tail || "No container output was retained. Diagnose using the exit code, OOM status and Docker error above."}</pre>
  </div>}
  </div>;
}
function RecordTable({rows,empty:emptyText}:{rows:AnyRecord[];empty:string}) {
  if (!rows.length) return <EmptyState icon={<CheckCircle2/>} title="Nothing to show" text={emptyText}/>;
  const columns = Array.from(new Set(rows.slice(0,12).flatMap(row=>Object.keys(row)))).filter(key=>!["id","payload","details","metadata"].includes(key)).slice(0,5);
  return <div className="table-scroll"><table><thead><tr>{columns.map(c=><th key={c}>{c.replace(/_/g," ").toUpperCase()}</th>)}</tr></thead><tbody>{rows.slice(0,30).map((row,i)=><tr key={row.id ?? i}>{columns.map(c=><td key={c}>{typeof row[c] === "object" ? JSON.stringify(row[c]) : String(row[c] ?? "—")}</td>)}</tr>)}</tbody></table></div>;
}
export default App;
