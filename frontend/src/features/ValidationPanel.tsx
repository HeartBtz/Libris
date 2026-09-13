import { useEffect, useState } from "react";
import { api } from "../api";
import type { Chapter, Issue, Project, Run, Segment } from "../types";
import { Inspector, SegmentRow } from "./Editor";

const PAGE_SIZE = 250;

export function ValidationPanel({
  project,
  chapters,
  run,
  refresh,
  tick,
}: {
  project: Project;
  chapters: Chapter[];
  run: Run;
  refresh: () => void;
  tick: number;
}) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [issues, setIssues] = useState<Issue[]>([]);
  const [selected, setSelected] = useState<Segment | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    void run(async () => {
      const found: Segment[] = [];
      let offset = 0;
      while (true) {
        const page = await api<Segment[]>(
          `/projects/${project.id}/segments?status=check&offset=${offset}&limit=${PAGE_SIZE}`,
        );
        found.push(...page);
        if (page.length < PAGE_SIZE) break;
        offset += PAGE_SIZE;
      }
      const projectIssues = await api<Issue[]>(
        `/projects/${project.id}/issues`,
      );
      if (active) {
        setSegments(found);
        setIssues(projectIssues.filter((issue) => !issue.resolved));
        setSelected((current) =>
          current
            ? found.find((segment) => segment.id === current.id) || null
            : null,
        );
        setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [project.id, run, tick]);

  const chapterNames = new Map(
    chapters.map((chapter) => [chapter.id, chapter.title]),
  );
  const issuesBySegment = new Map<string, Issue[]>();
  for (const issue of issues) {
    if (!issue.segment_id) continue;
    const current = issuesBySegment.get(issue.segment_id) || [];
    current.push(issue);
    issuesBySegment.set(issue.segment_id, current);
  }

  return (
    <section className="validation-panel">
      <div className="validation-heading">
        <div>
          <p className="eyebrow">Relecture humaine</p>
          <h2>Validations de traduction</h2>
          <p className="muted">
            Corrigez si nécessaire, puis validez. Le passage disparaîtra de
            cette file une fois la décision enregistrée.
          </p>
        </div>
        <span
          className="validation-count"
          aria-label={`${segments.length} passages à vérifier`}
        >
          {segments.length}
          <small>à vérifier</small>
        </span>
      </div>

      {loading ? (
        <p className="muted">Chargement des validations…</p>
      ) : segments.length ? (
        <div className="validation-queue">
          {segments.map((segment) => {
            const segmentIssues = issuesBySegment.get(segment.id) || [];
            return (
              <section className="validation-item" key={segment.id}>
                <header>
                  <div>
                    <strong>
                      {chapterNames.get(segment.chapter_id) || segment.section}
                    </strong>
                    <small>Passage {segment.position + 1}</small>
                  </div>
                  <div className="validation-reasons">
                    {!!segment.uncertainties.length && (
                      <span>{segment.uncertainties.length} incertitude(s)</span>
                    )}
                    {!!segment.critique.length && (
                      <span>{segment.critique.length} remarque(s) IA</span>
                    )}
                    {segmentIssues.map((issue) => (
                      <span
                        className={issue.severity}
                        key={issue.id}
                        title={issue.message}
                      >
                        {issue.message}
                      </span>
                    ))}
                    {!segment.uncertainties.length &&
                      !segment.critique.length &&
                      !segmentIssues.length && (
                        <span>Contrôle manuel demandé</span>
                      )}
                  </div>
                </header>
                <SegmentRow
                  segment={segment}
                  project={project}
                  run={run}
                  refresh={refresh}
                  inspect={() => setSelected(segment)}
                />
              </section>
            );
          })}
        </div>
      ) : (
        <div className="empty validation-empty">
          <img src="/assets/libris-icon.png" alt="" />
          <h2>Aucune validation en attente.</h2>
          <p>
            Les passages signalés par l’IA ou les contrôles apparaîtront ici.
          </p>
        </div>
      )}

      {selected && (
        <Inspector
          segment={selected}
          run={run}
          close={() => setSelected(null)}
          refresh={refresh}
        />
      )}
    </section>
  );
}
