import React, { useState, useEffect, useMemo, useCallback, useRef } from "react";
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
} from "@tanstack/react-table";
import {
  Loader2,
  Eye,
} from "lucide-react";

import { getDispatchQueue } from "../../services/dispatchQueueService";
import type { DispatchQueueJob } from "../../types/dispatchQueue";

/* ─── Helpers ──────────────────────────────────────────────────────────────── */

const getPriorityStyle = (priority: string): React.CSSProperties => {
  const p = (priority || "").toUpperCase();
  const theme = priorityColors[p] || priorityColors.DEFAULT;
  return {
    ...tableStyles.priorityBadge,
    backgroundColor: theme.bg,
    color: theme.color,
  };
};

/* ─── Skeleton ─────────────────────────────────────────────────────────────── */

const SkeletonRows = () => {
  const skeletonBarBase = {
    height: "12px",
    borderRadius: "6px",
    background: "linear-gradient(90deg, #E5E7EB 25%, #F3F4F6 50%, #E5E7EB 75%)",
    backgroundSize: "200% 100%",
    animation: "shimmer 1.5s ease-in-out infinite",
  };
  return (
    <>
      {Array.from({ length: 7 }).map((_, i) => (
        <tr key={i}>
          <td style={{ ...tableStyles.td, padding: "4px 8px" }}>
            <div style={{ ...skeletonBarBase, width: "60px" }} />
          </td>
          <td style={{ ...tableStyles.td, padding: "4px 8px" }}>
            <div style={{ ...skeletonBarBase, width: "100px" }} />
          </td>
          <td style={{ ...tableStyles.td, padding: "4px 8px" }}>
            <div style={{ ...skeletonBarBase, width: "140px" }} />
          </td>
          <td style={{ ...tableStyles.td, padding: "4px 8px" }}>
            <div style={{ ...skeletonBarBase, width: "80px" }} />
          </td>
          <td style={{ ...tableStyles.td, padding: "4px 8px" }}>
            <div style={{ ...skeletonBarBase, width: "50px" }} />
          </td>
          <td style={{ ...tableStyles.td, padding: "4px 8px" }}>
            <div style={{ ...skeletonBarBase, width: "50px" }} />
          </td>
          <td style={{ ...tableStyles.td, padding: "4px 8px" }}>
            <div style={{ ...skeletonBarBase, width: "22px" }} />
          </td>
        </tr>
      ))}
    </>
  );
};

/* ─── Main Component ───────────────────────────────────────────────────────── */

interface DispatchQueueTableProps {
  onCountChange?: (count: number) => void;
  jobStatus?: "EN_ROUTE" | "IN_PROGRESS";
  onViewJob?: (job: any) => void;
  visibleJobIds?: Set<string> | null;
}

