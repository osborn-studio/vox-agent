import { useEffect, useState, type FormEvent } from "react";
import { pairingCodeSchema, type Device } from "@voxlog/contracts";
import type { z } from "zod";
import { client } from "../api";

type PairingCode = z.infer<typeof pairingCodeSchema>;

function statusBadgeClass(status: Device["status"]): string {
  return status === "PAIRED" || status === "CONNECTED" || status === "ONLINE"
    ? "badge"
    : "badge badge--muted";
}

function formatBattery(pct: number | null): string {
  return pct === null ? "unknown" : `${pct}%`;
}

export function DevicesView() {
  const [devices, setDevices] = useState<Device[]>([]);
  const [loading, setLoading] = useState(true);

  const [pairingCode, setPairingCode] = useState<PairingCode | null>(null);
  const [creatingCode, setCreatingCode] = useState(false);

  const [code, setCode] = useState("");
  const [ownerUserId, setOwnerUserId] = useState("user_local");
  const [workspaceId, setWorkspaceId] = useState("ws_demo");
  const [pairing, setPairing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function loadDevices() {
    let mounted = true;
    setLoading(true);
    client.listDevices().then((result) => {
      if (!mounted) return;
      setDevices(result);
      setLoading(false);
    });
    return () => {
      mounted = false;
    };
  }

  useEffect(() => {
    return loadDevices();
  }, []);

  function handleCreateCode() {
    setCreatingCode(true);
    setError(null);
    client.createPairingCode().then((result) => {
      setCreatingCode(false);
      if (result.ok) {
        setPairingCode(result.data);
        return;
      }
      setPairingCode(null);
      setError(
        result.offline
          ? "Engine unreachable — start vox-engine to generate a pairing code."
          : `Could not generate a pairing code (server responded ${result.status}).`
      );
    });
  }

  function handlePair(event: FormEvent) {
    event.preventDefault();
    const trimmedCode = code.trim();
    if (!trimmedCode || !ownerUserId.trim() || !workspaceId.trim()) return;
    setPairing(true);
    setError(null);
    client.pairDevice(trimmedCode, ownerUserId.trim(), workspaceId.trim()).then((result) => {
      setPairing(false);
      if (result.ok) {
        setCode("");
        setPairingCode(null);
        loadDevices();
        return;
      }
      setError(
        result.offline
          ? "Engine unreachable — pairing needs a live connection to vox-engine."
          : `Pairing failed (server responded ${result.status}). Check the code hasn't expired and try again.`
      );
    });
  }

  return (
    <section className="panel">
      <div className="card" style={{ flex: 1 }}>
        <p className="section-title">Devices</p>
        {loading ? (
          <div className="empty-state">Loading devices…</div>
        ) : devices.length === 0 ? (
          <div className="empty-state">No devices paired yet.</div>
        ) : (
          <div className="list">
            {devices.map((d) => (
              <div key={d.id} className="list-item" style={{ cursor: "default" }}>
                <span className="list-item-title">{d.displayName}</span>
                <span className="list-item-meta">
                  <span className="badge badge--muted">{d.kind}</span>
                  <span className={statusBadgeClass(d.status)}>{d.status}</span>
                  <span>Battery: {formatBattery(d.batteryPercent)}</span>
                  <span>Firmware: {d.firmwareVersion ?? "unknown"}</span>
                  <span>Last seen: {d.lastSeenAt ?? "never"}</span>
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="card" style={{ flex: "0 0 360px" }}>
        <p className="section-title">Pair a device</p>

        <button className="button button--primary" onClick={handleCreateCode} disabled={creatingCode}>
          {creatingCode ? "Generating…" : "Generate pairing code"}
        </button>

        {error && (
          <div
            role="alert"
            style={{
              marginTop: "1rem",
              padding: "0.6rem 0.8rem",
              borderRadius: "8px",
              border: "1px solid var(--color-danger)",
              color: "var(--color-danger)",
              background: "rgba(214, 69, 69, 0.08)"
            }}
          >
            {error}
          </div>
        )}

        {pairingCode && (
          <div className="card" style={{ marginTop: "1rem" }}>
            <p className="list-item-title" style={{ marginTop: 0 }}>
              Code: {pairingCode.code}
            </p>
            <p style={{ margin: "0.25rem 0", color: "var(--color-text-muted)", wordBreak: "break-all" }}>
              {pairingCode.qrPayload}
            </p>
            <p className="list-item-meta" style={{ margin: 0 }}>
              Expires: {pairingCode.expiresAt}
            </p>
          </div>
        )}

        <form onSubmit={handlePair} style={{ display: "flex", flexDirection: "column", gap: "0.5rem", marginTop: "1.25rem" }}>
          <p className="section-title" style={{ margin: 0 }}>
            Claim code
          </p>
          <input
            className="input"
            type="text"
            placeholder="Pairing code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <input
            className="input"
            type="text"
            placeholder="Owner user id"
            value={ownerUserId}
            onChange={(e) => setOwnerUserId(e.target.value)}
          />
          <input
            className="input"
            type="text"
            placeholder="Workspace id"
            value={workspaceId}
            onChange={(e) => setWorkspaceId(e.target.value)}
          />
          <button
            type="submit"
            className="button button--primary"
            disabled={pairing || !code.trim() || !ownerUserId.trim() || !workspaceId.trim()}
          >
            {pairing ? "Pairing…" : "Pair device"}
          </button>
        </form>
      </div>
    </section>
  );
}
