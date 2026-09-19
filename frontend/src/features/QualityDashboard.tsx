import { useEffect, useState } from "react";
import { api } from "../api";
import { formatNumber, registerTranslations, useI18n } from "../i18n";
import type { Run } from "../types";
import { Badge, Button, Card, EmptyState, LoadingBlock, Stat, Table } from "../ui";
import type { Tone } from "../ui";

registerTranslations({
  "Score de qualité des passages": "Passage quality score",
  "Chaque passage traduit reçoit un score de 0 à 100 calculé à partir des signaux déjà enregistrés (contrôles automatiques, critiques, doutes, échecs, récupérations) ; il oriente la relecture sans mesurer seul la qualité littéraire.":
    "Each translated passage gets a score from 0 to 100 computed from the signals already recorded (automatic checks, critiques, doubts, failures, recoveries); it guides the review without measuring literary quality on its own.",
  "Chargement des scores…": "Loading scores…",
  "Score de qualité": "Quality score",
  "Score moyen": "Average score",
  "Passages notés": "Scored passages",
  "À relire": "To review",
  "Score le plus bas": "Lowest score",
  "Sous {score}": "Below {score}",
  "Aucun passage traduit à noter pour l’instant.": "No translated passage to score yet.",
  "Répartition des scores": "Score distribution",
  "{band} : {count} passages": "{band}: {count} passages",
  Bon: "Good",
  Correct: "Fair",
  Faible: "Weak",
  Insuffisant: "Poor",
  "À relire en priorité": "Review these first",
  "Les passages sous {score} que personne n’a validés, du plus faible au plus solide.":
    "Passages below {score} that nobody validated, weakest first.",
  "Aucun passage ne demande de relecture prioritaire.": "No passage needs a priority review.",
  "Ouvrir dans l’éditeur": "Open in the editor",
  "Ouvrir le passage {position} dans l’éditeur": "Open passage {position} in the editor",
  "Chapitres, du plus faible au plus solide": "Chapters, weakest first",
  "{shown} chapitres affichés sur {total}.": "{shown} of {total} chapters shown.",
  Chapitre: "Chapter",
  Volume: "Volume",
  Moyenne: "Average",
  Minimum: "Lowest",
  Faibles: "Weak",
  Notés: "Scored",
  "Volumes de la série": "Volumes of the series",
  "Pas encore de score": "No score yet",
  "{count} passage noté": "{count} scored passage",
  "{count} passages notés": "{count} scored passages",
  "Échec de traduction": "Translation failed",
  "Terme verrouillé non respecté": "Locked term not respected",
  "Alerte bloquante": "Blocking alert",
  "Alerte à vérifier": "Alert to check",
  "Critique de relecture ouverte": "Open review critique",
  "Doute du modèle": "Model doubt",
  "Longueur inhabituelle": "Unusual length",
  "Appels au modèle échoués": "Failed model calls",
  "Récupération automatique": "Automatic recovery",
  "Points clos sans correction": "Points closed without correction",
  "Arbitrage du pilote automatique": "Autopilot arbitration",
  "Erreur notée sur le passage": "Error noted on the passage",
  "Validé par une personne": "Validated by a person",
});

export interface QualitySignal {
  code: string;
  count: number;
  penalty: number;
}

export interface QualitySummary {
  scored: number;
  average: number | null;
  minimum: number | null;
  bands: Record<string, number>;
  histogram: number[];
  to_review: number;
  review_below: number;
}

interface ReviewItem {
  segment_id: string;
  project_id: string;
  project_title: string;
  chapter_id: string;
  chapter_title: string;
  position: number;
  score: number;
  band: string;
  signals: QualitySignal[];
  excerpt: string;
}

interface ChapterRow {
  chapter_id: string;
  project_id: string;
  project_title: string;
  volume_number: number | null;
  title: string;
  passages: number;
  scored: number;
  average: number;
  minimum: number;
  weak: number;
}

interface VolumeRow {
  project_id: string;
  title: string;
  volume_number: number | null;
  scored: number;
  average: number | null;
  minimum: number | null;
  weak: number;
}

interface Dashboard {
  summary: QualitySummary;
  chapters: ChapterRow[];
  chapters_total: number;
  review_first: ReviewItem[];
  volumes?: VolumeRow[];
}

const BANDS: { band: string; label: string; tone: Tone }[] = [
  { band: "good", label: "Bon", tone: "success" },
  { band: "fair", label: "Correct", tone: "info" },
  { band: "weak", label: "Faible", tone: "warning" },
  { band: "poor", label: "Insuffisant", tone: "danger" },
];

