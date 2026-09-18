import { useCallback, useEffect, useMemo, useState } from "react";
import { Background, Controls, MarkerType, ReactFlow, useNodesState } from "@xyflow/react";
import type { Edge, Node, ReactFlowInstance } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, send } from "../api";
import { registerTranslations, useI18n } from "../i18n";
import type { Run, Segment } from "../types";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Field,
  FormGrid,
  Input,
  LoadingBlock,
  SearchInput,
  Select,
  TextArea,
  cx,
  useDialogs,
} from "../ui";

registerTranslations({
  "enfant de": "child of",
  "parent de": "parent of",
  "enseigne à": "teaches",
  "élève de": "student of",
  "ami de": "friend of",
  "conjoint de": "spouse of",
  "frère / sœur de": "sibling of",
  "rival de": "rival of",
  "Aucun alias enregistré": "No alias recorded",
  "Même personne ?": "Same person?",
  "{source} et {target} seront réunis. {target} reste la fiche canonique ; les noms sont conservés comme alias.":
    "{source} and {target} will be merged. {target} remains the canonical profile; names are kept as aliases.",
  Fusionner: "Merge",
  "Personnages & relations": "Characters & relationships",
  "{nodes} identités · {edges} liens regroupés. Trait plein : validé humainement. Pointillés : analyse ou proposition.":
    "{nodes} identities · {edges} grouped links. Solid line: human-validated. Dotted line: analysis or suggestion.",
  "Proposer les liens des fiches existantes": "Suggest links from existing profiles",
  "Rechercher un personnage ou un alias": "Search for a character or alias",
  "Nom ou alias…": "Name or alias…",
  "Graphe des personnages": "Character graph",
  "Les personnages apparaîtront progressivement pendant l’analyse.": "Characters will appear gradually during analysis.",
  "Créer une relation": "Create a relationship",
  "Personnage source": "Source character",
  "Personnage cible": "Target character",
  "Choisir…": "Choose…",
  "Type de lien": "Relationship type",
  "enfant de, maître de, ami de…": "child of, teacher of, friend of…",
  Précisions: "Details",
  "Ajouter et valider le lien": "Add and validate relationship",
  "Identité confirmée": "Identity confirmed",
  "Identité issue de l’analyse": "Identity from analysis",
  Alias: "Aliases",
  "Centrer sur ses relations": "Center on relationships",
  Aucun: "None",
  "Variantes proposées, non confirmées : {aliases}": "Proposed, unconfirmed variants: {aliases}",
  "Ajouter des alias": "Add aliases",
  "Séparés par des virgules": "Separated by commas",
  "Enregistrer les alias": "Save aliases",
  "Regrouper deux identités": "Merge two identities",
  "Fiche canonique de destination": "Destination canonical profile",
  "Fusionner avec cette fiche": "Merge with this profile",
  Type: "Type",
  Description: "Description",
  "Provenance : {source} · {place}": "Source: {source} · {place}",
  "passage {position}": "passage {position}",
  "instruction globale": "global instruction",
  "Enregistrer et valider": "Save and validate",
  "Écarter ce lien": "Discard this link",
  "Écarter ce lien ?": "Discard this link?",
  "Le lien disparaîtra du graphe et ne sera plus proposé au modèle.":
    "The link will disappear from the graph and will no longer be given to the model.",
  Écarter: "Discard",
  "Voir le passage source": "View source passage",
  "Sélectionnez un personnage ou un lien, dans le graphe ou dans la liste des relations. Les nœuds peuvent être déplacés et la vue zoomée.":
    "Select a character or link, in the graph or in the list of relationships. Nodes can be moved and the view zoomed.",
  "Identités à rapprocher ?": "Identities to match?",
  "Confirmer la même personne": "Confirm same person",
  "Aucun rapprochement proposé.": "No suggested match.",
  "Historique des fusions": "Merge history",
  Relations: "Relationships",
  "{source} → {target} : {type}": "{source} → {target}: {type}",
  "validé": "validated",
  "Chargement du graphe…": "Loading graph…",
  "Détail": "Details",
  "Aucune relation pour l’instant.": "No relationship yet.",
  "Personnage : {name}": "Character: {name}",
  "Appuyez sur Entrée pour sélectionner ; flèches pour déplacer.": "Press Enter to select; arrow keys to move.",
  "Appuyez sur Entrée pour sélectionner le lien.": "Press Enter to select the link.",
  Commandes: "Controls",
  "Zoom avant": "Zoom in",
  "Zoom arrière": "Zoom out",
  "Ajuster la vue": "Fit view",
});

