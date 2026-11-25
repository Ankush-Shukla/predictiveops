import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Activity, AlertTriangle, ArrowDownRight, ArrowUpRight, Bell, CheckCircle2,
  ChevronRight, CircleHelp, Clock3, Cpu, ExternalLink, Gauge, LayoutDashboard,
  RefreshCw, Server, ShieldCheck, ShieldAlert, Zap,
} from "lucide-react";
import {
  Area, AreaChart, Bar, CartesianGrid, ComposedChart, Legend, Line,
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
  const [faultType, setFaultType] = useState("memory_leak");
  const [simulation, setSimulation] = useState<AnyRecord | null>(null);
  const [simulationLoading, setSimulationLoading] = useState(false);
  const [simulationError, setSimulationError] = useState<string | null>(null);
  const [rangeMinutes, setRangeMinutes] = useState(60);
  const [history, setHistory] = useState<AnyRecord | null>(null);
  const [infraAlerts, setInfraAlerts] = useState<AnyRecord | null>(null);
  const [coverage, setCoverage] = useState<AnyRecord | null>(null);

  const refreshLive = useCallback(async () => {
    const [nextHistory, nextAlerts, nextCoverage] = await Promise.all([
      get(`/v1/host/history?minutes=${rangeMinutes}`).catch(() => null),
      get("/v1/infra/alerts").catch(() => null),
      get("/v1/telemetry/coverage").catch(() => null),
    ]);
    setHistory(nextHistory);
    setInfraAlerts(nextAlerts);
    setCoverage(nextCoverage);
  }, [rangeMinutes]);

  useEffect(() => {
    void refreshLive();
    const timer = window.setInterval(() => void refreshLive(), 10000);
    return () => window.clearInterval(timer);
  }, [refreshLive]);

  const historyData = useMemo(
    () => (history?.points ?? []).map((point: AnyRecord) => ({ ...point, time: point.t * 1000 })),
    [history],
  );

  const runSimulation = useCallback(async () => {
    setSimulationLoading(true);
    setSimulationError(null);
    try {
      const response = await fetch("/v1/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          fault_type: faultType,
          minutes: 360,
          fault_start: 150,
          ramp_minutes: 60,
          seed: 42,
          spikes: true
        })
      });
      const body = await response.json().catch(() => null);
      if (!response.ok) {
        throw new Error(body?.detail ?? `Simulation failed: HTTP ${response.status}`);
      }
      setSimulation(body);
    } catch (error) {
      setSimulationError(error instanceof Error ? error.message : String(error));
    } finally {
      setSimulationLoading(false);
    }
  }, [faultType]);

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
  const primaryModel: AnyRecord | null = scopedModels?.host ?? data.model;
  const modelVersionLabel = modelCount
    ? Object.entries(scopedModels!).map(([scope, model]: [string, any]) => scope + ": " + (model.version ?? "missing")).join(" · ")
    : data.model?.version ? "Version " + data.model.version : "No champion loaded";
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
  const hostModel = scopedModels?.host;
  const applicationModel = scopedModels?.application;
  const serviceCount = data.series.length;
  const containerRows: AnyRecord[] = data.containerStatus?.containers ?? [];
  const containerCount = containerRows.length;
  const containerCpu = containerRows.length && containerRows.every(row => typeof row.cpu_cores === "number")
    ? containerRows.reduce((sum, row) => sum + row.cpu_cores, 0) : null;
  const containerMemory = containerRows.length && containerRows.every(row => typeof row.memory_bytes === "number")
    ? containerRows.reduce((sum, row) => sum + row.memory_bytes, 0) : null;
  const alertCount = data.alerts.length;
  const remediation = data.policy?.config ?? {};
  const containerChartData = useMemo(() => containerRows
    .filter(row => typeof row.memory_bytes === "number" && typeof row.cpu_cores === "number")
    .sort((a, b) => b.memory_bytes - a.memory_bytes)
    .slice(0, 10)
    .map(row => ({
      name: row.service ?? row.name,
      memoryMiB: Number((row.memory_bytes / (1024 * 1024)).toFixed(1)),
      cpuCores: Number(row.cpu_cores.toFixed(3)),
    })), [containerRows]);
  const simulationChartData = useMemo(() => {
    if (!simulation) return [];
    return (simulation.probability ?? []).map((probability: number | null, minute: number) => ({
      minute,
      probability,
      threshold: simulation.threshold,
      unhealthy: simulation.is_unhealthy?.[minute] ? 1 : 0
    }));
  }, [simulation]);
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
            <div><div className="eyebrow">SYSTEM OVERVIEW <span className="eyebrow-line"/></div><h1>{page}</h1><p>{page === "Overview" ? "Predictive service health, model readiness and operational signals." : page === "Predictions" ? "Current model assessments for monitored service series." : page === "Alerts & audit" ? "Alert history and gated remediation decisions." : page === "Containers" ? "Live CPU, memory and network usage by Docker container." : "Operational monitoring."}</p></div>
            <div className="updated"><Clock3 size={14}/><span>{lastUpdated ? `Updated ${lastUpdated.toLocaleTimeString()}` : "Waiting for data"}</span></div>
          </div>

          {data.errors.length > 0 && <div className="notice warning"><AlertTriangle size={16}/><div><b>Some API endpoints could not be loaded</b><span>{data.errors.join(" · ")}</span></div></div>}
          {!isReady && data.ready?.reason && <div className="notice warning"><ShieldAlert size={16}/><div><b>Production inference is blocked</b><span>{data.ready.reason}. The API will not publish predictions from an unqualified model.</span></div></div>}

          {page === "Overview" && <>
            <section className="stat-grid">
              <StatCard icon={<ShieldCheck/>} label="MODEL STATUS" value={isReady ? "Ready" : "Not ready"} detail={modelCount ? `Host ${hostModel ? "loaded" : "missing"} · Applications ${applicationModel ? "loaded" : "missing"}` : "Champion model unavailable"} tone={isReady ? "green" : "amber"} />
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
              {history?.available ? (
                <div className="metric-grid">
                  <MetricChart title="CPU utilisation" unit="%" data={historyData} latest={history.latest} domain={[0, 100]} series={[{ key: "cpu_pct", name: "CPU", color: "#5c96ff" }]}/>
                  <MetricChart title="Memory used" unit="%" data={historyData} latest={history.latest} domain={[0, 100]} series={[{ key: "memory_pct", name: "Memory", color: "#45c49a" }]}/>
                  <MetricChart title="Disk I/O utilisation" unit="%" data={historyData} latest={history.latest} domain={[0, 100]} series={[{ key: "disk_io_util_pct", name: "Disk I/O", color: "#e8b45b" }]}/>
                  <MetricChart title="Network (physical NICs)" unit=" Mbps" data={historyData} latest={history.latest} series={[{ key: "network_rx_mbps", name: "Receive", color: "#5c96ff" }, { key: "network_tx_mbps", name: "Transmit", color: "#f07878" }]}/>
                  <MetricChart title="Load average (1m)" unit="" data={historyData} latest={history.latest} series={[{ key: "load1", name: "Load", color: "#b794f6" }]}/>
                  <MetricChart title="Root filesystem used" unit="%" data={historyData} latest={history.latest} domain={[0, 100]} series={[{ key: "root_disk_used_pct", name: "Root disk", color: "#f0a36b" }]}/>
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
            <ExperimentalHostPanel prediction={data.experimentalPrediction}/>
            <section className="content-grid">
              <div className="panel risk-panel">
                <PanelTitle
                  title="Live container resource usage"
                  subtitle="Top 10 containers by memory · refreshed every 10 seconds"
                />
                {containerChartData.length ? (
                  <div className="chart-wrap">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={containerChartData} margin={{top:12,right:12,left:0,bottom:8}}>
                        <CartesianGrid stroke="#202a39" vertical={false}/>
                        <XAxis dataKey="name" tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false}/>
                        <YAxis yAxisId="memory" tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false} tickFormatter={v => `${v} MB`}/>
                        <YAxis yAxisId="cpu" orientation="right" tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false} tickFormatter={v => `${v}`}/>
                        <Tooltip
                          contentStyle={{background:"#111925",border:"1px solid #2a3749",borderRadius:9,color:"#e7edf6"}}
                          formatter={(value, name) => [
                            name === "Memory" ? `${value} MiB` : `${value} CPU cores`,
                            name
                          ]}
                        />
                        <Legend wrapperStyle={{fontSize:11,color:"#aab6c8"}}/>
                        <Bar yAxisId="memory" dataKey="memoryMiB" name="Memory" fill="#4f8cff" fillOpacity={0.75} radius={[4,4,0,0]}/>
                        <Line yAxisId="cpu" type="monotone" dataKey="cpuCores" name="CPU" stroke="#36c9a0" strokeWidth={2} dot={{r:3}}/>
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <EmptyState icon={<Activity/>} title="Waiting for container metrics" text="Resource usage will appear when cAdvisor reports container CPU and memory readings."/>
                )}
                <div className="chart-foot">
                  <span><i className="legend-dot blue"/>Memory working set (MiB)</span>
                  <span>CPU consumption (cores)</span>
                </div>
              </div>
              <div className="panel model-panel">
                <PanelTitle title="Model details" subtitle="Independent host and application model metadata"/>
                <div className="model-version"><div className="model-icon"><Cpu size={20}/></div><div><b>{primaryModel?.model_type ?? (modelCount ? "Scoped prediction models" : "Model unavailable")}</b><span>{modelVersionLabel}</span></div><span className={isReady ? "status-pill success" : "status-pill warning"}>{isReady ? "READY" : "OFFLINE"}</span></div>
                <Detail label="Host model" value={hostModel ? hostModel.version : "Not loaded"}/>
                <Detail label="Application model" value={applicationModel ? applicationModel.version : "Not loaded"}/>
                <Detail label="Decision threshold" value={primaryModel?.threshold !== undefined ? pretty(primaryModel.threshold) : "—"}/>
                <Detail label="Required history" value={primaryModel?.required_history_minutes ? `${primaryModel.required_history_minutes} minutes` : "—"}/>
                <Detail label="Feature count" value={primaryModel?.n_features ?? "—"}/>
                <Detail label="Training data" value={primaryModel?.data_source ?? "—"}/>
                <div className="model-note"><ShieldAlert size={15}/><span>{modelCount ? "Host and application models are isolated; both must pass production gates before readiness." : "No production-qualified champion is loaded. Live infrastructure monitoring remains active; predictions are unavailable."}</span></div>
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
            <ExperimentalHostPanel prediction={data.experimentalPrediction}/>
            <section className="panel full-panel">
              <PanelTitle
                title="Predictive failure simulation"
                subtitle="Run the calibrated HGB model against a controlled fault scenario"
              />
              <div className="notice warning" style={{marginBottom:18}}>
                <ShieldAlert size={17}/>
                <div>
                  <b>SYNTHETIC SIMULATION — NOT A LIVE FAILURE FORECAST</b>
                  <span>This uses generated scenario data and a model trained on synthetic data. It does not predict the current state of your real containers.</span>
                </div>
              </div>

              <div style={{display:"flex",gap:12,alignItems:"end",flexWrap:"wrap",marginBottom:20}}>
                <label style={{display:"grid",gap:7,color:"#aab6c8",fontSize:12,minWidth:220}}>
                  FAULT SCENARIO
                  <select
                    value={faultType}
                    onChange={event => setFaultType(event.target.value)}
                    disabled={simulationLoading}
                    style={{background:"#111925",color:"#e7edf6",border:"1px solid #2a3749",borderRadius:8,padding:"11px 12px",fontSize:14}}
                  >
                    <option value="memory_leak">Memory leak</option>
                    <option value="latency_creep">Latency creep</option>
                    <option value="error_ramp">Error rate ramp</option>
                    <option value="cpu_contention">CPU contention</option>
                    <option value="disk_saturation">Disk saturation</option>
                    <option value="none">Healthy baseline</option>
                  </select>
                </label>
                <button
                  type="button"
                  onClick={() => void runSimulation()}
                  disabled={simulationLoading}
                  style={{background:"#3978e8",color:"#fff",border:0,borderRadius:8,padding:"12px 18px",fontWeight:700,cursor:simulationLoading ? "wait" : "pointer",opacity:simulationLoading ? 0.65 : 1}}
                >
                  {simulationLoading ? "Running model…" : "Run simulation"}
                </button>
                <span style={{fontSize:12,color:"#8491a4"}}>360 minutes · 20-minute prediction horizon · fixed seed 42</span>
              </div>

              {simulationError && <div className="notice warning"><AlertTriangle size={16}/><div><b>Simulation request failed</b><span>{simulationError}</span></div></div>}

              {!simulation && !simulationLoading && !simulationError && (
                <EmptyState icon={<Gauge/>} title="No simulation run yet" text="Select a fault scenario and run the HGB inference pipeline to inspect predicted failure probabilities."/>
              )}

              {simulation && <>
                <div className="stat-grid" style={{marginBottom:20}}>
                  <StatCard icon={<Gauge/>} label="PEAK FAILURE PROBABILITY" value={`${(Math.max(0,...simulation.probability.filter((v: number | null): v is number => v !== null))*100).toFixed(1)}%`} detail={`Decision threshold ${(simulation.threshold*100).toFixed(2)}%`} tone="amber"/>
                  <StatCard icon={<Clock3/>} label="FORECAST HORIZON" value={`${simulation.horizon_minutes} min`} detail={`Model ${simulation.model_version}`} tone="blue"/>
                  <StatCard icon={<AlertTriangle/>} label="EARLY WARNING" value={simulation.summary.lead_time_minutes == null ? "No detection" : `${simulation.summary.lead_time_minutes} min`} detail="Lead time before simulated failure" tone="green"/>
                  <StatCard icon={<Activity/>} label="ALERT MINUTES" value={String(simulation.summary.alert_minutes)} detail={`${simulation.summary.false_alert_minutes} false-alert minutes`} tone="amber"/>
                </div>

                <div className="panel" style={{marginBottom:18}}>
                  <PanelTitle title="Predicted failure probability" subtitle="Calibrated HGB probability at each simulated minute · dashed line is the decision threshold"/>
                  <div className="chart-wrap">
                    <ResponsiveContainer width="100%" height="100%">
                      <AreaChart data={simulationChartData} margin={{top:12,right:16,left:0,bottom:8}}>
                        <CartesianGrid stroke="#202a39" vertical={false}/>
                        <XAxis dataKey="minute" tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false} label={{value:"Scenario minute",position:"insideBottom",offset:-3,fill:"#8491a4",fontSize:10}}/>
                        <YAxis domain={[0,1]} tick={{fill:"#8491a4",fontSize:10}} axisLine={false} tickLine={false} tickFormatter={v => `${Math.round(v*100)}%`}/>
                        <Tooltip
                          contentStyle={{background:"#111925",border:"1px solid #2a3749",borderRadius:9,color:"#e7edf6"}}
                          labelFormatter={v => `Minute ${v}`}
                          formatter={(value, name) => [`${(Number(value)*100).toFixed(2)}%`, name === "probability" ? "Failure probability" : "Decision threshold"]}
                        />
                        <Legend wrapperStyle={{fontSize:11,color:"#aab6c8"}}/>
                        <Area type="monotone" dataKey="probability" name="Failure probability" stroke="#4f8cff" fill="#4f8cff" fillOpacity={0.20} strokeWidth={2} connectNulls={false} dot={false}/>
                        <Line type="monotone" dataKey="threshold" name="Decision threshold" stroke="#e7a94b" strokeWidth={2} strokeDasharray="6 4" dot={false}/>
                      </AreaChart>
                    </ResponsiveContainer>
                  </div>
                  <div className="chart-foot">
                    <span><i className="legend-dot blue"/>Model probability</span>
                    <span>Threshold: {(simulation.threshold*100).toFixed(2)}%</span>
                    <span>{simulationChartData.filter((point: AnyRecord) => point.probability !== null).length} scored points</span>
                  </div>
                </div>

                <div className="content-grid">
                  <div className="panel">
                    <PanelTitle title="Scenario outcome" subtitle="Ground truth from the generated scenario"/>
                    <Detail label="Scenario" value={faultType.replace(/_/g," ")}/>
                    <Detail label="Failure onset" value={simulation.summary.failure_start_minute == null ? "No failure injected" : `Minute ${simulation.summary.failure_start_minute}`}/>
                    <Detail label="First alert" value={simulation.summary.first_alert_minute == null ? "No alert" : `Minute ${simulation.summary.first_alert_minute}`}/>
                    <Detail label="Lead time" value={simulation.summary.lead_time_minutes == null ? "—" : `${simulation.summary.lead_time_minutes} minutes`}/>
                    <Detail label="False-alert minutes" value={simulation.summary.false_alert_minutes}/>
                  </div>
                  <div className="panel">
                    <PanelTitle title="Policy replay" subtitle="Isolated in-memory policy engine · forced dry-run"/>
                    <Detail label="Alert minutes" value={simulation.policy_summary.alert_minutes}/>
                    <Detail label="False-alert minutes" value={simulation.policy_summary.false_alert_minutes}/>
                    <Detail label="Sustained alert decisions" value={simulation.policy_summary.sustain_alert}/>
                    <Detail label="Sustained remediation decisions" value={simulation.policy_summary.sustain_remediate}/>
                    <Detail label="Would plan remediation" value={simulation.policy_summary.would_remediate_minute == null ? "No" : `Minute ${simulation.policy_summary.would_remediate_minute}`}/>
                    <div className="model-note"><ShieldCheck size={15}/><span>Dry-run only. No production containers were restarted or modified.</span></div>
                  </div>
                </div>
                <div style={{fontSize:11,color:"#8491a4",marginTop:14}}>
                  Synthetic data · Model {simulation.model_version} · Scenario start {time(simulation.start)} · {simulation.minutes} samples
                </div>
              </>}
            </section>

            <section className="panel full-panel">
              <PanelTitle title="Real telemetry collected" subtitle="What Prometheus holds for this host today, the raw material for training on real data"/>
              {coverage?.available ? <>
                <Detail label="Collection started" value={coverage.first_sample ? time(new Date(coverage.first_sample * 1000).toISOString()) : "No samples yet"}/>
                <Detail label="Hours with host telemetry" value={`${coverage.covered_hours} h (last ${coverage.window_days} days)`}/>
                <Detail label="Latest sample" value={coverage.last_sample ? time(new Date(coverage.last_sample * 1000).toISOString()) : "—"}/>
                <div className="model-note"><ShieldAlert size={15}/><span>Telemetry alone cannot train the model. Label real incidents (see scripts/annotate_incident.py and docs/production-telemetry.md), export with scripts/export_telemetry.py, then run the trainer. Until both scope models pass the gates, live assessments below stay blocked.</span></div>
              </> : <EmptyState icon={<Clock3/>} title="Coverage unavailable" text={coverage?.error ?? "Could not read Prometheus coverage."}/>}
            </section>

            <section className="panel full-panel">
              <PanelTitle title="Live production assessments" subtitle="Separate from the synthetic simulation above · requires production-qualified scoped models"/>
              <SeriesTable rows={data.series} expanded/>
            </section>
          </>}
          {page === "Containers" && <>
            <section className="stat-grid container-summary">
              <StatCard icon={<Server/>} label="RUNNING CONTAINERS" value={String(containerCount)} detail={data.containerStatus?.available ? "Reporting through cAdvisor" : "Waiting for cAdvisor metrics"} tone="blue"/>
              <StatCard icon={<Cpu/>} label="TOTAL CPU" value={containerCpu === null ? "—" : containerCpu.toFixed(2) + " cores"} detail="Aggregate container CPU consumption" tone="green"/>
              <StatCard icon={<Activity/>} label="MEMORY WORKING SET" value={formatBytes(containerMemory)} detail="Sum of container working sets" tone="amber"/>
              <StatCard icon={<Zap/>} label="TELEMETRY" value={data.containerStatus?.available ? "Live" : "Unavailable"} detail="Prometheus · cAdvisor · 15s scrape" tone={data.containerStatus?.available ? "green" : "amber"}/>
            </section>
            <section className="panel full-panel">
              <PanelTitle title="Container resource inventory" subtitle="One row per container · refreshed every 10 seconds"/>
              {data.containerStatus?.error && <div className="notice warning"><AlertTriangle size={16}/><div><b>Container telemetry unavailable</b><span>{data.containerStatus.error}</span></div></div>}
              <ContainerTable rows={containerRows}/>
            </section>
            <section className="container-grafana-link"><span>Historical container trends, filesystem I/O and network charts</span><a href={"http://" + window.location.hostname + ":3000/d/predictiveops/predictiveops"} target="_blank" rel="noreferrer">Open infrastructure dashboard <ExternalLink size={13}/></a></section>
          </>}
          {page === "Alerts & audit" && <section className="content-grid stacked-panels"><div className="panel"><PanelTitle title="Infrastructure alerts" subtitle="Pending and firing Prometheus alerts"/><InfraAlerts data={infraAlerts}/></div><div className="panel"><PanelTitle title="Recent alerts" subtitle="Policy-generated service alerts"/><RecordTable rows={data.alerts} empty="No alert records returned."/></div><div className="panel"><PanelTitle title="Remediation audit" subtitle="Executed, dry-run, blocked and skipped decisions"/><RecordTable rows={data.audit} empty="No remediation decisions recorded."/></div></section>}

          <footer className="footer"><span>PredictiveOps <b>·</b> Reliability intelligence</span><span>Refresh interval 10s <b>·</b> {loading ? "Connecting…" : "API snapshot loaded"}</span></footer>
        </div>
      </main>
    </div>
  );
}