const SIGNALS: Record<string, string> = {
  source_retained: "Original conservé",
  failed: "Échec de traduction",
  locked_term: "Terme verrouillé non respecté",
  alert_error: "Alerte bloquante",
  alert_warning: "Alerte à vérifier",
  critique: "Critique de relecture ouverte",
  doubt: "Doute du modèle",
  length_ratio: "Longueur inhabituelle",
  retry: "Appels au modèle échoués",
  recovery: "Récupération automatique",
  open_points_closed: "Points clos sans correction",
  arbitration: "Arbitrage du pilote automatique",
  error_note: "Erreur notée sur le passage",
  validated: "Validé par une personne",
};

export function bandTone(band: string): Tone {
  return BANDS.find((item) => item.band === band)?.tone || "neutral";
}

/** A passage score as a soft badge: the band's tone, the number, and the band named for screen readers. */
export function ScoreBadge({ score, band, signals = [] }: { score: number; band: string; signals?: QualitySignal[] }) {
  const { t } = useI18n();
  const label = BANDS.find((item) => item.band === band)?.label || band;
  const why = signals
    .filter((signal) => signal.penalty > 0)
    .map((signal) => `${t(SIGNALS[signal.code] || signal.code)} (−${signal.penalty})`)
    .join(" · ");
  return (
    <Badge tone={bandTone(band)} title={why || undefined}>
      <span className="sr-only">{t("Score de qualité")} </span>
      <span className="tabular">{score}</span>
      <span className="sr-only"> {t(label)}</span>
    </Badge>
  );
}

export function SignalList({ signals }: { signals: QualitySignal[] }) {
  const { t } = useI18n();
  const shown = signals.filter((signal) => signal.penalty > 0);
  if (!shown.length) return null;
  return (
    <ul className="quality-signals">
      {shown.map((signal) => (
        <li key={signal.code}>
          {t(SIGNALS[signal.code] || signal.code)}
          {signal.count > 1 ? ` ×${signal.count}` : ""} <span className="subtle tabular">−{signal.penalty}</span>
        </li>
      ))}
    </ul>
  );
}

