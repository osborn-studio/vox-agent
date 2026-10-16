import { useEffect, useState } from "react";
import type { CaptureSession, CaptureSummary, TranscriptSegment } from "@voxlog/contracts";
import { client } from "../api";

interface Detail {
  session: CaptureSession;
  segments: TranscriptSegment[];
  summary: CaptureSummary;
}

function statusBadgeClass(status: CaptureSession["status"]): string {
  return status === "READY" ? "badge" : "badge badge--muted";
}

export function SessionsView() {
  const [sessions, setSessions] = useState<CaptureSession[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    let mounted = true;
    setLoading(true);
    client.listSessions().then((result) => {
      if (!mounted) return;
      setSessions(result);
      setLoading(false);
      if (result.length > 0) {
        setSelectedId(result[0].id);
      }
    });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let mounted = true;
    setDetailLoading(true);
    Promise.all([
      client.getSession(selectedId),
      client.getSegments(selectedId),
      client.getSummary(selectedId)
    ]).then(([session, segments, summary]) => {
      if (!mounted) return;
      setDetail({ session, segments, summary });
      setDetailLoading(false);
    });
    return () => {
      mounted = false;
    };
  }, [selectedId]);

  return (
    <section className="panel">
      <div className="card" style={{ flex: "0 0 320px" }}>
        <p className="section-title">Sessions</p>
        {loading ? (
          <div className="empty-state">Loading sessions…</div>
        ) : sessions.length === 0 ? (
          <div className="empty-state">No sessions yet.</div>
        ) : (
          <div className="list">
            {sessions.map((s) => (
              <button
                key={s.id}
                className={s.id === selectedId ? "list-item list-item--active" : "list-item"}
                onClick={() => setSelectedId(s.id)}
              >
                <span className="list-item-title">{s.title ?? "Untitled session"}</span>
                <span className="list-item-meta">
                  <span className="badge badge--muted">{s.mode}</span>
                  <span className={statusBadgeClass(s.status)}>{s.status}</span>
                  <span>{s.startedAt}</span>
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="card" style={{ flex: 1 }}>
        <p className="section-title">Session detail</p>
        {detailLoading ? (
          <div className="empty-state">Loading detail…</div>
        ) : !detail ? (
          <div className="empty-state">Select a session to see its transcript and summary.</div>
        ) : (
          <div>
            <h3 style={{ marginTop: 0 }}>{detail.session.title ?? "Untitled session"}</h3>
            <p className="list-item-meta">
              <span className={statusBadgeClass(detail.session.status)}>{detail.session.status}</span>
              <span>
                Languages:{" "}
                {detail.session.detectedLanguages.length > 0
                  ? detail.session.detectedLanguages.join(", ")
                  : "unknown"}
              </span>
            </p>

            <p className="section-title" style={{ marginTop: "1.25rem" }}>
              Transcript
            </p>
            {detail.segments.length === 0 ? (
              <div className="empty-state">No transcript segments.</div>
            ) : (
              <div className="list">
                {detail.segments.map((seg) => (
                  <div key={seg.id} className="list-item" style={{ cursor: "default" }}>
                    <span className="list-item-title">{seg.speakerLabel ?? seg.speakerId}</span>
                    <span>{seg.textNormalized}</span>
                  </div>
                ))}
              </div>
            )}

            <p className="section-title" style={{ marginTop: "1.25rem" }}>
              Summary
            </p>
            {detail.summary.summaryNodes.length === 0 ? (
              <div className="empty-state">No summary yet.</div>
            ) : (
              <div className="list">
                {detail.summary.summaryNodes.map((node) => (
                  <div key={node.id} className="list-item" style={{ cursor: "default" }}>
                    <span className="list-item-title">{node.title}</span>
                    <span>{node.body}</span>
                  </div>
                ))}
              </div>
            )}

            <p className="section-title" style={{ marginTop: "1.25rem" }}>
              Action items
            </p>
            {detail.summary.actionItems.length === 0 ? (
              <div className="empty-state">No action items.</div>
            ) : (
              <div className="list">
                {detail.summary.actionItems.map((node) => (
                  <div key={node.id} className="list-item" style={{ cursor: "default" }}>
                    <span className="list-item-title">{node.title}</span>
                    <span>{node.body}</span>
                  </div>
                ))}
              </div>
            )}

            <p className="section-title" style={{ marginTop: "1.25rem" }}>
              Decisions
            </p>
            {detail.summary.decisions.length === 0 ? (
              <div className="empty-state">No decisions.</div>
            ) : (
              <div className="list">
                {detail.summary.decisions.map((node) => (
                  <div key={node.id} className="list-item" style={{ cursor: "default" }}>
                    <span className="list-item-title">{node.title}</span>
                    <span>{node.body}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
}