const DispatchQueueTable = ({
  onCountChange,
  jobStatus = "EN_ROUTE",
  onViewJob,
  visibleJobIds,
}: DispatchQueueTableProps) => {
  // Data
  const [jobs, setJobs] = useState<DispatchQueueJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Auto-refresh interval
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Hover states
  const [hoveredRow, setHoveredRow] = useState<string | null>(null);

  // ── Fetch ─────────────────────────────────────────────────────────────────

  const fetchQueue = useCallback(
    async (isRefresh = false) => {
      try {
        if (!isRefresh) {
          setLoading(true);
        }
        setError(null);

        const response = await getDispatchQueue({
  status: jobStatus,
  limit: 100,
});

const stageJobs = (response.data || []).filter((job) => {
  const normalizedStatus = (job.status || "")
    .trim()
    .toUpperCase()
    .replace(/[\s-]+/g, "_");

  if (jobStatus === "IN_PROGRESS") {
    return ["IN_PROGRESS", "ON_SITE"].includes(normalizedStatus);
  }

  return normalizedStatus === "EN_ROUTE";
});

setJobs(stageJobs);
onCountChange?.(stageJobs.length);
      } catch (err) {
        console.error("Dispatch queue fetch error:", err);
        setError("Failed to load dispatch queue. Please try again.");
      } finally {
        setLoading(false);
      }
    },
    [onCountChange, jobStatus]
  );

  // Initial fetch + filter changes
  useEffect(() => {
    fetchQueue(false);
  }, [fetchQueue]);

  // Auto-refresh every 30s
  useEffect(() => {
    intervalRef.current = setInterval(() => {
      fetchQueue(true);
    }, 30000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [fetchQueue]);

  // ── Filtered data (client-side text search) ───────────────────────────────

  const filteredJobs = useMemo(
    () =>
      visibleJobIds == null
        ? jobs
        : jobs.filter((job) => visibleJobIds.has(String(job.job_id))),
    [jobs, visibleJobIds],
  );

  // ── Column Definitions ────────────────────────────────────────────────────

  const columns = useMemo<ColumnDef<DispatchQueueJob>[]>(
    () => [
      {
        accessorKey: "job_id",
        header: "Job ID",
        size: 120,
        cell: ({ row }) => <span style={tableStyles.jobIdText}>#{row.original.job_id}</span>,
      },
      {
        accessorKey: "customer",
        header: "Customer Name",
        size: 170,
        cell: ({ row }) => (
          <div style={tableStyles.customerName}>{row.original.customer}</div>
        ),
      },
      {
        accessorKey: "title",
        header: "Service Name",
        size: 180,
        cell: ({ row }) => row.original.title || "—",
      },
      {
        id: "technician",
        header: "Technician Name",
        size: 160,
        accessorFn: (row) => row.technician?.name || "",
        cell: ({ row }) => {
          return row.original.technician?.name || "Not Assigned";
        },
      },
      {
        accessorKey: "priority",
        header: "Priority",
        size: 90,
        cell: ({ row }) => (
          <span style={getPriorityStyle(row.original.priority)}>
            {row.original.priority || "—"}
          </span>
        ),
      },
      {
        accessorKey: "status",
        header: "Status",
        size: 110,
        cell: ({ row }) => (
          <span style={{ fontWeight: 600 }}>
            {(row.original.status || "UNKNOWN").replace(/[\s_-]+/g, " ").toUpperCase()}
          </span>
        ),
      },
      {
  id: "actions",
  header: "Action",
  size: 80,
  enableSorting: false,
  cell: ({ row }) => (
    <button
      type="button"
      className="icon-action-btn-style"
      title="View job details and status history"
      aria-label={`View job ${row.original.job_id} details`}
      onClick={(event) => {
        event.stopPropagation();

        onViewJob?.({
          id: row.original.job_id,
          customer_name: row.original.customer,
          issue_description: row.original.title,
          service_type: row.original.title,
          location: row.original.location,
          priority: row.original.priority,
          status: row.original.status,
          technician_name: row.original.technician?.name || null,
        });
      }}
    >
      <Eye size={16} />
    </button>
  ),
},
    ],
    [onViewJob]
  );

  // ── Table Instance ────────────────────────────────────────────────────────

  const table = useReactTable({
    data: filteredJobs,
    columns,
    getCoreRowModel: getCoreRowModel(),
  });

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <section style={tableStyles.section} aria-label="En Route Jobs">
      <style>
        {`
          @keyframes shimmer {
            0% { background-position: 200% 0; }
            100% { background-position: -200% 0; }
          }
          @keyframes toastSlideUp {
            from { opacity: 0; transform: translateX(-50%) translateY(12px); }
            to   { opacity: 1; transform: translateX(-50%) translateY(0); }
          }
          .animate-spin {
            animation: spin-anim 1s linear infinite;
          }
          @keyframes spin-anim {
            to { transform: rotate(360deg); }
          }
        `}
      </style>

      {/* Table Content */}
      <div style={{ ...tableStyles.tableWrap, flex: 1, minHeight: 0 }}>
            <table style={tableStyles.table} role="table">
              <thead>
                {table.getHeaderGroups().map((headerGroup) => (
                  <tr key={headerGroup.id}>
                    {headerGroup.headers.map((header) => {
                      return (
                        <th
                          key={header.id}
                          style={{
                            ...tableStyles.th,
                            width: header.getSize(),
                            padding: "4px 8px",
                            fontSize: "9.5px",
                            letterSpacing: "0.05em",
                            cursor: "default",
                            backgroundColor: "#F6FAF8",
                          }}
                        >
                          {flexRender(header.column.columnDef.header, header.getContext())}
                        </th>
                      );
                    })}
                  </tr>
                ))}
              </thead>
              <tbody>
                {error ? (
                  <tr><td colSpan={columns.length} role="alert" style={{ ...tableStyles.td, padding: "8px", color: "#B91C1C" }}>{error}</td></tr>
                ) : loading ? (
                  <SkeletonRows />
                ) : table.getRowModel().rows.length === 0 ? (
                  <tr>
                    <td colSpan={columns.length} style={{ ...tableStyles.td, padding: "8px", textAlign: "center", color: "#64748B" }}>
                      {jobStatus === "EN_ROUTE" ? "No en route jobs found." : "No in-progress jobs found."}
                    </td>
                  </tr>
                ) : (
                  table.getRowModel().rows.map((row) => {
                    const isRowHovered = hoveredRow === row.id;
                    return (
                      <tr
                        key={row.id}
                        onMouseEnter={() => setHoveredRow(row.id)}
                        onMouseLeave={() => setHoveredRow(null)}
                      >
                        {row.getVisibleCells().map((cell) => (
                          <td
                            key={cell.id}
                            style={{
                              ...tableStyles.td,
                              padding: "4px 8px",
                              backgroundColor: isRowHovered ? "#F4FAF6" : "transparent",
                            }}
                          >
                            {flexRender(
                              cell.column.columnDef.cell,
                              cell.getContext()
                            )}
                          </td>
                        ))}
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
      </div>
      <div
        style={{
          padding: "8px",
          borderTop: "1px solid #E3ECE7",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          color: "#6B7280",
          fontSize: "11.5px",
        }}
      >
        <span>Page 1 of 1 · {filteredJobs.length} results</span>
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="planning-page-btn-style" disabled>‹ Prev</button>
          <button type="button" className="planning-page-btn-style" disabled>Next ›</button>
        </div>
      </div>
    </section>
  );
};

/* ─── Styles & Design Tokens ────────────────────────────────────────── */

const tableStyles = {
  section: {
    fontFamily: "'Inter', sans-serif",
    width: "100%",
    minHeight: "420px",
    boxSizing: "border-box",
    background: "#FFFFFF",
    border: "1px solid #E3ECE7",
    borderRadius: "12px",
    boxShadow: "0 1px 4px rgba(47, 79, 62, 0.07)",
    display: "flex",
    flexDirection: "column",
    overflow: "hidden",
  } as React.CSSProperties,
  
  filterBar: {
    display: "flex",
    alignItems: "center",
    gap: "10px",
    padding: "12px 16px",
    background: "#F8FBF9",
    borderBottom: "1px solid #E3ECE7",
    flexWrap: "wrap",
  } as React.CSSProperties,

  filterSelect: {
    padding: "6px 10px",
    borderRadius: "8px",
    border: "1.5px solid #E3ECE7",
    fontSize: "12px",
    fontWeight: 600,
    color: "#1F2933",
    background: "#FFFFFF",
    outline: "none",
    minWidth: "130px",
    fontFamily: "'Inter', sans-serif",
    cursor: "pointer",
    transition: "border-color 0.2s, box-shadow 0.2s",
  } as React.CSSProperties,
  
  filterSearchWrap: {
    position: "relative",
    flex: 1,
    minWidth: "180px",
  } as React.CSSProperties,

  filterSearchIcon: {
    position: "absolute",
    left: "10px",
    top: "50%",
    transform: "translateY(-50%)",
    color: "#9CA3AF",
    pointerEvents: "none",
  } as React.CSSProperties,

  filterSearch: {
    width: "100%",
    padding: "6px 10px 6px 32px",
    borderRadius: "8px",
    border: "1.5px solid #E3ECE7",
    fontSize: "12px",
    color: "#1F2933",
    background: "#FFFFFF",
    outline: "none",
    transition: "border-color 0.2s, box-shadow 0.2s",
    fontFamily: "'Inter', sans-serif",
    boxSizing: "border-box",
  } as React.CSSProperties,

  filterCount: {
    fontSize: "11px",
    fontWeight: 600,
    color: "#6B7280",
    whiteSpace: "nowrap",
    marginLeft: "auto",
    display: "flex",
    alignItems: "center",
    gap: "6px",
  } as React.CSSProperties,

  refreshIndicator: {
    display: "inline-flex",
    alignItems: "center",
    gap: "5px",
    fontSize: "10px",
    fontWeight: 600,
    color: "#7AAE8A",
    padding: "3px 8px",
    background: "#EAF4EE",
    borderRadius: "12px",
    marginLeft: "8px",
  } as React.CSSProperties,

  tableWrap: {
    overflowX: "auto",
  } as React.CSSProperties,

  table: {
    width: "100%",
    borderCollapse: "collapse",
  } as React.CSSProperties,

  th: {
    padding: "8px 12px",
    textAlign: "left",
    fontSize: "10px",
    fontWeight: 700,
    color: "#6B7280",
    textTransform: "uppercase",
    letterSpacing: "0.06em",
    borderBottom: "1px solid #E3ECE7",
    whiteSpace: "nowrap",
    cursor: "pointer",
    userSelect: "none",
    transition: "background 0.15s",
  } as React.CSSProperties,

  thInner: {
    display: "flex",
    alignItems: "center",
    gap: "4px",
  } as React.CSSProperties,

  sortIcon: {
    color: "#9CA3AF",
    flexShrink: 0,
    transition: "color 0.15s, transform 0.2s",
  } as React.CSSProperties,

  td: {
    padding: "10px 12px",
    fontSize: "12px",
    color: "#1F2933",
    borderBottom: "1px solid #F0F6F2",
    verticalAlign: "middle",
    transition: "background 0.12s",
  } as React.CSSProperties,

  jobIdCell: {
    display: "flex",
    alignItems: "center",
    gap: "6px",
  } as React.CSSProperties,

  jobIdText: {
    fontFamily: "'SF Mono', 'Fira Code', 'Cascadia Code', monospace",
    fontSize: "11.5px",
    fontWeight: 700,
    color: "#5C9470",
    letterSpacing: "-0.02em",
  } as React.CSSProperties,

  copyBtn: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "22px",
    height: "22px",
    border: "none",
    borderRadius: "4px",
    cursor: "pointer",
    transition: "all 0.15s",
    flexShrink: 0,
  } as React.CSSProperties,

  customerName: {
    fontWeight: 600,
    color: "#2F4F3E",
    fontSize: "12px",
  } as React.CSSProperties,

  customerTitle: {
    fontSize: "10.5px",
    color: "#6B7280",
    marginTop: "1px",
  } as React.CSSProperties,

  locationCell: {
    display: "flex",
    alignItems: "center",
    gap: "5px",
  } as React.CSSProperties,

  locationText: {
    fontSize: "12px",
    color: "#1F2933",
  } as React.CSSProperties,

  mapLink: {
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    width: "20px",
    height: "20px",
    borderRadius: "4px",
    transition: "all 0.15s",
    flexShrink: 0,
  } as React.CSSProperties,

  techCell: {
    display: "flex",
    alignItems: "center",
    gap: "8px",
  } as React.CSSProperties,

  techAvatar: {
    width: "28px",
    height: "28px",
    borderRadius: "50%",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    fontWeight: 700,
    color: "#FFFFFF",
    fontSize: "11px",
    flexShrink: 0,
    textTransform: "uppercase",
  } as React.CSSProperties,

  techInfo: {
    display: "flex",
    flexDirection: "column",
  } as React.CSSProperties,

  techName: {
    fontWeight: 600,
    fontSize: "12px",
    color: "#1F2933",
  } as React.CSSProperties,

  techStatusText: {
    fontSize: "10px",
    color: "#6B7280",
    textTransform: "capitalize",
  } as React.CSSProperties,

  unassigned: {
    fontSize: "11.5px",
    color: "#9CA3AF",
    fontStyle: "italic",
  } as React.CSSProperties,

  priorityBadge: {
    fontSize: "10px",
    fontWeight: 700,
    padding: "3px 8px",
    borderRadius: "20px",
    textTransform: "uppercase",
    letterSpacing: "0.03em",
    display: "inline-block",
  } as React.CSSProperties,
};

const avatarColors: Record<string, string> = {
  available: "#10B981",
  assigned: "#3B82F6",
  busy: "#F59E0B",
  offline: "#9CA3AF",
  unassigned: "#E5E7EB",
};

const priorityColors: Record<string, { bg: string; color: string }> = {
  CRITICAL: { bg: "#FAE5E5", color: "#7A2020" },
  HIGH: { bg: "#FEF0D6", color: "#7A5120" },
  MEDIUM: { bg: "#FDFBDC", color: "#706020" },
  LOW: { bg: "#DDEEE5", color: "#2F4F3E" },
  DEFAULT: { bg: "#F0F4F2", color: "#6B7280" },
};

export default DispatchQueueTable;
