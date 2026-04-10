import { html, type TemplateResult } from "lit";
import { getFarmSummary, formatAcres, formatBushels, formatNdvi } from "../farm-data.ts";

export type FarmKpiCardsProps = {
  selectedFieldId: string | null;
  selectedSoilType: string | null;
};

type KpiCard = {
  kind: string;
  label: string;
  value: string;
  hint: string;
};

function renderKpiCard(card: KpiCard): TemplateResult {
  return html`
    <div class="farm-kpi-card">
      <span class="farm-kpi-card__label">${card.label}</span>
      <span class="farm-kpi-card__value">${card.value}</span>
      <span class="farm-kpi-card__hint">${card.hint}</span>
    </div>
  `;
}

export function renderFarmKpiCards(props: FarmKpiCardsProps) {
  const summary = getFarmSummary(props.selectedFieldId, props.selectedSoilType);

  const cards: KpiCard[] = [
    {
      kind: "ndvi",
      label: "Average NDVI",
      value: formatNdvi(summary.avgNdvi),
      hint: `${summary.fieldCount} field${summary.fieldCount !== 1 ? "s" : ""} in view`,
    },
    {
      kind: "acres",
      label: "Total Acreage",
      value: formatAcres(summary.totalAcres),
      hint: "across filtered fields",
    },
    {
      kind: "bushels",
      label: "Predicted Bushels",
      value: formatBushels(summary.totalBushels),
      hint: "OSU INSEY model",
    },
  ];

  return html`
    <section class="farm-kpi-cards">
      ${cards.map((card) => renderKpiCard(card))}
    </section>
  `;
}
