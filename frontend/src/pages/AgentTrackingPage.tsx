import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ArrowLeft,
  Activity,
  AlertCircle,
  CalendarDays,
  Check,
  ChevronDown,
  CircleDashed,
  Clock3,
  LockKeyhole,
  MapPin,
  Play,
  CircleCheck,
  CircleX,
  FileText,
  Send,
  Smile,
  MessageSquare,
  Bot,
  MessageCircle,
  RefreshCw,
  Search,
  UserRound,
  Wrench,
  Workflow,
} from "lucide-react";
import {
  getAgentTracking,
  getAgentTrackingJob,
  type AgentTrackedJob,
  type AgentTrackingJobDetail,
  type AgentTrackingStage,
  type AgentTrackingResponse,
} from "../services/agentTrackingService";
import "./AgentTrackingPage.css";

type FilterKey = "all" | "active" | "complete" | "failed";

const FILTERS: Array<{ key: FilterKey; label: string }> = [
  { key: "all", label: "All Jobs" },
  { key: "active", label: "Running" },
  { key: "complete", label: "Completed" },
  { key: "failed", label: "Failed" },
];

function isFailed(job: AgentTrackedJob): boolean { return ["FAILED", "CANCELLED", "CANCELED"].includes(job.status.toUpperCase()); }
function progressFor(job: AgentTrackedJob): number {
  if (isTerminal(job) && !isFailed(job)) return 100;
  const done = job.stages.filter((stage) => stage.state === "complete").length;
  const active = job.stages.some((stage) => stage.state === "active") ? 1 : 0;
  return Math.min(99, Math.round(((done + active * 0.45) / job.stages.length) * 100));
}

function formatTimestamp(value: string | null): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Not recorded";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function isTerminal(job: AgentTrackedJob): boolean {
  return ["COMPLETED", "CLOSED", "CANCELLED", "CANCELED"].includes(
    job.status.toUpperCase(),
  );
}

function stageIcon(stage: AgentTrackingStage) {
  if (stage.state === "complete") return <Check size={15} />;
  if (stage.state === "active") return <Activity size={15} />;
  if (stage.state === "waiting") return <Clock3 size={15} />;
  if (stage.state === "locked") return <LockKeyhole size={14} />;
  return <CircleDashed size={15} />;
}

function stageStateLabel(state: AgentTrackingStage["state"]): string {
  switch (state) {
    case "complete": return "Complete";
    case "active": return "Active";
    case "waiting": return "Waiting";
    case "locked": return "After acceptance";
    case "event_driven": return "On event";
  }
}

function JobWorkflowCard({ job, onOpen }: { job: AgentTrackedJob; onOpen: () => void }) {
  const terminal = isTerminal(job);
  const visibleStages = job.accepted ? job.stages : job.stages.slice(0, 3);
  return (
    <article className="agent-track-card" role="button" tabIndex={0} onClick={onOpen} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); onOpen(); } }} aria-label={`View job ${job.job_number} progress`}>
      <div className="agent-track-card-head">
        <div className="agent-track-job-heading">
          <span className="agent-track-job-id">JOB #{job.job_number}</span>
          <h2>{job.service_type || "Service job"}</h2>
          <span className="agent-track-job-meta">
            Updated {formatTimestamp(job.updated_at)}
          </span>
        </div>
        <div className="agent-track-job-badges">
          <span className={`agent-track-status ${terminal ? "is-terminal" : ""}`}>
            {job.status.replace(/_/g, " ")}
          </span>
          {job.priority ? <span className="agent-track-priority">{job.priority}</span> : null}
        </div>
      </div>

      <div className={`agent-track-current ${terminal ? "is-terminal" : job.accepted ? "is-working" : "is-waiting"}`}>
        <div className="agent-track-current-icon"><Workflow size={19} /></div>
        <div className="agent-track-current-copy">
          <span className="agent-track-eyebrow">CURRENT WORKFLOW STAGE</span>
          <strong>{job.current_agent}</strong>
          <span>{job.current_detail}</span>
        </div>
        <div className="agent-track-current-technician">
          <span>TECHNICIAN</span>
          <strong>{job.technician_name || "Not assigned"}</strong>
          <small>{job.accepted ? "Accepted" : job.assigned_at ? "Awaiting acceptance" : "Not assigned"}</small>
        </div>
      </div>

      <div className="agent-track-stages" role="list" aria-label={`Agent workflow for job ${job.job_number}`}>
        {visibleStages.map((stage, index) => (
          <div
            className={`agent-track-stage state-${stage.state}`}
            role="listitem"
            key={stage.key}
          >
            <div className="agent-track-stage-top">
              <span className="agent-track-stage-icon">{stageIcon(stage)}</span>
              {index < visibleStages.length - 1 ? <span className="agent-track-stage-connector" /> : null}
            </div>
            <strong>{stage.label}</strong>
            <span className="agent-track-stage-state">{stageStateLabel(stage.state)}</span>
            <p>{stage.detail}</p>
          </div>
        ))}
      </div>
      <div className="agent-track-open-hint">Open job progress <span>→</span></div>
    </article>
  );
}

