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
import Onboarding from "./onboarding/Onboarding";
import {
  useOnboarding,
  saveOnboarding,
  clearOnboarding,
} from "./onboarding/session";
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
  const onboarding = useOnboarding();
  // A session configuration takes over until the analyst picks a source explicitly.
  const [chosen, setChosen] = useState(() =>
    new URLSearchParams(window.location.search).get("demo") === "1"
      ? "design_demo"
      : null,
  );
  const [mobileOpen, setMobileOpen] = useState(false);
  const source =
    chosen === "onboarding" && !onboarding
      ? "connected"
      : (chosen ?? (onboarding ? "onboarding" : "connected"));
  const demo = source !== "connected";
  const { incidents: latestIncidents, status } = useDashboard(
      source,
      onboarding,
    ),
    navigate = useNavigate();
  const location = useLocation();
  // The onboarding form describes a source rather than reporting on one.
  const onboardingPage = location.pathname === "/onboarding";
  const timeline = useTimeline(latestIncidents, source);
  const incidents = timeline.incidents;
  // The selector keeps its published values; "demo" stays the design demo.
  const changeSource = (value) => {
    const next = value === "demo" ? "design_demo" : value;
    setChosen(next);
    const url = new URL(window.location.href);
    if (next === "design_demo") url.searchParams.set("demo", "1");
    else url.searchParams.delete("demo");
    window.history.replaceState(null, "", url);
    navigate("/overview");
  };
  const applyOnboarding = (config) => {
    saveOnboarding(config);
    setChosen("onboarding");
    const url = new URL(window.location.href);
    url.searchParams.delete("demo");
    window.history.replaceState(null, "", url);
  };
  const resetOnboarding = () => {
    clearOnboarding();
    setChosen(null);
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
      key={source}
      source={source}
      incidents={incidents}
      readOnly={timeline.historical}
      offline={source === "onboarding"}
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
              <Link
                className="header-brand"
                to="/overview"
                aria-label="ResponsAra overview"
              >
                <span className="brand-wordmark">
                  Respons<span>Ara</span>
                </span>
              </Link>
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
                {source === "onboarding"
                  ? "Session demo"
                  : status.connection === "connected"
                    ? "Connected"
                    : demo
                      ? "Design demo"
                      : status.connection === "connecting"
                        ? "Connecting"
                        : "Reconnecting"}
              </span>
              <select
                aria-label="Data source"
                value={source === "design_demo" ? "demo" : source}
                onChange={(e) => changeSource(e.target.value)}
              >
                <option value="connected">Connected backend</option>
                <option value="demo">Design demo</option>
                {onboarding && (
                  <option value="onboarding">Session onboarding</option>
                )}
              </select>
              <span className="avatar">RA</span>
            </div>
          </header>
          <main key={source}>
            {!onboardingPage && <TimelineControls timeline={timeline} />}
            {onboardingPage ? null : source === "onboarding" ? (
              <Alert severity="info" className="mode-banner">
                Session demo · Built from the onboarding form for this browser
                session. Synthetic locations, calls, crews and plans; nothing is
                dispatched or called.{" "}
                <Link to="/onboarding">Edit the session configuration</Link>
              </Alert>
            ) : demo ? (
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
            {!demo && !onboardingPage && status.stale && (
              <Alert severity="warning">
                Source is stale or unavailable. Last supplied data remains
                visible; check assessment times.
              </Alert>
            )}
            {!demo && !onboardingPage && status.errors > 0 && (
              <Alert severity="error">
                The coordination source reported errors. Some information may be
                incomplete.
              </Alert>
            )}
            <Routes>
              <Route
                path="/onboarding"
                element={
                  <Onboarding
                    config={onboarding}
                    onSave={applyOnboarding}
                    onClear={resetOnboarding}
                  />
                }
              />
              {!demo && !incidents.length ? (
                <Route
                  path="*"
                  element={
                    <MainCard>
                      <Empty title="Waiting for coordination data">
                        Connect the read-only dashboard API to view current
                        incidents. You can explore the separate Design demo
                        using the data-source selector, or build a session demo
                        from <Link to="/onboarding">demo onboarding</Link>.
                      </Empty>
                    </MainCard>
                  }
                />
              ) : (
                <>
                  <Route
                    path="/active-fires"
                    element={<LegacyIncidentRoute />}
                  />
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
                  <Route
                    path="*"
                    element={<Navigate to="/overview" replace />}
                  />
                </>
              )}
            </Routes>
            <footer className="page-footer">
              <span>ResponsAra · Analyst coordination</span>
              <span>
                {source === "onboarding"
                  ? "Source: session onboarding"
                  : demo
                    ? "Illustrative scenario"
                    : "Source: coordination state"}{" "}
                · Plan review · <Link to="/onboarding">Demo onboarding</Link>
              </span>
            </footer>
          </main>
        </div>
      </div>
    </ApprovalProvider>
  );
}
