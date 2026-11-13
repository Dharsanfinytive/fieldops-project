import {
  CalendarDays,
  Check,
  Clock,
  MapPin,
  Navigation,
  Phone,
  User,
  Wrench,
  X,
  AlertTriangle,
} from "lucide-react";
import { formatAcceptanceCountdown } from "../../hooks/useAcceptanceCountdown";
import "./TechnicianJobAssignmentPopup.css";

interface TechnicianJob {
  id: number | string;
  customer_name?: string | null;
  contact_number?: string | null;
  priority?: string | null;
  preferred_service_date?: string | null;
  preferred_service_time?: string | null;
  preferred_visit_time?: string | null;
  site_address?: string | null;
  location?: string | null;
  service_type?: string | null;
  issue_description?: string | null;
}

interface TechnicianJobAssignmentPopupProps {
  job: TechnicianJob;
  countdownSeconds: number | null;
  distanceDisplay: string;
  loading: boolean;
  rejecting?: boolean;
  rejectReason?: string;
  onClose: () => void;
  onAccept: () => void;
  onReject: () => void;
  onRejectReasonChange?: (reason: string) => void;
  onConfirmReject?: () => void;
  onCancelReject?: () => void;
}

const formatDateIndian = (dateStr?: string | null): string => {
  if (!dateStr) return "N/A";

  const value = String(dateStr).trim();

  // ISO date: YYYY-MM-DD
  const isoMatch = value.match(/^(\d{4})-(\d{2})-(\d{2})/);

  if (isoMatch) {
    return `${isoMatch[3]}/${isoMatch[2]}/${isoMatch[1]}`;
  }

  // Already Indian date: DD/MM/YYYY
  const indianMatch = value.match(/^(\d{2})\/(\d{2})\/(\d{4})$/);

  if (indianMatch) {
    return value;
  }

  const parsed = new Date(value);

  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat("en-IN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    timeZone: "Asia/Kolkata",
  }).format(parsed);
};

const formatTime = (timeStr?: string | null): string => {
  if (!timeStr) return "N/A";

  const value = String(timeStr).trim();

  // Already in 12-hour format.
  const ampmMatch = value.match(
    /^(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)$/i
  );

  if (ampmMatch) {
    const hours = Number(ampmMatch[1]);

    if (hours >= 1 && hours <= 12) {
      return `${String(hours).padStart(2, "0")}:${ampmMatch[2]} ${ampmMatch[3].toUpperCase()}`;
    }
  }

  // Convert 24-hour format to 12-hour format.
  const timeMatch = value.match(
    /^(\d{1,2}):(\d{2})(?::\d{2})?$/
  );

  if (timeMatch) {
    const hours = Number(timeMatch[1]);
    const minutes = timeMatch[2];

    if (hours >= 0 && hours <= 23) {
      const ampm = hours >= 12 ? "PM" : "AM";
      const hours12 = hours % 12 || 12;

      return `${String(hours12).padStart(2, "0")}:${minutes} ${ampm}`;
    }
  }

  return value;
};

const getPriorityTheme = (p?: string | null) => {
  const norm = String(p || "MEDIUM").toUpperCase().trim();
  switch (norm) {
    case "URGENT":
    case "CRITICAL":
      return {
        label: "EMERGENCY / CRITICAL",
        color: "#DC2626",
        bg: "#FEF2F2",
        border: "#FCA5A5",
        iconBg: "#FEE2E2",
      };
    case "HIGH":
      return {
        label: "HIGH PRIORITY",
        color: "#D97706",
        bg: "#FFFBEB",
        border: "#FDE68A",
        iconBg: "#FEF3C7",
      };
    case "LOW":
      return {
        label: "LOW PRIORITY",
        color: "#059669",
        bg: "#ECFDF5",
        border: "#A7F3D0",
        iconBg: "#D1FAE5",
      };
    case "MEDIUM":
    default:
      return {
        label: "STANDARD PRIORITY",
        color: "#2563EB",
        bg: "#EFF6FF",
        border: "#BFDBFE",
        iconBg: "#DBEAFE",
      };
  }
};