function JobProgressDetail({
  job,
  detailed,
  onToggleDetail,
  onClose,
}: {
  job: AgentTrackingJobDetail;
  detailed: boolean;
  onToggleDetail: () => void;
  onClose: () => void;
}) {
  const terminal = isTerminal(job);
  const [expandedEvents, setExpandedEvents] = useState<number[]>([]);
  useEffect(() => {
    const firstCommunication = job.events.findIndex((event) => event.kind === "communication");
    setExpandedEvents(firstCommunication >= 0 ? [firstCommunication] : []);
  }, [job.job_id, job.events]);

  const toggleEvent = (index: number) => {
    setExpandedEvents((current) => current.includes(index)
      ? current.filter((value) => value !== index)
      : [...current, index]);
  };
  const toggleDetailedLog = () => {
    onToggleDetail();
    setExpandedEvents(detailed ? [] : job.events.map((_, index) => index));
  };
  const agentCards = [
    { name: "Intake Agent", icon: <FileText size={21}/>, active: true, running: false },
    { name: "Planning Agent", icon: <CalendarDays size={21}/>, active: Boolean(job.assigned_at), running: false },
    { name: "Dispatcher Agent", icon: <Send size={21}/>, active: Boolean(job.assigned_at), running: job.current_agent === "Dispatcher" },
    { name: "Monitoring Agent", icon: <Activity size={21}/>, active: job.accepted, running: ["EN_ROUTE", "ON_SITE", "IN_PROGRESS"].includes(job.status.toUpperCase()) },
    { name: "Communication Agent", icon: <MessageSquare size={21}/>, active: job.accepted, running: false },
    { name: "Sentiment Agent", icon: <Smile size={21}/>, active: job.events.some((event) => event.kind === "sentiment"), running: false },
    { name: "Closure Agent", icon: <CircleCheck size={21}/>, active: isTerminal(job), running: job.status.toUpperCase() === "COMPLETED" },
  ];

  return (
      <section className="agent-track-detail-page" aria-labelledby="agent-track-detail-title">
        <header className="agent-track-detail-topbar"><div className="agent-track-topbar-mark"><Workflow size={29}/></div><div><h1 id="agent-track-detail-title">Agent Tracking</h1><p>Monitor AI agents, their executions, and outcomes in real time.</p></div><span className="agent-track-date"><CalendarDays size={17}/>Today, {new Date().toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}<ChevronDown size={15}/></span></header>
        <div className="agent-track-agent-strip" aria-label="Agent status overview">{agentCards.map((agent) => <div key={agent.name} className={`agent-track-agent-card ${agent.running ? "is-running" : ""}`}><span className="agent-track-agent-card-icon">{agent.icon}</span><span className="agent-track-agent-card-copy"><strong>{agent.name}</strong><small className={agent.active ? "is-active" : "is-idle"}><i/>{agent.running ? "Running" : agent.active ? "Active" : "Idle"}</small></span></div>)}</div>
        <div className="agent-track-detail-banner"><span className="agent-track-banner-chip"><Bot size={15}/> AI Agents</span><strong>Agent Tracking</strong><i/><p>Track and monitor all FieldOps AI agents in real time —<br/> from planning to communication and resolution.</p><span className="agent-track-banner-graphic"><img src="/ai-agent-chip.png" alt="AI processor with connected workflow icons" /></span></div>
        <div className="agent-track-detail">
        <header className="agent-track-detail-header">
          <button className="agent-track-back" onClick={onClose} aria-label="Back to job workflows"><ArrowLeft size={19} /></button>
          <div><h2>Job Progress</h2><p>See how the agents worked together on this job</p></div>
          <span className="agent-track-live"><i /> Live</span>
        </header>

        <div className="agent-track-detail-summary">
          <div className="agent-track-detail-summary-icon"><Wrench size={23} /></div>
          <div><span>Job ID</span><strong>JOB-{job.job_number}</strong></div>
          <div><UserRound size={21} /><span>Customer</span><strong>{job.customer_name || "Customer"}</strong></div>
          <div><Wrench size={21} /><span>Service</span><strong>{job.service_type || "Service"}</strong></div>
          <div><CalendarDays size={21} /><span>Preferred Visit</span><strong>{[job.preferred_service_date, job.preferred_service_time].filter(Boolean).join(", ") || "Not recorded"}</strong></div>
          <span className={`agent-track-detail-status ${terminal ? "is-terminal" : ""}`}>{job.status.replace(/_/g, " ")}</span>
        </div>
        {job.location ? <div className="agent-track-detail-location"><MapPin size={16} />{job.location}</div> : null}

        <div className="agent-track-progress-rail" role="list" aria-label="Job workflow stages">
          {job.stages.map((stage) => <div key={stage.key} className={`agent-track-progress-step state-${stage.state}`} role="listitem"><span className="agent-track-progress-icon">{stageIcon(stage)}</span><strong>{({intake:"Request Received",planning:"Plan Job",dispatch:"Assign Technician",communication:"Communicate",technician_work:"Track Work",sentiment:"Check Customer Mood",closure:"Close Job"} as Record<string,string>)[stage.key] || stage.label}</strong></div>)}
        </div>

        <section className="agent-track-event-panel">
          <div className="agent-track-event-panel-head">
            <h2>What Happened in This Job</h2>
            <div className="agent-track-log-toggle" role="group" aria-label="Event log detail level">
              <button className={!detailed ? "is-active" : ""} onClick={() => detailed && toggleDetailedLog()}>Simple View</button>
              <button className={detailed ? "is-active" : ""} onClick={() => !detailed && toggleDetailedLog()}>Detailed Log</button>
            </div>
          </div>
          {job.events.length ? <ol className="agent-track-event-list">
            {job.events.map((event, index) => (
              <li className={`agent-track-event kind-${event.kind} ${expandedEvents.includes(index) ? "is-expanded" : ""}`} key={`${event.kind}-${event.timestamp}-${index}`}>
                <button className="agent-track-event-trigger" onClick={() => toggleEvent(index)} aria-expanded={expandedEvents.includes(index)}>
                  <span className="agent-track-event-dot" />
                  <time>{formatTimestamp(event.timestamp)}</time>
                  <span className="agent-track-event-icon">{event.kind === "communication" ? <MessageCircle size={18} /> : event.kind === "work" ? <Activity size={18} /> : <CircleDashed size={18} />}</span>
                  <span className="agent-track-event-agent">{event.agent}</span>
                  <span className="agent-track-event-copy"><strong>{event.title}</strong>{!detailed ? <small>{event.detail}</small> : null}</span>
                  <span className="agent-track-event-state"><Check size={14} /> {event.kind === "communication" ? "Action Taken" : "Done"}</span>
                  <ChevronDown className={expandedEvents.includes(index) ? "is-open" : ""} size={17} />
                </button>
                {expandedEvents.includes(index) ? (
                  <div className="agent-track-event-expanded">
                    <div className="agent-track-event-detail"><strong>{event.agent} — {detailed ? "Recorded execution details" : "Event details"}</strong><p>{event.detail}</p></div>
                    {detailed ? <div className="agent-track-data-grid">
                      <div className="agent-track-data-card"><h3>Recorded input</h3>{event.input && Object.keys(event.input).length ? <dl>{Object.entries(event.input).map(([key, value]) => <div key={key}><dt>{key.replace(/_/g, " ")}</dt><dd>{typeof value === "string" ? value : value == null ? "Not recorded" : JSON.stringify(value, null, 2)}</dd></div>)}</dl> : <p>No input snapshot was persisted for this event.</p>}</div>
                      <div className="agent-track-data-card"><h3>Recorded output / outcome</h3>{event.output && Object.keys(event.output).length ? <dl>{Object.entries(event.output).map(([key, value]) => <div key={key}><dt>{key.replace(/_/g, " ")}</dt><dd>{typeof value === "string" ? value : value == null ? "Not recorded" : JSON.stringify(value, null, 2)}</dd></div>)}</dl> : <p>No output snapshot was persisted for this event.</p>}</div>
                    </div> : null}
                  </div>
                ) : null}
              </li>
            ))}
          </ol> : <div className="agent-track-empty agent-track-no-events"><Workflow size={25} /><strong>No workflow events recorded yet</strong><span>Events will appear here as this job moves through its stages.</span></div>}
        </section>
        </div>
      </section>
  );
}

