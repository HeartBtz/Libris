import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  useNodesState,
} from "@xyflow/react";
import type { Edge, Node, ReactFlowInstance } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, send } from "../api";
import type { Run, Segment } from "../types";

const relationLabel = (value: string) =>
  (
    ({
      child_of: "enfant de",
      parent_of: "parent de",
      teacher_of: "enseigne à",
      student_of: "élève de",
      friend_of: "ami de",
      spouse_of: "conjoint de",
      sibling_of: "frère / sœur de",
      rival_of: "rival de",
    }) as Record<string, string>
  )[value] || value;

interface Person {
  id: string;
  name: string;
  identity_validated: boolean;
  validated: boolean;
  data: {
    aliases?: string[];
    proposed_aliases?: string[];
    role?: string;
    description?: string;
    relationships?: string[];
  };
}
interface Relation {
  id: string;
  source_id: string;
  target_id: string;
  relation_type: string;
  description: string;
  evidence: string;
  provenance: string;
  validated: boolean;
  position: number;
  segment_id: string | null;
}
interface Graph {
  nodes: Person[];
  edges: Relation[];
  suggestions: {
    source_id: string;
    target_id: string;
    reason: string;
    score: number;
  }[];
  merges: unknown[];
}

export default function CharacterGraph({
  pid,
  tick,
  run,
}: {
  pid: string;
  tick: number;
  run: Run;
}) {
  const [graph, setGraph] = useState<Graph>({
    nodes: [],
    edges: [],
    suggestions: [],
    merges: [],
  });
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [selected, setSelected] = useState("");
  const [relation, setRelation] = useState<Relation | null>(null);
  const [query, setQuery] = useState("");
  const [aliases, setAliases] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [kind, setKind] = useState("ami de");
  const [description, setDescription] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const [flow, setFlow] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const load = useCallback(
    async () => setGraph(await api(`/projects/${pid}/characters/graph`)),
    [pid],
  );
  useEffect(() => {
    void run(load);
  }, [load, run, tick]);
  useEffect(() => {
    const width = Math.max(1, Math.ceil(Math.sqrt(graph.nodes.length)));
    setNodes((previous) =>
      graph.nodes.map((p, i) => ({
        id: p.id,
        position: previous.find((n) => n.id === p.id)?.position || {
          x: (i % width) * 270,
          y: Math.floor(i / width) * 160,
        },
        data: {
          label: (
            <>
              <strong>{p.name}</strong>
              <small>
                {(p.data.aliases || []).join(" · ") || "Aucun alias enregistré"}
              </small>
            </>
          ),
        },
        style: {
          width: 225,
          background: "var(--surface)",
          color: "var(--text)",
          border: `1px solid ${selected === p.id ? "var(--accent)" : "var(--line)"}`,
        },
      })),
    );
  }, [graph.nodes, selected, setNodes]);
  const edges = useMemo<Edge[]>(() => {
    const groups = new Map<string, Relation[]>();
    for (const r of graph.edges) {
      const key = `${r.source_id}:${r.target_id}:${r.relation_type}`;
      groups.set(key, [...(groups.get(key) || []), r]);
    }
    return [...groups.values()].map((group) => {
      const r = group[0];
      return {
        id: r.id,
        source: r.source_id,
        target: r.target_id,
        label:
          relationLabel(r.relation_type) +
          (group.length > 1 ? ` · ${group.length}` : ""),
        type: "smoothstep",
        markerEnd: { type: MarkerType.ArrowClosed, color: "#7960ff" },
        style: {
          stroke: "#7960ff",
          strokeDasharray: group.every((e) => e.validated) ? undefined : "6 4",
        },
        labelStyle: { fill: "var(--text)", fontSize: 11 },
        labelBgStyle: { fill: "var(--surface)" },
      };
    });
  }, [graph.edges]);
  const person = graph.nodes.find((p) => p.id === selected);
  const matching = graph.nodes.filter((p) =>
    `${p.name} ${(p.data.aliases || []).join(" ")}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const name = (id: string) => graph.nodes.find((p) => p.id === id)?.name || id;
  async function merge(source: string, target: string) {
    if (
      !confirm(
        `Confirmer que ${name(source)} et ${name(target)} sont une seule personne ? ${name(target)} restera la fiche canonique, avec les noms conservés comme alias.`,
      )
    )
      return;
    await send(`/projects/${pid}/characters/merge`, {
      target_id: target,
      source_ids: [source],
    });
    setSelected(target);
    setMergeTarget("");
    await load();
  }
  return (
    <section>
      <div className="page-heading">
        <div>
          <h2>Personnages & relations</h2>
          <p className="muted">
            {graph.nodes.length} identités · {edges.length} liens regroupés.
            Trait plein : validé humainement. Pointillés : analyse ou
            proposition.
          </p>
        </div>
        <button
          onClick={() =>
            void run(async () => {
              await send(`/projects/${pid}/characters/rebuild-links`);
              await load();
            })
          }
        >
          Proposer les liens des fiches existantes
        </button>
      </div>
      <div className="character-layout">
        <div>
          <label>
            Rechercher un personnage ou un alias
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Rudy, Rudeus…"
            />
          </label>
          {query && (
            <div className="actions">
              {matching.map((p) => (
                <button
                  key={p.id}
                  onClick={() => {
                    setSelected(p.id);
                    setRelation(null);
                    void flow?.fitView({
                      nodes: [{ id: p.id }],
                      minZoom: 0.8,
                      maxZoom: 1.2,
                      duration: 300,
                    });
                  }}
                >
                  {p.name}
                </button>
              ))}
            </div>
          )}
          <div className="character-canvas" aria-label="Graphe des personnages">
            {nodes.length ? (
              <ReactFlow
                onInit={setFlow}
                nodes={nodes}
                edges={edges}
                onNodesChange={onNodesChange}
                fitView
                minZoom={0.15}
                maxZoom={2}
                nodesConnectable={false}
                deleteKeyCode={null}
                onNodeClick={(_event, node) => {
                  setSelected(node.id);
                  setRelation(null);
                  setExcerpt("");
                }}
                onEdgeClick={(_event, edge) => {
                  setRelation(
                    graph.edges.find((r) => r.id === edge.id) || null,
                  );
                  setSelected("");
                  setExcerpt("");
                }}
              >
                <Background color="#2b3565" gap={24} />
                <Controls showInteractive={false} />
              </ReactFlow>
            ) : (
              <div className="empty">
                Les personnages apparaîtront progressivement pendant l’analyse.
              </div>
            )}
          </div>
          <h3>Créer une relation</h3>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await send(`/projects/${pid}/characters/relations`, {
                  source_id: sourceId,
                  target_id: targetId,
                  relation_type: kind,
                  description,
                });
                await load();
                setDescription("");
              });
            }}
          >
            <div className="form-grid">
              <label>
                Personnage source
                <select
                  required
                  value={sourceId}
                  onChange={(e) => setSourceId(e.target.value)}
                >
                  <option value="">Choisir…</option>
                  {graph.nodes.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Personnage cible
                <select
                  required
                  value={targetId}
                  onChange={(e) => setTargetId(e.target.value)}
                >
                  <option value="">Choisir…</option>
                  {graph.nodes
                    .filter((p) => p.id !== sourceId)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                Type de lien
                <input
                  required
                  value={kind}
                  onChange={(e) => setKind(e.target.value)}
                  placeholder="enfant de, maître de, ami de…"
                />
              </label>
              <label>
                Précisions
                <input
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                />
              </label>
            </div>
            <button className="primary">Ajouter et valider le lien</button>
          </form>
        </div>
        <aside className="character-detail">
          {person ? (
            <>
              <h3>{person.name}</h3>
              <span className="badge">
                {person.identity_validated
                  ? "Identité confirmée"
                  : "Identité issue de l’analyse"}
              </span>
              <p>{person.data.role}</p>
              <p>{person.data.description}</p>
              <h3>Alias</h3>
              <button
                onClick={() => {
                  const adjacent = new Set([
                    person.id,
                    ...graph.edges
                      .filter(
                        (r) =>
                          r.source_id === person.id ||
                          r.target_id === person.id,
                      )
                      .flatMap((r) => [r.source_id, r.target_id]),
                  ]);
                  void flow?.fitView({
                    nodes: [...adjacent].map((id) => ({ id })),
                    padding: 0.25,
                    duration: 300,
                  });
                }}
              >
                Centrer sur ses relations
              </button>
              <p>{(person.data.aliases || []).join(" · ") || "Aucun"}</p>
              {!!person.data.proposed_aliases?.length && (
                <p className="muted">
                  Variantes proposées, non confirmées :{" "}
                  {person.data.proposed_aliases.join(" · ")}
                </p>
              )}
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    await send(
                      `/projects/${pid}/characters/${person.id}/aliases`,
                      {
                        aliases: aliases
                          .split(",")
                          .map((s) => s.trim())
                          .filter(Boolean),
                      },
                      "PUT",
                    );
                    setAliases("");
                    await load();
                  });
                }}
              >
                <label>
                  Ajouter des alias
                  <input
                    value={aliases}
                    onChange={(e) => setAliases(e.target.value)}
                    placeholder="Séparés par des virgules"
                  />
                </label>
                <button>Enregistrer les alias</button>
              </form>
              <h3>Regrouper deux identités</h3>
              <label>
                Fiche canonique de destination
                <select
                  value={mergeTarget}
                  onChange={(e) => setMergeTarget(e.target.value)}
                >
                  <option value="">Choisir…</option>
                  {graph.nodes
                    .filter((p) => p.id !== person.id)
                    .map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                </select>
              </label>
              <button
                disabled={!mergeTarget}
                onClick={() => void run(() => merge(person.id, mergeTarget))}
              >
                Fusionner avec cette fiche
              </button>
            </>
          ) : relation ? (
            <>
              <h3>
                {name(relation.source_id)} → {name(relation.target_id)}
              </h3>
              <label>
                Type
                <input
                  value={relation.relation_type}
                  onChange={(e) =>
                    setRelation({ ...relation, relation_type: e.target.value })
                  }
                />
              </label>
              <label>
                Description
                <textarea
                  value={relation.description}
                  onChange={(e) =>
                    setRelation({ ...relation, description: e.target.value })
                  }
                />
              </label>
              <p className="muted">
                Provenance : {relation.provenance} ·{" "}
                {relation.position >= 0
                  ? `passage ${relation.position + 1}`
                  : "instruction globale"}
              </p>
              {relation.evidence && (
                <blockquote>{relation.evidence}</blockquote>
              )}
              <div className="actions">
                <button
                  onClick={() =>
                    void run(async () => {
                      await send(
                        `/projects/${pid}/characters/relations/${relation.id}`,
                        {
                          source_id: relation.source_id,
                          target_id: relation.target_id,
                          relation_type: relation.relation_type,
                          description: relation.description,
                        },
                        "PUT",
                      );
                      await load();
                      setRelation(null);
                    })
                  }
                >
                  Enregistrer et valider
                </button>
                <button
                  onClick={() =>
                    void run(async () => {
                      await api(
                        `/projects/${pid}/characters/relations/${relation.id}`,
                        { method: "DELETE" },
                      );
                      await load();
                      setRelation(null);
                    })
                  }
                >
                  Écarter ce lien
                </button>
                {relation.segment_id && (
                  <button
                    onClick={() =>
                      void run(async () =>
                        setExcerpt(
                          (
                            await api<Segment>(
                              `/segments/${relation.segment_id}`,
                            )
                          ).source,
                        ),
                      )
                    }
                  >
                    Voir le passage source
                  </button>
                )}
              </div>
              {excerpt && <pre>{excerpt}</pre>}
            </>
          ) : (
            <p className="muted">
              Sélectionnez un personnage ou un lien. Les nœuds peuvent être
              déplacés et la vue zoomée.
            </p>
          )}
          <h3>Identités à rapprocher ?</h3>
          {graph.suggestions.slice(0, 12).map((s) => (
            <div
              className="identity-suggestion"
              key={`${s.source_id}:${s.target_id}`}
            >
              <strong>
                {name(s.source_id)} / {name(s.target_id)}
              </strong>
              <small>{s.reason}</small>
              <button
                onClick={() => void run(() => merge(s.source_id, s.target_id))}
              >
                Confirmer la même personne
              </button>
            </div>
          ))}
          {!graph.suggestions.length && (
            <p className="muted">Aucun rapprochement proposé.</p>
          )}
          <details>
            <summary>Historique des fusions</summary>
            <pre>{JSON.stringify(graph.merges, null, 2)}</pre>
          </details>
        </aside>
      </div>
    </section>
  );
}