/** Share of the passages in each band: a stacked bar, with a legend that gives every count in text. */
export function BandDistribution({ summary }: { summary: QualitySummary }) {
  const { t } = useI18n();
  if (!summary.scored) return null;
  return (
    <div className="quality-distribution">
      <div className="quality-bar" role="img" aria-label={t("Répartition des scores")}>
        {BANDS.filter((item) => summary.bands[item.band]).map((item) => (
          <span
            key={item.band}
            className={`quality-bar-part tone-${item.tone}`}
            style={{ flexGrow: summary.bands[item.band] }}
            title={t("{band} : {count} passages", { band: t(item.label), count: summary.bands[item.band] })}
          />
        ))}
      </div>
      <ul className="quality-legend">
        {BANDS.map((item) => (
          <li key={item.band}>
            <span className={`quality-swatch tone-${item.tone}`} aria-hidden="true" />
            {t(item.label)} <span className="tabular">{formatNumber(summary.bands[item.band] || 0)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function QualityStats({ summary }: { summary: QualitySummary }) {
  const { t } = useI18n();
  return (
    <div className="stat-grid">
      <Stat
        label={t("Score moyen")}
        value={summary.average === null ? "—" : formatNumber(summary.average, { maximumFractionDigits: 1 })}
      />
      <Stat label={t("Passages notés")} value={formatNumber(summary.scored)} />
      <Stat
        label={t("À relire")}
        value={formatNumber(summary.to_review)}
        hint={t("Sous {score}", { score: summary.review_below })}
        tone={summary.to_review ? "warning" : undefined}
      />
      <Stat label={t("Score le plus bas")} value={summary.minimum ?? "—"} />
    </div>
  );
}

/**
 * Quality dashboard of a volume (`scope="project"`) or a series: summary, distribution, the passages to
 * review first (each opens in the editor) and the chapters ranked weakest first.
 */
export function QualityDashboard({
  scope,
  id,
  run,
  tick = 0,
  onOpenPassage,
}: {
  scope: "project" | "series";
  id: string;
  run: Run;
  tick?: number;
  onOpenPassage?: (segmentId: string, projectId: string) => void;
}) {
  const { t, tp } = useI18n();
  const [data, setData] = useState<Dashboard | null>(null);
  useEffect(() => {
    let active = true;
    void run.background(async () => {
      const next = await api<Dashboard>(`/${scope === "project" ? "projects" : "series"}/${id}/quality`);
      if (active) setData(next);
    });
    return () => {
      active = false;
    };
  }, [scope, id, run, tick]);
  if (!data) return <LoadingBlock label={t("Chargement des scores…")} lines={4} />;
  if (!data.summary) return null; // a server without passage scores
  const { summary } = data;
  const series = scope === "series";
  const open = (item: ReviewItem) =>
    onOpenPassage
      ? onOpenPassage(item.segment_id, item.project_id)
      : (location.hash = `project/${item.project_id}/passage/${item.segment_id}`);
  return (
    <div className="stack quality-dashboard">
      <Card
        title={t("Score de qualité des passages")}
        description={t(
          "Chaque passage traduit reçoit un score de 0 à 100 calculé à partir des signaux déjà enregistrés (contrôles automatiques, critiques, doutes, échecs, récupérations) ; il oriente la relecture sans mesurer seul la qualité littéraire.",
        )}
      >
        {summary.scored ? (
          <div className="stack">
            <QualityStats summary={summary} />
            <BandDistribution summary={summary} />
          </div>
        ) : (
          <EmptyState compact icon="chart" title={t("Aucun passage traduit à noter pour l’instant.")} />
        )}
      </Card>
      {!!summary.scored && (
        <Card
          padded={false}
          title={t("À relire en priorité")}
          description={t("Les passages sous {score} que personne n’a validés, du plus faible au plus solide.", {
            score: summary.review_below,
          })}
        >
          {data.review_first.length ? (
            <ul className="series-list quality-review">
              {data.review_first.map((item) => (
                <li key={item.segment_id} className="series-list-item">
                  <ScoreBadge score={item.score} band={item.band} signals={item.signals} />
                  <div className="series-list-main">
                    <strong>
                      {series ? `${item.project_title} · ` : ""}
                      {item.chapter_title} · § {item.position + 1}
                    </strong>
                    <span className="book-excerpt muted">{item.excerpt}</span>
                    <SignalList signals={item.signals} />
                  </div>
                  <div className="series-list-actions">
                    <Button
                      icon="edit"
                      aria-label={t("Ouvrir le passage {position} dans l’éditeur", { position: item.position + 1 })}
                      onClick={() => open(item)}
                    >
                      {t("Ouvrir dans l’éditeur")}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState compact icon="check" title={t("Aucun passage ne demande de relecture prioritaire.")} />
          )}
        </Card>
      )}
      {!!data.chapters.length && (
        <Card
          padded={false}
          title={t("Chapitres, du plus faible au plus solide")}
          description={
            data.chapters_total > data.chapters.length
              ? t("{shown} chapitres affichés sur {total}.", {
                  shown: data.chapters.length,
                  total: data.chapters_total,
                })
              : undefined
          }
        >
          <Table label={t("Chapitres, du plus faible au plus solide")}>
            <thead>
              <tr>
                {series && <th>{t("Volume")}</th>}
                <th>{t("Chapitre")}</th>
                <th className="num">{t("Moyenne")}</th>
                <th className="num">{t("Minimum")}</th>
                <th className="num">{t("Faibles")}</th>
                <th className="num">{t("Notés")}</th>
              </tr>
            </thead>
            <tbody>
              {data.chapters.map((chapter) => (
                <tr key={chapter.chapter_id}>
                  {series && <td className="muted">{chapter.project_title}</td>}
                  <td>{chapter.title}</td>
                  <td className="num tabular">{formatNumber(chapter.average, { maximumFractionDigits: 1 })}</td>
                  <td className="num tabular">{chapter.minimum}</td>
                  <td className="num tabular">{chapter.weak}</td>
                  <td className="num tabular">
                    {chapter.scored} / {chapter.passages}
                  </td>
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      )}
      {series && !!data.volumes?.length && (
        <Card padded={false} title={t("Volumes de la série")}>
          <ul className="series-list">
            {data.volumes.map((volume) => (
              <li key={volume.project_id} className="series-list-item">
                <div className="series-list-main">
                  <a href={`#project/${volume.project_id}`}>{volume.title}</a>
                  <span className="subtle">
                    {volume.scored
                      ? tp(volume.scored, "{count} passage noté", "{count} passages notés")
                      : t("Pas encore de score")}
                  </span>
                </div>
                {volume.average !== null && (
                  <div className="series-list-actions">
                    <Badge tone={volume.weak ? "warning" : "success"}>
                      {t("Moyenne")} {formatNumber(volume.average, { maximumFractionDigits: 1 })}
                    </Badge>
                    {!!volume.weak && (
                      <Badge tone="warning">
                        {t("Faibles")} {volume.weak}
                      </Badge>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </Card>
      )}
    </div>
  );
}
