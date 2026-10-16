import { useEffect, useState, type FormEvent } from "react";
import type { AgentAnswer, MemoryNode, TokenBalance } from "@voxlog/contracts";
import { client } from "../api";

const SUGGESTED_PROMPTS = [
  "What were the decisions this week?",
  "Summarize the Japan launch",
  "Draft a follow-up email"
];

export function AgentsView() {
  const [query, setQuery] = useState("");
  const [answer, setAnswer] = useState<AgentAnswer | null>(null);
  const [answerNotice, setAnswerNotice] = useState<string | null>(null);
  const [asking, setAsking] = useState(false);

  const [balance, setBalance] = useState<TokenBalance | null>(null);
  const [balanceLoading, setBalanceLoading] = useState(true);

  const [recent, setRecent] = useState<MemoryNode[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setBalanceLoading(true);
    client.getTokenBalance().then((result) => {
      if (!mounted) return;
      setBalance(result);
      setBalanceLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    setRecentLoading(true);
    client.getRecent().then((result) => {
      if (!mounted) return;
      setRecent(result);
      setRecentLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, []);

  function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) return;
    setAsking(true);
    setAnswer(null);
    setAnswerNotice(null);
    client.askAgent(trimmed).then((result) => {
      setAnswer(result.data);
      setAnswerNotice(
        result.ok
          ? null
          : result.offline
            ? "Engine unreachable — showing an offline placeholder, not a live answer."
            : `Engine error (server responded ${result.status}) — showing an offline placeholder.`
      );
      setAsking(false);
    });
  }

  return (
    <section>
      <form className="card" onSubmit={handleSubmit} style={{ marginBottom: "1.5rem" }}>
        <p className="section-title">Ask an agent</p>
        <div style={{ display: "flex", gap: "0.5rem" }}>
          <input
            className="input"
            type="text"
            placeholder="Ask a question about your memory…"
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
            }}
          />
          <button type="submit" className="button button--primary" disabled={!query.trim() || asking}>
            {asking ? "Asking…" : "Ask"}
          </button>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap", marginTop: "0.75rem" }}>
          {SUGGESTED_PROMPTS.map((prompt) => (
            <button
              key={prompt}
              type="button"
              className="chip"
              onClick={() => {
                setQuery(prompt);
              }}
            >
              {prompt}
            </button>
          ))}
        </div>

        {asking && (
          <p className="empty-state" style={{ marginTop: "1rem", textAlign: "left" }}>
            Asking the agent…
          </p>
        )}

        {answerNotice && !asking && (
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
            {answerNotice}
          </div>
        )}

        {answer && !asking && (
          <div style={{ marginTop: "1rem" }}>
            <p style={{ margin: 0 }}>{answer.answer}</p>
            <p className="list-item-meta" style={{ marginTop: "0.5rem" }}>
              <span className="badge badge--muted">{answer.answerer}</span>
            </p>
            {answer.citations.length === 0 ? (
              <p className="empty-state" style={{ textAlign: "left" }}>
                No citations — no matching memory found.
              </p>
            ) : (
              <div className="list" style={{ marginTop: "0.5rem" }}>
                {answer.citations.map((citation) => (
                  <div key={citation.id} className="list-item" style={{ cursor: "default" }}>
                    <span className="list-item-meta">
                      <span className="badge">{citation.type ?? "UNKNOWN"}</span>
                      {citation.id}
                    </span>
                    <span className="list-item-title">{citation.title ?? "(untitled)"}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </form>

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <p className="section-title">MCP status</p>
        <p style={{ margin: 0 }}>
          MCP: 8 <code>vox.*</code> tools — run <code>vox-engine serve-mcp</code>
        </p>
      </div>

      <div className="card" style={{ marginBottom: "1.5rem" }}>
        <p className="section-title">Token balance</p>
        {balanceLoading ? (
          <div className="empty-state">Loading balance…</div>
        ) : !balance ? (
          <div className="empty-state">Balance unavailable.</div>
        ) : (
          <p style={{ margin: 0 }}>
            {balance.balance} {balance.currency} ({balance.scopeType.toLowerCase()} scope)
          </p>
        )}
      </div>

      <div className="card">
        <p className="section-title">Recent context</p>
        {recentLoading ? (
          <div className="empty-state">Loading recent context…</div>
        ) : recent.length === 0 ? (
          <div className="empty-state">No recent memory nodes.</div>
        ) : (
          <div className="list">
            {recent.map((node) => (
              <div key={node.id} className="list-item" style={{ cursor: "default" }}>
                <span className="list-item-meta">{node.id}</span>
                <span className="list-item-title">{node.title}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
