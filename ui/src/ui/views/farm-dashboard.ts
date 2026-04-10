import { html, nothing, type TemplateResult } from "lit";
import { renderFarmKpiCards } from "./farm-kpi-cards.ts";
import { getUniqueFieldIds, getUniqueSoilTypes, getFarmSummary } from "../farm-data.ts";

export type FarmDashboardProps = {
  selectedFieldId: string | null;
  selectedSoilType: string | null;
  onFieldIdChange: (fieldId: string | null) => void;
  onSoilTypeChange: (soilType: string | null) => void;
};

function renderNdviAlert(avgNdvi: number): TemplateResult {
  if (avgNdvi < 0.5) {
    return html`
      <div class="farm-alert callout warning" role="alert">
        Alert: low vegetation index detected. Recommend immediate scouting for nitrogen deficiency or pest pressure.
      </div>
    `;
  }
  return nothing;
}

function renderFilterSelect(
  label: string,
  value: string | null,
  options: string[],
  onChange: (value: string | null) => void,
): TemplateResult {
  return html`
    <div class="farm-filter">
      <label class="farm-filter__label">${label}</label>
      <select
        class="farm-filter__select"
        @change=${(e: Event) => {
          const target = e.target as HTMLSelectElement;
          onChange(target.value === "all" ? null : target.value);
        }}
      >
        <option value="all" ?selected=${value === null}>All</option>
        ${options.map(
          (opt) => html`<option value=${opt} ?selected=${value === opt}>${opt}</option>`,
        )}
      </select>
    </div>
  `;
}

export function renderFarmDashboard(props: FarmDashboardProps) {
  console.log('🚜 renderFarmDashboard called with:', {
    selectedFieldId: props.selectedFieldId,
    selectedSoilType: props.selectedSoilType
  });
  
  const fieldIds = getUniqueFieldIds();
  const soilTypes = getUniqueSoilTypes();
  const summary = getFarmSummary(props.selectedFieldId, props.selectedSoilType);
  
  console.log('🚜 Farm summary:', summary);

  return html`
    <div class="farm-dashboard">
      <header class="farm-dashboard__header">
        <h2 class="farm-dashboard__title">Farm Overview</h2>
        <div class="farm-dashboard__filters">
          ${renderFilterSelect(
            "Field ID",
            props.selectedFieldId,
            fieldIds,
            props.onFieldIdChange,
          )}
          ${renderFilterSelect(
            "Soil Type",
            props.selectedSoilType,
            soilTypes,
            props.onSoilTypeChange,
          )}
        </div>
      </header>
      ${renderNdviAlert(summary.avgNdvi)}
      ${renderFarmKpiCards({
        selectedFieldId: props.selectedFieldId,
        selectedSoilType: props.selectedSoilType,
      })}
    </div>
  `;
}
