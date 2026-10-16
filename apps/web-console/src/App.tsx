import { useState } from "react";
import { SessionsView } from "./views/SessionsView";
import { MemoryView } from "./views/MemoryView";
import { AgentsView } from "./views/AgentsView";
import { DevicesView } from "./views/DevicesView";
import { AdminView } from "./views/AdminView";

type Tab = "sessions" | "memory" | "agents" | "devices" | "admin";

const TABS: { key: Tab; label: string }[] = [
  { key: "sessions", label: "Sessions" },
  { key: "memory", label: "Memory" },
  { key: "agents", label: "Agents" },
  { key: "devices", label: "Devices" },
  { key: "admin", label: "Admin" }
];

export function App() {
  const [tab, setTab] = useState<Tab>("memory");
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">VOXAGENT Console</span>
        <nav className="tabs">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={t.key === tab ? "tab tab--active" : "tab"}
              onClick={() => setTab(t.key)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <main className="content">
        {tab === "sessions" && <SessionsView />}
        {tab === "memory" && <MemoryView />}
        {tab === "agents" && <AgentsView />}
        {tab === "devices" && <DevicesView />}
        {tab === "admin" && <AdminView />}
      </main>
    </div>
  );
}
