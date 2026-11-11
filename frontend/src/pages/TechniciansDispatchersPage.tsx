import React, { useEffect, useState } from "react";
import { Users } from "lucide-react";
import TechnicianListPage from "./TechDashboardPage";
import DispatcherPage from "./dispatcher/DispatcherPage";

export type StaffView = "technicians" | "dispatchers";

interface TechniciansDispatchersPageProps {
  activeView?: StaffView;
  allowDispatcherToggle?: boolean;
  onViewChange?: (view: StaffView) => void;
}

export default function TechniciansDispatchersPage({
  activeView: requestedView,
  allowDispatcherToggle = true,
  onViewChange,
}: TechniciansDispatchersPageProps) {
  const [activeView, setActiveView] = useState<StaffView>(
    requestedView ?? "technicians",
  );

  useEffect(() => {
    setActiveView(requestedView ?? "technicians");
  }, [requestedView]);

  const changeView = (view: StaffView) => {
    setActiveView(view);
    onViewChange?.(view);
  };

  const tabStyle = (selected: boolean): React.CSSProperties => ({
    display: "inline-flex",
    alignItems: "center",
    justifyContent: "center",
    gap: "5px",
    minHeight: "34px",
    padding: "0 11px",
    border: selected ? "1px solid #075a35" : "1px solid #dce6e1",
    borderRadius: "11px",
    background: selected
      ? "linear-gradient(145deg, #08683c, #034a2b)"
      : "#FFFFFF",
    color: selected ? "#FFFFFF" : "#344256",
    fontSize: "12px",
    fontWeight: 750,
    cursor: "pointer",
    whiteSpace: "nowrap",
    boxShadow: selected
      ? "0 5px 12px rgba(4, 81, 45, .16)"
      : "0 2px 6px rgba(23, 50, 39, .06)",
  });

  return (
    <div
      style={{
        height: "100%",
        minHeight: 0,
        display: "flex",
        flexDirection: "column",
        background: "#F3F8F5",
        overflow: "hidden",
        fontFamily: "'Inter', sans-serif",
      }}
    >
      <style>{`
        .staff-switch-heading {
          position: relative;
          z-index: 5;
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 12px;
          flex-wrap: wrap;
          min-height: 44px;
          padding: 5px clamp(14px, 1.5vw, 20px);
          border-bottom: 1px solid #DFEBE4;
          background: linear-gradient(105deg, #fff 0%, #fff 58%, #eff8f2 100%);
          box-sizing: border-box;
        }
        .staff-switch-body {
          flex: 1;
          min-height: 0;
          overflow: auto;
        }
        .staff-switch-body .tld-header-responsive > div:first-child {
          display: none !important;
        }
        .staff-switch-body .tld-header-responsive {
          justify-content: flex-end !important;
        }
        @media (max-width: 760px) {
          .staff-switch-heading { align-items: flex-start !important; }
          .staff-switch-buttons { width: 100%; }
          .staff-switch-buttons button { flex: 1; }
        }
      `}</style>

      <header className="staff-switch-heading">
        {allowDispatcherToggle && (
          <div
            className="staff-switch-buttons"
            role="tablist"
            aria-label="Choose staff type"
            style={{ display: "flex", alignItems: "center", gap: 10 }}
          >
            <button
              type="button"
              role="tab"
              aria-selected={activeView === "technicians"}
              style={tabStyle(activeView === "technicians")}
              onClick={() => changeView("technicians")}
            >
              <Users size={14} /> Technicians
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeView === "dispatchers"}
              style={tabStyle(activeView === "dispatchers")}
              onClick={() => changeView("dispatchers")}
            >
              <Users size={14} /> Dispatchers
            </button>
          </div>
        )}
      </header>

      <main className="staff-switch-body">
        {activeView === "technicians" ? (
          <TechnicianListPage />
        ) : (
          <DispatcherPage />
        )}
      </main>
    </div>
  );
}