export default function TechnicianJobAssignmentPopup({
  job,
  countdownSeconds,
  distanceDisplay,
  loading,
  rejecting = false,
  rejectReason = "",
  onClose,
  onAccept,
  onReject,
  onRejectReasonChange,
  onConfirmReject,
  onCancelReject,
}: TechnicianJobAssignmentPopupProps) {
  const expired = countdownSeconds !== null && countdownSeconds <= 0;
  const priorityTheme = getPriorityTheme(job.priority);

  return (
    <div className="technician-job-assignment-modal">
      <div
        className="technician-job-assignment-backdrop"
        onClick={onClose}
      />
      <div
        className="technician-job-assignment-card"
        role="dialog"
        aria-modal="true"
        aria-labelledby="technician-job-assignment-title"
      >
        <header className="technician-job-assignment-header">
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "4px" }}>
              <span className="technician-job-assignment-id">
                Job ID: #{job.id}
              </span>
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "4px",
                  padding: "3px 9px",
                  borderRadius: "12px",
                  fontSize: "11px",
                  fontWeight: 800,
                  letterSpacing: "0.5px",
                  background: priorityTheme.bg,
                  color: priorityTheme.color,
                  border: `1px solid ${priorityTheme.border}`,
                }}
              >
                <AlertTriangle size={12} />
                {priorityTheme.label}
              </span>
            </div>
            <h3
              className="technician-job-assignment-title"
              id="technician-job-assignment-title"
            >
              Job Assigned
            </h3>
            <p className="technician-job-assignment-subtitle">
              You have been assigned a new service job.
            </p>
          </div>
          <button
            className="technician-job-assignment-close"
            type="button"
            aria-label="Close assigned job popup"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </header>

        {rejecting ? (
          <div className="technician-job-assignment-rejection">
            <label htmlFor="technician-assignment-reject-reason">
              Why are you rejecting this job?
            </label>
            <textarea
              id="technician-assignment-reject-reason"
              value={rejectReason}
              onChange={(event) => onRejectReasonChange?.(event.target.value)}
              placeholder="Explain why you're rejecting this job..."
              rows={4}
            />
          </div>
        ) : (
          <>
        <section
          className="technician-job-assignment-summary"
          aria-label="Offer time and customer distance"
        >
          <div className="technician-job-assignment-summary-cell">
            <span className="technician-job-assignment-icon">
              <Clock size={19} />
            </span>
            <div className="technician-job-assignment-summary-content">
              <span className="technician-job-assignment-eyebrow">
                TIME REMAINING
              </span>
              <div
                className="technician-job-assignment-summary-value"
                data-testid="assignment-countdown"
              >
                {countdownSeconds === null
                  ? "Unavailable"
                  : expired
                    ? "EXPIRED"
                    : formatAcceptanceCountdown(countdownSeconds)}
              </div>
              {countdownSeconds !== null && !expired && (
                <span className="technician-job-assignment-summary-note">
                  REMAINING
                </span>
              )}
            </div>
          </div>
          <div className="technician-job-assignment-summary-cell">
            <span className="technician-job-assignment-icon">
              <Navigation size={19} />
            </span>
            <div className="technician-job-assignment-summary-content">
              <span className="technician-job-assignment-eyebrow">
                DISTANCE TO CUSTOMER
              </span>
              <div
                className="technician-job-assignment-summary-value"
                data-testid="assignment-top-distance"
                aria-live="polite"
              >
                {distanceDisplay}
              </div>
            </div>
          </div>
        </section>

        <section className="technician-job-assignment-columns">

          {/* LEFT: CUSTOMER DETAILS */}
          <article className="technician-job-assignment-panel">

            <div className="technician-job-assignment-panel-heading is-customer">
              <User size={22} />
              <h4>Customer Details</h4>
            </div>

            <div className="technician-job-assignment-panel-body">

              {/* Customer Name */}
              <div className="technician-job-assignment-detail-row">
                <span className="technician-job-assignment-info-icon is-green">
                  <User size={19} />
                </span>

                <div className="technician-job-assignment-info-content">
                  <span className="technician-job-assignment-eyebrow">
                    NAME
                  </span>

                  <div className="technician-job-assignment-info-value">
                    {job.customer_name || "N/A"}
                  </div>
                </div>
              </div>

              {/* Contact */}
              <div className="technician-job-assignment-detail-row">
                <span className="technician-job-assignment-info-icon is-blue">
                  <Phone size={19} />
                </span>

                <div className="technician-job-assignment-info-content">
                  <span className="technician-job-assignment-eyebrow">
                    CONTACT
                  </span>

                  <div className="technician-job-assignment-info-value">
                    {job.contact_number || "N/A"}
                  </div>
                </div>
              </div>

              {/* Location */}
              <div className="technician-job-assignment-detail-row is-location">
                <span className="technician-job-assignment-info-icon is-green">
                  <MapPin size={19} />
                </span>

                <div className="technician-job-assignment-info-content">
                  <span className="technician-job-assignment-eyebrow">
                    LOCATION
                  </span>

                  <div className="technician-job-assignment-info-value">
                    {job.site_address || job.location || "N/A"}
                  </div>
                </div>
              </div>

            </div>
          </article>

          {/* RIGHT: SERVICE DETAILS */}
          <article className="technician-job-assignment-panel">

            <div className="technician-job-assignment-panel-heading is-service">
              <Wrench size={22} />
              <h4>Service Details</h4>
            </div>

            <div className="technician-job-assignment-panel-body">

              {/* Service Type */}
              <div className="technician-job-assignment-detail-row">
                <span className="technician-job-assignment-info-icon is-blue">
                  <Wrench size={19} />
                </span>

                <div className="technician-job-assignment-info-content">
                  <span className="technician-job-assignment-eyebrow">
                    SERVICE TYPE
                  </span>

                  <div className="technician-job-assignment-info-value">
                    {job.service_type || "Service Request"}
                  </div>
                </div>
              </div>

              {/* Job Title / Issue */}
              <div className="technician-job-assignment-detail-row">
                <span className="technician-job-assignment-info-icon is-blue">
                  <AlertTriangle size={19} />
                </span>

                <div className="technician-job-assignment-info-content">
                  <span className="technician-job-assignment-eyebrow">
                    TITLE / ISSUE
                  </span>

                  <div className="technician-job-assignment-info-value">
                    {job.issue_description || "No additional service details provided."}
                  </div>
                </div>
              </div>

              {/* Appointment Date and Time */}
              <div className="technician-job-assignment-date-time">

                <div className="technician-job-assignment-date-time-item">
                  <span className="technician-job-assignment-info-icon">
                    <CalendarDays size={19} />
                  </span>

                  <div className="technician-job-assignment-info-content">
                    <span className="technician-job-assignment-eyebrow">
                      APPOINTMENT DATE
                    </span>

                    <div className="technician-job-assignment-info-value">
                      {formatDateIndian(job.preferred_service_date)}
                    </div>
                  </div>
                </div>

                <div className="technician-job-assignment-date-time-item">
                  <span className="technician-job-assignment-info-icon is-blue">
                    <Clock size={19} />
                  </span>

                  <div className="technician-job-assignment-info-content">
                    <span className="technician-job-assignment-eyebrow">
                      PREFERRED TIME
                    </span>

                    <div className="technician-job-assignment-info-value">
                      {formatTime(
                        job.preferred_service_time || job.preferred_visit_time
                      )}
                    </div>
                  </div>
                </div>

              </div>

              {/* Priority */}
              <div
                className="technician-job-assignment-priority"
                style={{
                  borderColor: priorityTheme.border,
                  background: priorityTheme.bg,
                  color: priorityTheme.color,
                }}
              >
                <span
                  className="technician-job-assignment-info-icon"
                  style={{
                    background: priorityTheme.iconBg,
                    color: priorityTheme.color,
                  }}
                >
                  <AlertTriangle size={19} />
                </span>

                <div className="technician-job-assignment-info-content">
                  <span className="technician-job-assignment-eyebrow">
                    JOB PRIORITY
                  </span>

                  <div className="technician-job-assignment-info-value">
                    {job.priority
                      ? job.priority.toUpperCase()
                      : "MEDIUM"}
                  </div>
                </div>
              </div>

            </div>
          </article>

        </section>
          </>
        )}

        <div className="technician-job-assignment-actions">
          {rejecting ? (
            <>
              <button
                type="button"
                onClick={onCancelReject}
                disabled={loading}
                className="technician-job-assignment-action is-cancel"
              >
                Back
              </button>
              <button
                type="button"
                onClick={onConfirmReject}
                disabled={loading || rejectReason.trim().length < 10}
                className="technician-job-assignment-action is-reject"
              >
                {loading ? "Rejecting..." : "Confirm Reject"}
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                onClick={onAccept}
                disabled={loading || expired}
                className="technician-job-assignment-action is-accept"
              >
                <Check size={18} />
                {loading ? "Accepting..." : "Accept Job"}
              </button>
              <button
                type="button"
                onClick={onReject}
                disabled={loading}
                className="technician-job-assignment-action is-reject"
              >
                <X size={18} />
                Reject
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
