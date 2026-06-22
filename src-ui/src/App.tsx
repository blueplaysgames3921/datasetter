// src-ui/src/App.tsx
// Root component. Renders the nav header, routes between screens,
// and mounts global overlays (toasts, confirm dialog, agent detail modal).

import React, { useEffect } from "react";
import { AgentDetailModal, Btn, ConfirmDialog, ToastStack } from "./components";
import { checkHealth } from "./lib/api";
import {
  HistoryScreen,
  HomeScreen,
  SettingsScreen,
} from "./screens/OtherScreens";
import { NewProjectScreen } from "./screens/NewProjectScreen";
import { WorkspaceScreen } from "./screens/WorkspaceScreen";
import { useStore } from "./store";

// ── Nav header ────────────────────────────────────────────────────────────────

function Header() {
  const { screen, setScreen, currentConfig, pipelineState } = useStore();

  const navItems: [string, typeof screen][] = [
    ["Workspace", "workspace"],
    ["History",   "history"],
    ["Settings",  "settings"],
  ];

  return (
    <header style={{
      display: "flex", alignItems: "center",
      padding: "0 18px", height: 46,
      borderBottom: "1px solid var(--border-dim)",
      background: "#04070e", flexShrink: 0, gap: 12,
    }}>
      {/* Wordmark */}
      <div
        onClick={() => setScreen("home")}
        style={{
          fontSize: 15, fontWeight: 800, letterSpacing: ".12em",
          color: "var(--accent)", cursor: "pointer",
        }}
      >
        DATASETTER
      </div>

      <div style={{ width: 1, height: 14, background: "var(--border)" }} />

      {/* Nav */}
      <div style={{ display: "flex", gap: 2 }}>
        {navItems.map(([label, s]) => (
          <button
            key={s}
            onClick={() => setScreen(s)}
            style={{
              padding: "4px 12px", borderRadius: 4, fontSize: 11,
              background: screen === s ? "#f0c04014" : "transparent",
              color: screen === s ? "var(--accent)" : "var(--text-faint)",
              border: `1px solid ${screen === s ? "#f0c04035" : "transparent"}`,
              cursor: "pointer", fontFamily: "'Syne', sans-serif",
              fontWeight: 600, transition: "all .15s",
            }}
          >
            {label}
          </button>
        ))}
      </div>

      <div style={{ flex: 1 }} />

      {/* New dataset button */}
      <Btn variant="primary" onClick={() => setScreen("new")}>
        + New Dataset
      </Btn>
    </header>
  );
}

// ── Screen router ─────────────────────────────────────────────────────────────

function ScreenRouter() {
  const { screen } = useStore();

  return (
    <>
      {screen === "home"      && <HomeScreen />}
      {screen === "new"       && <NewProjectScreen />}
      {screen === "workspace" && <WorkspaceScreen />}
      {screen === "history"   && <HistoryScreen />}
      {screen === "settings"  && <SettingsScreen />}
    </>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const { loadSettings } = useStore();
  const [sidecarReady, setSidecarReady] = React.useState(false);
  const [checking,     setChecking]     = React.useState(true);

  // Wait for sidecar to be ready, then load settings
  useEffect(() => {
    let attempts = 0;
    const MAX    = 30;

    const poll = async () => {
      const ok = await checkHealth();
      if (ok) {
        setSidecarReady(true);
        setChecking(false);
        await loadSettings().catch(() => {});
        return;
      }
      attempts++;
      if (attempts >= MAX) {
        setChecking(false);
        return;
      }
      setTimeout(poll, 500);
    };

    poll();
  }, []);

  if (checking) {
    return (
      <div style={{
        height: "100vh", display: "flex", alignItems: "center",
        justifyContent: "center", flexDirection: "column", gap: 16,
        background: "var(--bg-base)",
      }}>
        <div style={{ fontSize: 18, fontWeight: 800, color: "var(--accent)", letterSpacing: ".1em" }}>
          DATASETTER
        </div>
        <div className="pulse" style={{ fontSize: 12, color: "var(--text-faint)" }}>
          Starting up…
        </div>
      </div>
    );
  }

  if (!sidecarReady) {
    return (
      <div style={{
        height: "100vh", display: "flex", alignItems: "center",
        justifyContent: "center", flexDirection: "column", gap: 16,
        background: "var(--bg-base)",
      }}>
        <div style={{ fontSize: 18, fontWeight: 800, color: "var(--error)" }}>
          DATASETTER
        </div>
        <div style={{ fontSize: 12, color: "var(--text-muted)", textAlign: "center", maxWidth: 320, lineHeight: 1.6 }}>
          Could not connect to the Python sidecar.
          <br />
          Make sure Python is installed and try relaunching.
        </div>
        <Btn variant="ghost" onClick={() => window.location.reload()}>
          Retry
        </Btn>
      </div>
    );
  }

  return (
    <>
      <Header />
      <ScreenRouter />

      {/* Global overlays */}
      <ConfirmDialog />
      <AgentDetailModal />
      <ToastStack />
    </>
  );
}