const RANGES = [[15, "15m"], [60, "1h"], [360, "6h"], [1440, "24h"]] as const;
const clock = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

type MetricSeries = { key: string; name: string; color: string };
function MetricChart({ title, unit, data, latest, series, domain }: {
  title: string; unit: string; data: AnyRecord[]; latest: AnyRecord | undefined;
  series: MetricSeries[]; domain?: [number, number];
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

function ExperimentalHostPanel({ prediction }: { prediction: AnyRecord | null }) {
  const status = prediction?.status ?? "unavailable";
  const metrics = prediction?.metrics ?? {};
  const statusClass = status === "scored" ? "warning" : status === "warming_up" ? "muted" : "danger";
  const metricRows = [
    ["CPU utilisation", "cpu_pct", "%"],
    ["Memory utilisation", "memory_pct", "%"],
    ["Disk I/O utilisation", "disk_io_util_pct", "%"],
  ] as const;

  return (
    <section className="panel full-panel">
      <PanelTitle
        title="Experimental host failure scoring"
        subtitle="Synthetic-trained model evaluated against live host telemetry"
      />
      <div className="notice warning">
        <ShieldAlert size={16}/>
        <div>
          <b>EXPERIMENTAL · NOT PRODUCTION-QUALIFIED</b>
          <span>
            This model was trained on synthetic host-fault data. Its score is
            observational only, is not production failure evidence, and does
            not trigger alerts, policy changes, or remediation.
          </span>
        </div>
      </div>
      <div style={{display:"flex",gap:10,alignItems:"center",flexWrap:"wrap",margin:"14px 0"}}>
        <span className={`status-pill ${statusClass}`}>{status.replace(/_/g, " ").toUpperCase()}</span>
        {prediction?.model_version && <span style={{fontSize:10,color:"#8794a7"}}>Model {prediction.model_version}</span>}
        {prediction?.buffered_samples != null && (
          <span style={{fontSize:10,color:"#8794a7"}}>
            {prediction.buffered_samples}/{prediction.required_history_minutes ?? 30} samples
          </span>
        )}
      </div>
      {prediction && status === "scored" ? (
        <>
          <div className="stat-grid">
            <StatCard
              icon={<Gauge/>}
              label="EXPERIMENTAL SCORE"
              value={`${(Number(prediction.probability) * 100).toFixed(1)}%`}
              detail={`Threshold ${(Number(prediction.threshold) * 100).toFixed(1)}%`}
              tone="amber"
            />
            <StatCard
              icon={<Activity/>}
              label="MODEL OUTPUT"
              value={prediction.predicted_failure ? "Positive" : "Negative"}
              detail={`${prediction.horizon_minutes ?? "—"} minute horizon`}
              tone="blue"
            />
            <StatCard
              icon={<ShieldCheck/>}
              label="QUALITY GATES"
              value={prediction.quality_gates_passed ? "Passed" : "Failed"}
              detail="Synthetic test evaluation only"
              tone={prediction.quality_gates_passed ? "green" : "amber"}
            />
          </div>
          <div className="table-scroll">
            <table>
              <thead><tr><th>LIVE HOST METRIC</th><th>VALUE</th></tr></thead>
              <tbody>
                {metricRows.filter(([, key]) => metrics[key] != null).map(([label, key, unit]) => (
                  <tr key={key}>
                    <td>{label}</td>
                    <td>{Number(metrics[key]).toFixed(1)}{unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="model-note">
            <Clock3 size={15}/>
            <span>Scored at {time(prediction.as_of)}. Data source: {prediction.data_source ?? "synthetic-trained model on live metrics"}.</span>
          </div>
          {prediction.quality_gates_passed !== true && (
            <div className="notice warning" style={{marginTop:12}}>
              <AlertTriangle size={15}/>
              <div><b>Experimental quality gates did not pass</b><span>Do not interpret this score as a reliable failure estimate.</span></div>
            </div>
          )}
        </>
      ) : (
        <EmptyState
          icon={<Activity/>}
          title={status === "warming_up" ? "Building host telemetry history" : "Experimental model unavailable"}
          text={
            prediction?.disclaimer
            ?? "The isolated experimental model is not installed or the endpoint could not be reached."
          }
        />
      )}
    </section>
  );
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
      <div className="notice warning">
        <Activity size={16}/>
        <div>
          <b>LIVE TELEMETRY · RESOURCE CAPACITY FORECAST</b>
          <span>This is a trend-based forecast of CPU, memory and disk-I/O guardrail crossings. The pressure index is not a failure probability and does not use the synthetic demo model.</span>
        </div>
      </div>
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
  return <div className="table-scroll"><table><thead><tr><th>CONTAINER</th><th>COMPOSE SERVICE</th><th>CPU CORES</th><th>MEMORY WORKING SET</th><th>NETWORK RX</th><th>NETWORK TX</th></tr></thead><tbody>{rows.map(row=><tr key={row.name}><td><span className="service-name"><span className="service-dot"/>{row.name}</span></td><td>{row.service ?? "—"}</td><td>{row.cpu_cores === null ? "—" : Number(row.cpu_cores).toFixed(3)}</td><td>{formatBytes(row.memory_bytes)}</td><td>{formatRate(row.network_rx_bytes_s)}</td><td>{formatRate(row.network_tx_bytes_s)}</td></tr>)}</tbody></table></div>;
}
function RecordTable({rows,empty:emptyText}:{rows:AnyRecord[];empty:string}) {
  if (!rows.length) return <EmptyState icon={<CheckCircle2/>} title="Nothing to show" text={emptyText}/>;
  const columns = Array.from(new Set(rows.slice(0,12).flatMap(row=>Object.keys(row)))).filter(key=>!["id","payload","details","metadata"].includes(key)).slice(0,5);
  return <div className="table-scroll"><table><thead><tr>{columns.map(c=><th key={c}>{c.replace(/_/g," ").toUpperCase()}</th>)}</tr></thead><tbody>{rows.slice(0,30).map((row,i)=><tr key={row.id ?? i}>{columns.map(c=><td key={c}>{typeof row[c] === "object" ? JSON.stringify(row[c]) : String(row[c] ?? "—")}</td>)}</tr>)}</tbody></table></div>;
}
export default App;