const relationKeys: Record<string, string> = {
  child_of: "enfant de",
  parent_of: "parent de",
  teacher_of: "enseigne à",
  student_of: "élève de",
  friend_of: "ami de",
  spouse_of: "conjoint de",
  sibling_of: "frère / sœur de",
  rival_of: "rival de",
};

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
  suggestions: { source_id: string; target_id: string; reason: string; score: number }[];
  merges: unknown[];
}

export default function CharacterGraph({ pid, tick, run }: { pid: string; tick: number; run: Run }) {
  const { t } = useI18n();
  const { confirm } = useDialogs();
  const [graph, setGraph] = useState<Graph | null>(null);
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [selected, setSelected] = useState("");
  const [relation, setRelation] = useState<Relation | null>(null);
  const [query, setQuery] = useState("");
  const [aliases, setAliases] = useState("");
  const [mergeTarget, setMergeTarget] = useState("");
  const [sourceId, setSourceId] = useState("");
  const [targetId, setTargetId] = useState("");
  const [kind, setKind] = useState("");
  const [description, setDescription] = useState("");
  const [excerpt, setExcerpt] = useState("");
  const [flow, setFlow] = useState<ReactFlowInstance<Node, Edge> | null>(null);
  const relationLabel = useCallback((value: string) => (relationKeys[value] ? t(relationKeys[value]) : value), [t]);
  const load = useCallback(async () => setGraph(await api(`/projects/${pid}/characters/graph`)), [pid]);
  useEffect(() => {
    void run.background(load);
  }, [load, run, tick]);
  const people = graph?.nodes || [];
  const relations = graph?.edges || [];
  useEffect(() => {
    const width = Math.max(1, Math.ceil(Math.sqrt(people.length)));
    setNodes((previous) =>
      people.map((p, i) => ({
        id: p.id,
        position: previous.find((n) => n.id === p.id)?.position || { x: (i % width) * 260, y: Math.floor(i / width) * 150 },
        ariaLabel: t("Personnage : {name}", { name: p.name }),
        className: cx("graph-node", selected === p.id && "is-selected", p.identity_validated && "is-validated"),
        data: {
          label: (
            <>
              <strong>{p.name}</strong>
              <small>{(p.data.aliases || []).join(" · ") || t("Aucun alias enregistré")}</small>
            </>
          ),
        },
      })),
    );
  }, [people, selected, setNodes, t]);
  const groups = useMemo(() => {
    const map = new Map<string, Relation[]>();
    for (const r of relations) {
      const key = `${r.source_id}:${r.target_id}:${r.relation_type}`;
      map.set(key, [...(map.get(key) || []), r]);
    }
    return [...map.values()];
  }, [relations]);
  const name = (id: string) => people.find((p) => p.id === id)?.name || id;
  const edges = useMemo<Edge[]>(
    () =>
      groups.map((group) => {
        const r = group[0];
        const label = relationLabel(r.relation_type) + (group.length > 1 ? ` · ${group.length}` : "");
        return {
          id: r.id,
          source: r.source_id,
          target: r.target_id,
          label,
          ariaLabel: t("{source} → {target} : {type}", { source: name(r.source_id), target: name(r.target_id), type: label }),
          type: "smoothstep",
          markerEnd: { type: MarkerType.ArrowClosed, color: "var(--accent)" },
          style: {
            stroke: "var(--accent)",
            strokeWidth: relation?.id === r.id ? 2.5 : 1.5,
            strokeDasharray: group.every((e) => e.validated) ? undefined : "6 4",
          },
          labelStyle: { fill: "var(--text)", fontSize: 11, fontFamily: "var(--font-ui)" },
          labelBgStyle: { fill: "var(--surface)" },
          labelBgPadding: [6, 3] as [number, number],
          labelBgBorderRadius: 4,
        };
      }),
    [groups, relationLabel, relation?.id, people, t],
  );
  if (!graph) return <LoadingBlock label={t("Chargement du graphe…")} lines={4} />;
  const person = people.find((p) => p.id === selected);
  const matching = query
    ? people.filter((p) => `${p.name} ${(p.data.aliases || []).join(" ")}`.toLowerCase().includes(query.toLowerCase()))
    : [];
  const selectPerson = (id: string) => {
    setSelected(id);
    setRelation(null);
    setExcerpt("");
    void flow?.fitView({ nodes: [{ id }], minZoom: 0.8, maxZoom: 1.2, duration: 300 });
  };
  const selectRelation = (id: string) => {
    setRelation(relations.find((r) => r.id === id) || null);
    setSelected("");
    setExcerpt("");
  };
  async function merge(source: string, target: string) {
    const accepted = await confirm({
      title: t("Même personne ?"),
      message: t(
        "{source} et {target} seront réunis. {target} reste la fiche canonique ; les noms sont conservés comme alias.",
        { source: name(source), target: name(target) },
      ),
      confirmLabel: t("Fusionner"),
    });
    if (!accepted) return;
    await run(async () => {
      await send(`/projects/${pid}/characters/merge`, { target_id: target, source_ids: [source] });
      setSelected(target);
      setMergeTarget("");
      await load();
    });
  }
  return (
    <section className="stack">
      <div className="panel-header">
        <div>
          <h2>{t("Personnages & relations")}</h2>
          <p className="muted">
            {t(
              "{nodes} identités · {edges} liens regroupés. Trait plein : validé humainement. Pointillés : analyse ou proposition.",
              { nodes: people.length, edges: edges.length },
            )}
          </p>
        </div>
        <div className="panel-actions">
          <Button
            icon="sparkles"
            onClick={() =>
              void run(async () => {
                await send(`/projects/${pid}/characters/rebuild-links`);
                await load();
              })
            }
          >
            {t("Proposer les liens des fiches existantes")}
          </Button>
        </div>
      </div>
      <div className="character-layout">
        <div className="stack">
          <Card padded={false} className="graph-card">
            <div className="card-toolbar">
              <label className="grow">
                <span className="sr-only">{t("Rechercher un personnage ou un alias")}</span>
                <SearchInput value={query} onChange={(e) => setQuery(e.target.value)} placeholder={t("Nom ou alias…")} />
              </label>
              {!!matching.length && (
                <div className="chip-row">
                  {matching.slice(0, 8).map((p) => (
                    <Button key={p.id} size="sm" variant="ghost" onClick={() => selectPerson(p.id)}>
                      {p.name}
                    </Button>
                  ))}
                </div>
              )}
            </div>
            <div className="character-canvas" role="group" aria-label={t("Graphe des personnages")}>
              {nodes.length ? (
                <ReactFlow
                  onInit={setFlow}
                  nodes={nodes}
                  edges={edges}
                  onNodesChange={onNodesChange}
                  fitView
                  fitViewOptions={{ maxZoom: 1, padding: 0.2 }}
                  minZoom={0.15}
                  maxZoom={2}
                  nodesConnectable={false}
                  deleteKeyCode={null}
                  ariaLabelConfig={{
                    "node.a11yDescription.default": t("Appuyez sur Entrée pour sélectionner ; flèches pour déplacer."),
                    "edge.a11yDescription.default": t("Appuyez sur Entrée pour sélectionner le lien."),
                    "controls.ariaLabel": t("Commandes"),
                    "controls.zoomIn.ariaLabel": t("Zoom avant"),
                    "controls.zoomOut.ariaLabel": t("Zoom arrière"),
                    "controls.fitView.ariaLabel": t("Ajuster la vue"),
                  }}
                  onSelectionChange={({ nodes: chosenNodes, edges: chosenEdges }) => {
                    // Keyboard selection (Enter on a focused node or edge) goes through here.
                    if (chosenEdges.length === 1 && !chosenNodes.length) selectRelation(chosenEdges[0].id);
                    else if (chosenNodes.length === 1 && !chosenEdges.length) {
                      setSelected(chosenNodes[0].id);
                      setRelation(null);
                    }
                  }}
                  onNodeClick={(_event, node) => {
                    setSelected(node.id);
                    setRelation(null);
                    setExcerpt("");
                  }}
                  onEdgeClick={(_event, edge) => selectRelation(edge.id)}
                >
                  <Background color="var(--border)" gap={24} />
                  <Controls showInteractive={false} />
                </ReactFlow>
              ) : (
                <EmptyState icon="users" title={t("Les personnages apparaîtront progressivement pendant l’analyse.")} />
              )}
            </div>
          </Card>
          <Card title={t("Relations")} padded={false}>
            {groups.length ? (
              <ul className="relation-list">
                {groups.map((group) => {
                  const r = group[0];
                  return (
                    <li key={r.id}>
                      <button
                        type="button"
                        className="relation-item"
                        aria-pressed={relation?.id === r.id}
                        onClick={() => selectRelation(r.id)}
                      >
                        <span className="relation-names">
                          {name(r.source_id)} <span className="subtle">→</span> {name(r.target_id)}
                        </span>
                        <Badge tone={group.every((e) => e.validated) ? "success" : "neutral"}>
                          {relationLabel(r.relation_type)}
                          {group.length > 1 ? ` · ${group.length}` : ""}
                        </Badge>
                      </button>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <EmptyState compact icon="link" title={t("Aucune relation pour l’instant.")} />
            )}
          </Card>
          <Card title={t("Créer une relation")}>
            <form
              className="stack"
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
              <FormGrid>
                <Field label={t("Personnage source")}>
                  <Select required value={sourceId} onChange={(e) => setSourceId(e.target.value)}>
                    <option value="">{t("Choisir…")}</option>
                    {people.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </Select>
                </Field>
                <Field label={t("Personnage cible")}>
                  <Select required value={targetId} onChange={(e) => setTargetId(e.target.value)}>
                    <option value="">{t("Choisir…")}</option>
                    {people
                      .filter((p) => p.id !== sourceId)
                      .map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                  </Select>
                </Field>
                <Field label={t("Type de lien")}>
                  <Input
                    required
                    list="relation-types"
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                    placeholder={t("enfant de, maître de, ami de…")}
                  />
                  <datalist id="relation-types">
                    {Object.values(relationKeys).map((label) => (
                      <option key={label} value={t(label)} />
                    ))}
                  </datalist>
                </Field>
                <Field label={t("Précisions")}>
                  <Input value={description} onChange={(e) => setDescription(e.target.value)} />
                </Field>
              </FormGrid>
              <div>
                <Button type="submit" variant="primary" icon="plus">
                  {t("Ajouter et valider le lien")}
                </Button>
              </div>
            </form>
          </Card>
        </div>
        <aside className="stack character-detail">
          <Card title={t("Détail")}>
            {person ? (
              <div className="stack">
                <div className="stack-sm">
                  <div className="row">
                    <h3>{person.name}</h3>
                    <Badge tone={person.identity_validated ? "success" : "neutral"}>
                      {person.identity_validated ? t("Identité confirmée") : t("Identité issue de l’analyse")}
                    </Badge>
                  </div>
                  {person.data.role && <p className="muted">{person.data.role}</p>}
                  {person.data.description && <p>{person.data.description}</p>}
                </div>
                <div className="stack-sm">
                  <div className="row-between">
                    <strong>{t("Alias")}</strong>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => {
                        const adjacent = new Set([
                          person.id,
                          ...relations
                            .filter((r) => r.source_id === person.id || r.target_id === person.id)
                            .flatMap((r) => [r.source_id, r.target_id]),
                        ]);
                        void flow?.fitView({ nodes: [...adjacent].map((id) => ({ id })), padding: 0.25, duration: 300 });
                      }}
                    >
                      {t("Centrer sur ses relations")}
                    </Button>
                  </div>
                  <p className="alias-line">{(person.data.aliases || []).join(" · ") || t("Aucun")}</p>
                  {!!person.data.proposed_aliases?.length && (
                    <p className="subtle">
                      {t("Variantes proposées, non confirmées : {aliases}", {
                        aliases: person.data.proposed_aliases.join(" · "),
                      })}
                    </p>
                  )}
                </div>
                <form
                  className="stack-sm"
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
                  <Field label={t("Ajouter des alias")}>
                    <Input value={aliases} onChange={(e) => setAliases(e.target.value)} placeholder={t("Séparés par des virgules")} />
                  </Field>
                  <div>
                    <Button type="submit" size="sm">
                      {t("Enregistrer les alias")}
                    </Button>
                  </div>
                </form>
                <div className="stack-sm">
                  <strong>{t("Regrouper deux identités")}</strong>
                  <Field label={t("Fiche canonique de destination")}>
                    <Select value={mergeTarget} onChange={(e) => setMergeTarget(e.target.value)}>
                      <option value="">{t("Choisir…")}</option>
                      {people
                        .filter((p) => p.id !== person.id)
                        .map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                    </Select>
                  </Field>
                  <div>
                    <Button size="sm" disabled={!mergeTarget} onClick={() => void merge(person.id, mergeTarget)}>
                      {t("Fusionner avec cette fiche")}
                    </Button>
                  </div>
                </div>
              </div>
            ) : relation ? (
              <div className="stack">
                <h3>
                  {name(relation.source_id)} → {name(relation.target_id)}
                </h3>
                <Field label={t("Type")}>
                  <Input
                    value={relation.relation_type}
                    onChange={(e) => setRelation({ ...relation, relation_type: e.target.value })}
                  />
                </Field>
                <Field label={t("Description")}>
                  <TextArea
                    rows={3}
                    value={relation.description}
                    onChange={(e) => setRelation({ ...relation, description: e.target.value })}
                  />
                </Field>
                <p className="subtle">
                  {t("Provenance : {source} · {place}", {
                    source: relation.provenance,
                    place:
                      relation.position >= 0
                        ? t("passage {position}", { position: relation.position + 1 })
                        : t("instruction globale"),
                  })}
                </p>
                {relation.evidence && <blockquote className="book-text evidence">{relation.evidence}</blockquote>}
                <div className="row">
                  <Button
                    size="sm"
                    variant="primary"
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
                    {t("Enregistrer et valider")}
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      void (async () => {
                        const accepted = await confirm({
                          title: t("Écarter ce lien ?"),
                          message: t("Le lien disparaîtra du graphe et ne sera plus proposé au modèle."),
                          confirmLabel: t("Écarter"),
                          tone: "danger",
                        });
                        if (!accepted) return;
                        await run(async () => {
                          await api(`/projects/${pid}/characters/relations/${relation.id}`, { method: "DELETE" });
                          await load();
                          setRelation(null);
                        });
                      })()
                    }
                  >
                    {t("Écarter ce lien")}
                  </Button>
                  {relation.segment_id && (
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() =>
                        void run(async () => setExcerpt((await api<Segment>(`/segments/${relation.segment_id}`)).source))
                      }
                    >
                      {t("Voir le passage source")}
                    </Button>
                  )}
                </div>
                {excerpt && <blockquote className="book-text evidence">{excerpt}</blockquote>}
              </div>
            ) : (
              <p className="muted">
                {t(
                  "Sélectionnez un personnage ou un lien, dans le graphe ou dans la liste des relations. Les nœuds peuvent être déplacés et la vue zoomée.",
                )}
              </p>
            )}
          </Card>
          <Card title={t("Identités à rapprocher ?")}>
            {graph.suggestions.length ? (
              <ul className="suggestion-list">
                {graph.suggestions.slice(0, 12).map((s) => (
                  <li key={`${s.source_id}:${s.target_id}`}>
                    <strong>
                      {name(s.source_id)} / {name(s.target_id)}
                    </strong>
                    <small className="subtle">{s.reason}</small>
                    <div>
                      <Button size="sm" onClick={() => void merge(s.source_id, s.target_id)}>
                        {t("Confirmer la même personne")}
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="subtle">{t("Aucun rapprochement proposé.")}</p>
            )}
            {!!graph.merges.length && (
              <details className="disclosure disclosure-plain">
                <summary>{t("Historique des fusions")}</summary>
                <pre>{JSON.stringify(graph.merges, null, 2)}</pre>
              </details>
            )}
          </Card>
        </aside>
      </div>
    </section>
  );
}