export default function AgentTrackingPage() {
  const [data, setData] = useState<AgentTrackingResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [search, setSearch] = useState("");
  const [selectedJob, setSelectedJob] = useState<AgentTrackingJobDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailedLog, setDetailedLog] = useState(false);

  const openJob = useCallback(async (job: AgentTrackedJob) => {
    setSelectedJob(null);
    setDetailError("");
    setDetailLoading(true);
    try {
      setSelectedJob(await getAgentTrackingJob(job.job_id));
    } catch (requestError) {
      const response = (requestError as { response?: { status?: number; data?: { detail?: string } } }).response;
      const status = response?.status;
      if (status === 404) {
        const detail = response?.data?.detail;
        setDetailError(detail === "Job not found."
          ? "This job is no longer available in your organization. Refresh the workflow list and try again."
          : "The running backend does not have the job detail route loaded. Restart it with the latest code, then try again.");
      } else if (status === 401) {
        setDetailError("Your session has expired. Sign in again to view this job's progress.");
      } else if (status === 403) {
        setDetailError("Your account does not have permission to view this job.");
      } else if (status && status >= 500) {
        setDetailError("The backend failed while loading this job's details. Check the backend logs for the API error.");
      } else {
        setDetailError("Could not connect to the job detail API. Check the backend connection and try again.");
      }
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    try {
      const result = await getAgentTracking();
      setData(result);
      setError("");
    } catch (requestError) {
      const status = (requestError as { response?: { status?: number } })
        .response?.status;
      if (status === 404) {
        setError("Agent Tracking API was not found. Restart the backend so it loads the Agent Tracking route.");
      } else if (status === 401) {
        setError("Your session has expired. Sign in again to load agent tracking.");
      } else if (status === 403) {
        setError("Your account does not have access to Agent Tracking.");
      } else if (status && status >= 500) {
        setError("The Agent Tracking API returned a server error. Check the backend logs for the request failure.");
      } else {
        setError("Agent tracking could not connect to the API. Check the backend connection and try again.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(true), 20_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const filteredJobs = useMemo(() => {
    const query = search.trim().toLowerCase();
    return (data?.jobs ?? []).filter((job) => {
      const matchesFilter =
        filter === "all" ||
        (filter === "active" && !isTerminal(job) && !isFailed(job)) ||
        (filter === "complete" && isTerminal(job) && !isFailed(job)) ||
        (filter === "failed" && isFailed(job));
      const matchesSearch = !query || [
        String(job.job_number),
        job.service_type,
        job.status,
        job.technician_name ?? "",
        job.customer_name ?? "",
        job.location ?? "",
        job.current_agent,
      ].some((value) => value.toLowerCase().includes(query));
      return matchesFilter && matchesSearch;
    });
  }, [data?.jobs, filter, search]);

  return (
    <main className="agent-tracking-page">
      {selectedJob ? (
        <JobProgressDetail job={selectedJob} detailed={detailedLog} onToggleDetail={() => setDetailedLog((value) => !value)} onClose={() => setSelectedJob(null)} />
      ) : detailLoading ? (
        <div className="agent-track-page-message"><span className="agent-track-loader" />Loading job progress…</div>
      ) : detailError ? (
        <div className="agent-track-page-message is-error"><AlertCircle size={20} />{detailError}<button onClick={() => setDetailError("")}>Back to workflows</button></div>
      ) : (
      <>
      <header className="agent-track-hero">
        <div className="agent-track-hero-mark"><Workflow size={24} /></div>
        <div>
          <h1>Agent Tracking</h1>
          <p>Track AI agent execution for all jobs and view real-time progress.</p>
        </div>
        <div className="agent-track-date"><CalendarDays size={17}/>Today, {new Date().toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</div>
        <button className="agent-track-refresh" onClick={() => void load(true)} disabled={refreshing} aria-label="Refresh tracking"><RefreshCw size={17} className={refreshing ? "is-spinning" : ""} /></button>
      </header>

      <section className="agent-track-board">
        <div className="agent-track-toolbar">
        <div className="agent-track-filters" role="tablist" aria-label="Filter jobs">
          {FILTERS.map((option) => <button key={option.key} role="tab" aria-selected={filter === option.key} className={filter === option.key ? "is-selected" : ""} onClick={() => setFilter(option.key)}>
            {option.key === "all" ? <Workflow size={19}/> : option.key === "active" ? <Play size={19}/> : option.key === "complete" ? <CircleCheck size={19}/> : <CircleX size={19}/>}{option.label}{option.key === "all" ? ` (${data?.total ?? 0})` : option.key === "active" ? ` (${(data?.jobs ?? []).filter((job) => !isTerminal(job) && !isFailed(job)).length})` : option.key === "complete" ? ` (${(data?.jobs ?? []).filter((job) => isTerminal(job) && !isFailed(job)).length})` : ` (${(data?.jobs ?? []).filter(isFailed).length})`}
          </button>)}
        </div>
          <label className="agent-track-search">
            <Search size={17} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search by Job ID, customer, or location..." />
          </label>
        </div>

        {loading ? (
          <div className="agent-track-message"><span className="agent-track-loader" />Loading job workflows…</div>
        ) : error ? (
          <div className="agent-track-message is-error"><AlertCircle size={20} />{error}<button onClick={() => void load()}>Try again</button></div>
        ) : filteredJobs.length ? (
          <div className="agent-track-table-wrap"><table className="agent-track-table"><thead><tr><th>Job ID</th><th>Customer</th><th>Job Type</th><th>Agent Pipeline (7 Agents)</th><th>Progress</th><th>Status</th><th>Last Update</th><th>Action</th></tr></thead><tbody>
            {filteredJobs.map((job) => { const progress = progressFor(job); return <tr key={job.job_id}>
              <td><strong className="agent-track-table-id">#{job.job_number}</strong></td>
              <td><div className="agent-track-customer"><span className="agent-track-customer-avatar"><UserRound size={17}/></span><span><strong>{job.customer_name || "Customer"}</strong><small>{job.location || "Location not recorded"}</small></span></div></td>
              <td><span className="agent-track-service"><Wrench size={14}/>{job.service_type || "Service"}</span></td>
              <td><div className="agent-track-pipeline">{job.stages.slice(0, 7).map((stage, index) => <div className={`agent-track-pipeline-step state-${stage.state}`} key={stage.key}><span>{stageIcon(stage)}</span><small>{({intake:"Intake",planning:"Planning",dispatch:"Dispatcher",communication:"Comm.",technician_work:"Monitoring",sentiment:"Sentiment",closure:"Closure"} as Record<string,string>)[stage.key] || stage.label}</small>{index < Math.min(job.stages.length, 7) - 1 ? <i/> : null}</div>)}</div></td>
              <td><div className="agent-track-progress"><strong>{progress}%</strong><span><i style={{width: `${progress}%`}}/></span></div></td>
              <td><span className={`agent-track-table-status ${isFailed(job) ? "failed" : isTerminal(job) ? "completed" : "running"}`}>{isFailed(job) ? "Failed" : isTerminal(job) ? "Completed" : "Running"}</span></td>
              <td className="agent-track-update">{formatTimestamp(job.updated_at)}</td><td><button className="agent-track-view" onClick={() => void openJob(job)}>View Tracking <span>→</span></button></td>
            </tr>; })}
          </tbody></table></div>
        ) : (
          <div className="agent-track-empty">
            <Workflow size={28} />
            <strong>No matching workflows</strong>
            <span>Try another filter or search term.</span>
          </div>
        )}
      </section>
      </>
      )}
    </main>
  );
}
