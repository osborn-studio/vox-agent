import { FormEvent, useCallback, useEffect, useState } from "react";
import type {
  AdminRole,
  AdminUser,
  AuditEvent,
  IssuedCredential,
  ModelPolicy,
  RetentionPolicy,
  RetentionSweep,
  TokenBalance,
  TokenLedgerEntry
} from "@voxlog/contracts";
import { client } from "../api";
import { ApiError } from "../api/client";

const ROLE_OPTIONS: Array<{ value: AdminRole; label: string }> = [
  { value: "member", label: "Member" },
  { value: "product_manager", label: "Product manager" },
  { value: "tech_lead", label: "Tech lead" },
  { value: "engineering_manager", label: "Engineering manager" },
  { value: "admin", label: "Administrator" }
];

export function AdminView() {
  const [balance, setBalance] = useState<TokenBalance | null>(null);
  const [ledger, setLedger] = useState<TokenLedgerEntry[]>([]);
  const [usageLoading, setUsageLoading] = useState(true);

  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [policy, setPolicy] = useState<ModelPolicy | null>(null);
  const [retention, setRetention] = useState<RetentionPolicy | null>(null);
  const [retentionSweep, setRetentionSweep] = useState<RetentionSweep | null>(null);
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [adminLoading, setAdminLoading] = useState(true);
  const [adminError, setAdminError] = useState<ApiError | null>(null);

  const [tokenInput, setTokenInput] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [newDisplayName, setNewDisplayName] = useState("");
  const [newRole, setNewRole] = useState<AdminRole>("member");
  const [userSaving, setUserSaving] = useState<string | null>(null);
  const [issuedCredential, setIssuedCredential] = useState<
    (IssuedCredential & { displayName: string }) | null
  >(null);
  const [audioDays, setAudioDays] = useState("30");
  const [transcriptDays, setTranscriptDays] = useState("90");
  const [memoryDays, setMemoryDays] = useState("365");
  const [autoDelete, setAutoDelete] = useState(false);
  const [retentionSaving, setRetentionSaving] = useState(false);
  const [confirmSweep, setConfirmSweep] = useState(false);

  const loadAdmin = useCallback(async () => {
    setAdminLoading(true);
    setAdminError(null);
    try {
      const [nextAudit, nextPolicy, nextRetention, nextUsers, nextSweep] = await Promise.all([
        client.getAuditEvents(),
        client.getModelPolicy(),
        client.getRetentionPolicy(),
        client.listAdminUsers(),
        client.getLatestRetentionSweep()
      ]);
      setAudit(nextAudit);
      setPolicy(nextPolicy);
      setRetention(nextRetention);
      setUsers(nextUsers);
      setRetentionSweep(nextSweep);
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to load admin data"));
    } finally {
      setAdminLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!retention) return;
    setAudioDays(String(retention.audioRetentionDays));
    setTranscriptDays(String(retention.transcriptRetentionDays));
    setMemoryDays(String(retention.memoryRetentionDays));
    setAutoDelete(retention.autoDeleteEnabled);
  }, [retention]);

  useEffect(() => {
    let mounted = true;
    setUsageLoading(true);
    Promise.all([client.getTokenBalance(), client.getTokenLedger()]).then(([nextBalance, nextLedger]) => {
      if (!mounted) return;
      setBalance(nextBalance);
      setLedger(nextLedger);
      setUsageLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    void loadAdmin();
  }, [loadAdmin]);

  async function connectAdmin(event: FormEvent) {
    event.preventDefault();
    client.setAdminToken(tokenInput);
    setIssuedCredential(null);
    await loadAdmin();
    setTokenInput("");
  }

  async function createUser(event: FormEvent) {
    event.preventDefault();
    setAdminError(null);
    setUserSaving("new");
    try {
      const user = await client.createAdminUser({
        email: newEmail,
        displayName: newDisplayName,
        role: newRole
      });
      setUsers((current) => [...current, user].sort((a, b) => a.displayName.localeCompare(b.displayName)));
      setAudit(await client.getAuditEvents());
      setNewEmail("");
      setNewDisplayName("");
      setNewRole("member");
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to create user"));
    } finally {
      setUserSaving(null);
    }
  }

  async function updateUser(user: AdminUser, changes: { role?: AdminRole; status?: "active" | "suspended" }) {
    setAdminError(null);
    setUserSaving(user.id);
    try {
      const updated = await client.updateAdminUser(user.id, changes);
      setUsers((current) => current.map((item) => item.id === user.id ? updated : item));
      setAudit(await client.getAuditEvents());
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to update user"));
    } finally {
      setUserSaving(null);
    }
  }

  async function issueCredential(user: AdminUser) {
    setAdminError(null);
    setUserSaving(user.id);
    try {
      const credential = await client.issueAdminCredential(user.id, "Web console");
      setIssuedCredential({ ...credential, displayName: user.displayName });
      setAudit(await client.getAuditEvents());
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to issue credential"));
    } finally {
      setUserSaving(null);
    }
  }

  async function revokeIssuedCredential() {
    if (!issuedCredential) return;
    setAdminError(null);
    try {
      await client.revokeAdminCredential(issuedCredential.id);
      setIssuedCredential(null);
      setAudit(await client.getAuditEvents());
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to revoke credential"));
    }
  }

  async function saveRetention(event: FormEvent) {
    event.preventDefault();
    const values = [audioDays, transcriptDays, memoryDays].map(Number);
    if (values.some((value) => !Number.isInteger(value) || value < 1)) {
      setAdminError(new ApiError("Retention days must be positive whole numbers"));
      return;
    }
    setRetentionSaving(true);
    setAdminError(null);
    try {
      const next = await client.patchRetentionPolicy({
        audioRetentionDays: values[0],
        transcriptRetentionDays: values[1],
        memoryRetentionDays: values[2],
        autoDeleteEnabled: autoDelete
      });
      setRetention(next);
      setAudit(await client.getAuditEvents());
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to save retention policy"));
    } finally {
      setRetentionSaving(false);
    }
  }

  async function resetRetentionDefaults() {
    setRetentionSaving(true);
    setAdminError(null);
    try {
      const next = await client.patchRetentionPolicy({
        audioRetentionDays: null,
        transcriptRetentionDays: null,
        memoryRetentionDays: null,
        autoDeleteEnabled: null
      });
      setRetention(next);
      setAudit(await client.getAuditEvents());
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to reset retention policy"));
    } finally {
      setRetentionSaving(false);
    }
  }

  async function previewRetentionSweep() {
    setRetentionSaving(true);
    setAdminError(null);
    setConfirmSweep(false);
    try {
      setRetentionSweep(await client.runRetentionSweep(true));
      setConfirmSweep(true);
      setAudit(await client.getAuditEvents());
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to preview retention sweep"));
    } finally {
      setRetentionSaving(false);
    }
  }

  async function executeRetentionSweep() {
    setRetentionSaving(true);
    setAdminError(null);
    try {
      setRetentionSweep(await client.runRetentionSweep(false));
      setConfirmSweep(false);
      setAudit(await client.getAuditEvents());
    } catch (error) {
      setAdminError(error instanceof ApiError ? error : new ApiError("Unable to run retention sweep"));
    } finally {
      setRetentionSaving(false);
    }
  }

  return (
    <section aria-busy={adminLoading}>
      <div className="card admin-auth-card">
        <div>
          <p className="section-title">Admin access</p>
          <p className="admin-copy">
            Personal mode connects locally. Enterprise credentials stay in this page session and are never saved in browser storage.
          </p>
        </div>
        <form className="admin-auth-form" onSubmit={connectAdmin}>
          <label className="sr-only" htmlFor="admin-token">Admin bearer token</label>
          <input
            className="input"
            id="admin-token"
            type="password"
            autoComplete="off"
            value={tokenInput}
            onChange={(event) => setTokenInput(event.target.value)}
            placeholder="Enterprise bearer token"
          />
          <button className="button button--primary" type="submit" disabled={adminLoading}>
            {adminLoading ? "Connecting…" : "Connect"}
          </button>
        </form>
      </div>

      {adminError && (
        <div className="error-banner" role="alert">
          <strong>{adminError.status === 401 ? "Authentication required." : "Admin request failed."}</strong>{" "}
          {adminError.message}
        </div>
      )}

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <p className="section-title">Token usage</p>
        {usageLoading ? (
          <div className="empty-state">Loading usage…</div>
        ) : (
          <>
            <p className="list-item-title" style={{ marginTop: 0 }}>
              {balance ? `${balance.balance.toLocaleString()} ${balance.currency}` : "No balance available"}
            </p>
            <p className="section-title" style={{ marginTop: "1.25rem" }}>Recent ledger entries</p>
            {ledger.length === 0 ? (
              <div className="empty-state">No ledger entries yet.</div>
            ) : (
              <div className="table-scroll">
                <table className="table">
                  <thead><tr><th>Spend type</th><th>Delta</th><th>Balance after</th><th>Description</th><th>Created</th></tr></thead>
                  <tbody>{ledger.map((entry) => (
                    <tr key={entry.id}><td>{entry.spendType}</td><td>{entry.delta}</td><td>{entry.balanceAfter}</td><td>{entry.description}</td><td>{entry.createdAt}</td></tr>
                  ))}</tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>

      {!adminError && !adminLoading && (
        <>
          <div className="card" style={{ marginBottom: "1.5rem" }}>
            <div className="admin-section-heading">
              <div><p className="section-title">Users &amp; roles</p><p className="admin-copy">Manage organization access and issue revocable API credentials.</p></div>
              <span className="badge badge--muted">{users.length} users</span>
            </div>

            <form className="admin-user-form" onSubmit={createUser}>
              <label>Name<input className="input" value={newDisplayName} onChange={(event) => setNewDisplayName(event.target.value)} required /></label>
              <label>Email<input className="input" type="email" value={newEmail} onChange={(event) => setNewEmail(event.target.value)} required /></label>
              <label>Role<select className="input" value={newRole} onChange={(event) => setNewRole(event.target.value as AdminRole)}>{ROLE_OPTIONS.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}</select></label>
              <button className="button button--primary" type="submit" disabled={userSaving === "new"}>{userSaving === "new" ? "Adding…" : "Add user"}</button>
            </form>

            <div className="table-scroll">
              <table className="table">
                <thead><tr><th>User</th><th>Role</th><th>Status</th><th>Credential</th></tr></thead>
                <tbody>{users.map((user) => (
                  <tr key={user.id}>
                    <td><strong>{user.displayName}</strong><div className="list-item-meta">{user.email}</div></td>
                    <td><select className="input input--compact" aria-label={`Role for ${user.displayName}`} value={user.role} disabled={userSaving === user.id} onChange={(event) => void updateUser(user, { role: event.target.value as AdminRole })}>{ROLE_OPTIONS.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}</select></td>
                    <td><select className="input input--compact" aria-label={`Status for ${user.displayName}`} value={user.status} disabled={userSaving === user.id} onChange={(event) => void updateUser(user, { status: event.target.value as "active" | "suspended" })}><option value="active">Active</option><option value="suspended">Suspended</option></select></td>
                    <td><button className="button" type="button" disabled={userSaving === user.id || user.status !== "active"} onClick={() => void issueCredential(user)}>Issue token</button></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>

            {issuedCredential && (
              <div className="credential-panel" role="status">
                <div><strong>Token for {issuedCredential.displayName}</strong><p>Copy it now. It will not be shown again.</p></div>
                <code>{issuedCredential.token}</code>
                <button className="button button--danger" type="button" onClick={() => void revokeIssuedCredential()}>Revoke</button>
              </div>
            )}
          </div>

          <div className="card" style={{ marginBottom: "1.5rem" }}>
            <p className="section-title">Audit log</p>
            {audit.length === 0 ? <div className="empty-state">No audit events yet.</div> : (
              <div className="table-scroll"><table className="table"><thead><tr><th>Action</th><th>Actor</th><th>Target</th><th>Details</th><th>Created</th></tr></thead><tbody>{audit.map((event) => (
                <tr key={event.id}><td><span className="badge badge--muted">{event.action}</span></td><td>{event.actorId ?? "—"}</td><td>{event.targetId ?? "—"}</td><td>{Object.keys(event.metadata).length ? JSON.stringify(event.metadata) : "—"}</td><td>{event.createdAt}</td></tr>
              ))}</tbody></table></div>
            )}
          </div>

          <div className="admin-grid">
            <div className="card">
              <p className="section-title">Model routing</p>
              {policy && <div className="admin-stack"><span className="badge badge--muted">Answerer: {policy.answerer}</span><span className="badge badge--muted">ASR: {policy.asrAdapter}</span><span className="badge badge--muted">LLM: {policy.llmConfigured ? `configured (${policy.llmModel ?? "?"})` : "not configured"}</span></div>}
            </div>
            <div className="card">
              <p className="section-title">Retention policy</p>
              {retention && (
                <form className="retention-form" onSubmit={saveRetention}>
                  <div className="retention-fields">
                    <label htmlFor="retention-audio">Audio days<input className="input" id="retention-audio" type="number" min="1" step="1" value={audioDays} onChange={(event) => setAudioDays(event.target.value)} required /></label>
                    <label htmlFor="retention-transcripts">Transcript days<input className="input" id="retention-transcripts" type="number" min="1" step="1" value={transcriptDays} onChange={(event) => setTranscriptDays(event.target.value)} required /></label>
                    <label htmlFor="retention-memory">Memory days<input className="input" id="retention-memory" type="number" min="1" step="1" value={memoryDays} onChange={(event) => setMemoryDays(event.target.value)} required /></label>
                  </div>
                  <label className="retention-toggle"><input type="checkbox" checked={autoDelete} onChange={(event) => setAutoDelete(event.target.checked)} />Run automatic deletion</label>
                  <div className="retention-actions">
                    <button className="button button--primary" type="submit" disabled={retentionSaving}>{retentionSaving ? "Working…" : "Save policy"}</button>
                    <button className="button" type="button" disabled={retentionSaving} onClick={() => void resetRetentionDefaults()}>Use defaults</button>
                    <button className="button" type="button" disabled={retentionSaving} onClick={() => void previewRetentionSweep()}>Preview deletion</button>
                  </div>
                  <p className="admin-copy">Overrides are durable across restarts. Preview never changes files or records.</p>
                </form>
              )}
              {retentionSweep && (
                <div className="retention-preview" role="status">
                  <strong>{retentionSweep.dryRun ? "Deletion preview" : "Deletion completed"}</strong>
                  <p>
                    Candidates: {retentionSweep.candidates.audio} audio, {retentionSweep.candidates.transcripts} transcript sessions, {retentionSweep.candidates.memory} memory nodes.
                    {!retentionSweep.dryRun && ` Deleted: ${retentionSweep.deleted.audio} audio, ${retentionSweep.deleted.transcripts} transcripts, ${retentionSweep.deleted.memory} memory nodes.`}
                  </p>
                  {retentionSweep.failures.length > 0 && <p>{retentionSweep.failures.length} item(s) could not be safely deleted.</p>}
                  {confirmSweep && (
                    <div className="retention-confirm" role="alert">
                      <span>This permanently deletes the previewed expired data.</span>
                      <button className="button button--danger" type="button" disabled={retentionSaving} onClick={() => void executeRetentionSweep()}>Confirm deletion</button>
                      <button className="button" type="button" onClick={() => setConfirmSweep(false)}>Cancel</button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </section>
  );
}
