import { useEffect, useState, type FormEvent } from "react";
import type { MemoryNode, MemorySearchResponse } from "@voxlog/contracts";
import { client } from "../api";

type Mode = "recent" | "action-items";

function groupByType(nodes: MemoryNode[]): Map<string, MemoryNode[]> {
  const groups = new Map<string, MemoryNode[]>();
  for (const node of nodes) {
    const bucket = groups.get(node.type) ?? [];
    bucket.push(node);
    groups.set(node.type, bucket);
  }
  return groups;
}

function snippet(body: string, maxLength = 140): string {
  if (body.length <= maxLength) return body;
  return `${body.slice(0, maxLength).trimEnd()}…`;
}

function NodeCard({ node }: { node: MemoryNode }) {
  return (
    <div className="card">
      <span className="badge">{node.type}</span>
      <p className="list-item-title" style={{ marginTop: "0.5rem" }}>
        {node.title}
      </p>
      <p style={{ margin: 0, color: "var(--color-text-muted)" }}>{snippet(node.body)}</p>
    </div>
  );
}

export function MemoryView() {
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<MemorySearchResponse | null>(null);

  const [mode, setMode] = useState<Mode>("recent");
  const [nodes, setNodes] = useState<MemoryNode[]>([]);
  const [nodesLoading, setNodesLoading] = useState(true);

  useEffect(() => {
    let mounted = true;
    setNodesLoading(true);
    const fetcher = mode === "recent" ? client.getRecent() : client.getActionItems();
    fetcher.then((result) => {
      if (!mounted) return;
      setNodes(result);
      setNodesLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, [mode]);

  function handleSearch(event: FormEvent) {
    event.preventDefault();
    const q = query.trim();
    if (!q) return;
    let mounted = true;
    setSearching(true);
    client.searchMemory(q).then((result) => {
      if (!mounted) return;
      setSearchResult(result);
      setSearching(false);
    });
    return () => {
      mounted = false;
    };
  }

  const grouped = groupByType(nodes);

  return (
    <section>
      <form className="card" onSubmit={handleSearch} style={{ display: "flex", gap: "0.5rem", marginBottom: "1.5rem" }}>
        <input
          className="input"
          type="text"
          placeholder="Search memory (e.g. Japan launch)"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button type="submit" className="button button--primary" disabled={searching || !query.trim()}>
          {searching ? "Searching…" : "Search"}
        </button>
      </form>

      {searchResult && (
        <div className="card" style={{ marginBottom: "1.5rem" }}>
          <p className="section-title">Search results for "{searchResult.query}"</p>
          {searchResult.results.length === 0 ? (
            <div className="empty-state">No matches found.</div>
          ) : (
            <div className="list">
              {searchResult.results.map((r) => (
                <NodeCard key={r.node.id} node={r.node} />
              ))}
            </div>
          )}
        </div>
      )}

      <div className="tabs" style={{ marginBottom: "1rem" }}>
        <button
          className={mode === "recent" ? "tab tab--active" : "tab"}
          onClick={() => setMode("recent")}
        >
          Recent
        </button>
        <button
          className={mode === "action-items" ? "tab tab--active" : "tab"}
          onClick={() => setMode("action-items")}
        >
          Action items
        </button>
      </div>

      {nodesLoading ? (
        <div className="empty-state">Loading…</div>
      ) : nodes.length === 0 ? (
        <div className="empty-state">
          {mode === "recent" ? "No recent memory nodes." : "No action items."}
        </div>
      ) : mode === "action-items" ? (
        <div className="list">
          {nodes.map((node) => (
            <NodeCard key={node.id} node={node} />
          ))}
        </div>
      ) : (
        <div>
          {Array.from(grouped.entries()).map(([type, groupNodes]) => (
            <div key={type} style={{ marginBottom: "1.25rem" }}>
              <p className="section-title">{type}</p>
              <div className="list">
                {groupNodes.map((node) => (
                  <NodeCard key={node.id} node={node} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
