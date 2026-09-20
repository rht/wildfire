import { useState } from "react";
import {
  Routes,
  Route,
  NavLink,
  Navigate,
  Link,
  useLocation,
  useNavigate,
  useParams,
} from "react-router-dom";
import { Alert, Button, Typography, IconButton, Drawer } from "@mui/material";
import DashboardOutlined from "@ant-design/icons/DashboardOutlined";
import FireOutlined from "@ant-design/icons/FireOutlined";
import BankOutlined from "@ant-design/icons/BankOutlined";
import TeamOutlined from "@ant-design/icons/TeamOutlined";
import HistoryOutlined from "@ant-design/icons/HistoryOutlined";
import MenuOutlined from "@ant-design/icons/MenuOutlined";
import { ApprovalProvider } from "./state/approvals";
import { useDashboard } from "./state/useDashboard";
import { useTimeline } from "./state/useTimeline";
import TimelineControls from "./components/TimelineControls";
import { Overview, Incidents } from "./pages/Overview";
import Buildings from "./pages/Buildings";
import ActivityLog from "./pages/ActivityLog";
import { Calls, ResponsePlan, Evacuation, Resources } from "./pages/Operations";
import { Empty, MainCard, Status } from "./components/Common";
const nav = [
  ["/overview", "Overview", DashboardOutlined],
  ["/incidents", "Incidents", FireOutlined],
  ["/buildings", "Buildings & risk", BankOutlined],
  ["/resources", "Resources", TeamOutlined],
  ["/log", "Activity log", HistoryOutlined],
];
const tabs = [
  ["summary", "Summary"],
  ["calls", "Calls & follow-up"],
  ["plan", "Firefighter plan"],
  ["evacuation", "Evacuation"],
  ["resources", "Resources"],
  ["buildings", "Buildings & risk"],
  ["log", "Activity log"],
];
function IncidentPage({ incidents, demo }) {
  const { id, page = "summary" } = useParams(),
    incident = incidents.find((i) => i.id === id);
  if (!incident)
    return (
      <MainCard>
        <Empty title="Incident not available">
          Select an incident from the current data source.
        </Empty>
        <Button component={Link} to="/incidents">
          Browse incidents
        </Button>
      </MainCard>
    );
  return (
    <>
      <div className="incident-heading">
        <div>
          <div className="breadcrumb">
            <Link to="/incidents">Incidents</Link>
            <span>/</span>
            {incident.area || incident.id}
          </div>
          <Typography variant="h3">{incident.name}</Typography>
        </div>
        <Status value={incident.status} />
      </div>
      <nav className="incident-tabs" aria-label="Incident pages">
        {tabs.map(([path, label]) => (
          <NavLink
            key={path}
            to={`/incidents/${encodeURIComponent(id)}/${path}`}
          >
            {label}
          </NavLink>
        ))}
      </nav>
      {page === "summary" ? (
        <Overview incidents={incidents} incident={incident} />
      ) : page === "calls" ? (
        <Calls incident={incident} />
      ) : page === "plan" ? (
        <ResponsePlan incident={incident} />
      ) : page === "evacuation" ? (
        <Evacuation incident={incident} />
      ) : page === "resources" ? (
        <Resources incidents={[incident]} />
      ) : page === "buildings" ? (
        <Buildings key={id} incidents={incidents} incident={incident} />
      ) : page === "log" ? (
        <ActivityLog
          key={id}
          incidents={incidents}
          incident={incident}
          demo={demo}
        />
      ) : (
        <Navigate to={`/incidents/${encodeURIComponent(id)}/summary`} replace />
      )}
    </>
  );
}
function LegacyIncidentRoute() {
  const { id, page } = useParams();
  const location = useLocation();
  return (
    <Navigate
      replace
      to={
        id
          ? `/incidents/${encodeURIComponent(id)}/${page || "summary"}${location.search}`
          : "/incidents"
      }
    />
  );
}
export default function App() {
  const [demo, setDemo] = useState(
      () => new URLSearchParams(window.location.search).get("demo") === "1",
    ),
    [mobileOpen, setMobileOpen] = useState(false);
  const { incidents: latestIncidents, status } = useDashboard(demo),
    navigate = useNavigate();
  const timeline = useTimeline(latestIncidents, demo);
  const incidents = timeline.incidents;
  const changeSource = (value) => {
    const next = value === "demo";
    setDemo(next);
    const url = new URL(window.location.href);
    if (next) url.searchParams.set("demo", "1");
    else url.searchParams.delete("demo");
    window.history.replaceState(null, "", url);
    navigate("/overview");
  };
  const sidebar = (
    <div className="sidebar-content">
      <Link
        className="brand"
        to="/overview"
        onClick={() => setMobileOpen(false)}
      >
        <span>
          <span className="brand-wordmark">
            Respons<span>Ara</span>
          </span>
          <small>Wildfire coordination</small>
        </span>
      </Link>
      <div className="nav-label">Workspace</div>
      <nav aria-label="Main navigation">
        {nav.map(([path, label, Icon]) => (
          <NavLink
            className="nav-item"
            key={path}
            to={path}
            onClick={() => setMobileOpen(false)}
          >
            <Icon aria-hidden="true" />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
    </div>
  );
  return (
    <ApprovalProvider
      key={demo ? "design_demo" : "connected"}
      source={demo ? "design_demo" : "connected"}
      incidents={incidents}
      readOnly={timeline.historical}
    >
      <div className="app-shell">
        <aside className="desktop-sidebar">{sidebar}</aside>
        <Drawer
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          sx={{ "& .MuiDrawer-paper": { width: 260 } }}
        >
          {sidebar}
        </Drawer>
        <div className="workspace">
          <header className="topbar">
            <div className="topbar-left">
              <IconButton
                className="mobile-menu"
                aria-label="Open navigation"
                onClick={() => setMobileOpen(true)}
              >
                <MenuOutlined />
              </IconButton>
              <div className="mission-badge">
                <svg viewBox="0 0 56 24" aria-hidden="true">
                  <path d="M1 12h13l4-7 6 15 5-11 4 3h22" />
                </svg>
                Nobody left behind
              </div>
            </div>
            <div className="topbar-right">
              <span
                className={`connection ${status.connection === "connected" ? "ok" : ""}`}
              >
                <i />
                {status.connection === "connected"
                  ? "Connected"
                  : demo
                    ? "Design demo"
                    : status.connection === "connecting"
                      ? "Connecting"
                      : "Reconnecting"}
              </span>
              <select
                aria-label="Data source"
                value={demo ? "demo" : "connected"}
                onChange={(e) => changeSource(e.target.value)}
              >
                <option value="connected">Connected backend</option>
                <option value="demo">Design demo</option>
              </select>
              <span className="avatar">RA</span>
            </div>
          </header>
          <main key={demo ? "demo" : "connected"}>
            <TimelineControls timeline={timeline} />
            {demo ? (
              <Alert severity="info" className="mode-banner">
                Design demo · Illustrative incidents, calls, deployments and
                people. Confirmations here apply only to this demo.
              </Alert>
            ) : incidents[0]?.input_mode === "offline_demo" ? (
              <Alert severity="info" className="mode-banner">
                Offline backend demo · Supplied illustrative state. No real
                calls or deployments.
              </Alert>
            ) : null}
            {!demo && status.stale && (
              <Alert severity="warning">
                Source is stale or unavailable. Last supplied data remains
                visible; check assessment times.
              </Alert>
            )}
            {!demo && status.errors > 0 && (
              <Alert severity="error">
                The coordination source reported errors. Some information may be
                incomplete.
              </Alert>
            )}
            {!demo && !incidents.length ? (
              <MainCard>
                <Empty title="Waiting for coordination data">
                  Connect the read-only dashboard API to view current incidents.
                  You can explore the separate Design demo using the data-source
                  selector.
                </Empty>
              </MainCard>
            ) : (
              <Routes>
                <Route path="/active-fires" element={<LegacyIncidentRoute />} />
                <Route
                  path="/active-fires/:id/:page"
                  element={<LegacyIncidentRoute />}
                />
                <Route
                  path="/overview"
                  element={<Overview incidents={incidents} />}
                />
                <Route
                  path="/incidents"
                  element={<Incidents incidents={incidents} />}
                />
                <Route
                  path="/incidents/:id/:page"
                  element={<IncidentPage incidents={incidents} demo={demo} />}
                />
                <Route
                  path="/buildings"
                  element={<Buildings incidents={incidents} />}
                />
                <Route
                  path="/resources"
                  element={<Resources incidents={incidents} />}
                />
                <Route
                  path="/people"
                  element={<Evacuation incidents={incidents} />}
                />
                <Route
                  path="/log"
                  element={<ActivityLog incidents={incidents} demo={demo} />}
                />
                <Route path="*" element={<Navigate to="/overview" replace />} />
              </Routes>
            )}
            <footer className="page-footer">
              <span>ResponsAra · Analyst coordination</span>
              <span>
                {demo ? "Illustrative scenario" : "Source: coordination state"}{" "}
                · Plan review
              </span>
            </footer>
          </main>
        </div>
      </div>
    </ApprovalProvider>
  );
}
