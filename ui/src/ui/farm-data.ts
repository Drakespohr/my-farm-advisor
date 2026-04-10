import { toNumber } from "./format.ts";

export interface FarmField {
  field_id: string;
  latitude: number;
  longitude: number;
  area_acres: number;
  soil_series: string;
  surface_texture: string;
  hydrologic_group: string;
  om_pct: number;
  ph: number;
  drainage_class: string;
  soil_health_score: number;
  mean_ndvi: number;
  yield_bu_acre: number;
  predicted_bushels: number;
}

export interface FarmSummary {
  totalAcres: number;
  avgNdvi: number;
  totalBushels: number;
  fieldCount: number;
}

const FARM_DATA: FarmField[] = [
  { field_id: "field_001", latitude: 40.92336, longitude: -96.3735, area_acres: 322.55, soil_series: "Kennebec", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.72, ph: 6.29, drainage_class: "Well drained", soil_health_score: 0.852, mean_ndvi: 0.121, yield_bu_acre: 147.89, predicted_bushels: 47701.43 },
  { field_id: "field_002", latitude: 40.92459, longitude: -96.38387, area_acres: 146.66, soil_series: "Kennebec", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.72, ph: 6.29, drainage_class: "Well drained", soil_health_score: 0.852, mean_ndvi: 0.108, yield_bu_acre: 152.73, predicted_bushels: 22399.34 },
  { field_id: "field_003", latitude: 40.91862, longitude: -96.36465, area_acres: 102.74, soil_series: "Kennebec", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 2.42, ph: 6.22, drainage_class: "Well drained", soil_health_score: 0.937, mean_ndvi: 0.106, yield_bu_acre: 153.57, predicted_bushels: 15777.07 },
  { field_id: "field_004", latitude: 40.95425, longitude: -96.0716, area_acres: 290.57, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 3.12, ph: 6.69, drainage_class: "Poorly drained", soil_health_score: 0.76, mean_ndvi: 0.160, yield_bu_acre: 134.16, predicted_bushels: 38981.52 },
  { field_id: "field_005", latitude: 41.01814, longitude: -96.07927, area_acres: 70.66, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.95, ph: 6.30, drainage_class: "Well drained", soil_health_score: 0.866, mean_ndvi: 0.135, yield_bu_acre: 142.55, predicted_bushels: 10071.62 },
  { field_id: "field_006", latitude: 41.09862, longitude: -95.95562, area_acres: 21.14, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.99, ph: 5.90, drainage_class: "Well drained", soil_health_score: 0.718, mean_ndvi: 0.475, yield_bu_acre: 61.03, predicted_bushels: 1290.17 },
  { field_id: "field_007", latitude: 40.91599, longitude: -96.16805, area_acres: 20.54, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.72, ph: 6.29, drainage_class: "Well drained", soil_health_score: 0.852, mean_ndvi: 0.387, yield_bu_acre: 75.97, predicted_bushels: 1560.20 },
  { field_id: "field_008", latitude: 40.92105, longitude: -96.15956, area_acres: 2.08, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 2.42, ph: 6.22, drainage_class: "Well drained", soil_health_score: 0.937, mean_ndvi: 0.446, yield_bu_acre: 65.52, predicted_bushels: 136.23 },
  { field_id: "field_009", latitude: 40.90879, longitude: -96.13887, area_acres: 16.36, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.75, ph: 6.25, drainage_class: "Moderately well drained", soil_health_score: 0.857, mean_ndvi: 0.446, yield_bu_acre: 65.52, predicted_bushels: 1072.00 },
  { field_id: "field_010", latitude: 40.89795, longitude: -96.12402, area_acres: 15.12, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 2.42, ph: 6.22, drainage_class: "Well drained", soil_health_score: 0.937, mean_ndvi: 0.443, yield_bu_acre: 66.11, predicted_bushels: 999.38 },
  { field_id: "field_011", latitude: 40.94431, longitude: -96.17363, area_acres: 11.45, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 3.04, ph: 5.91, drainage_class: "Well drained", soil_health_score: 0.84, mean_ndvi: 0.434, yield_bu_acre: 67.65, predicted_bushels: 774.86 },
  { field_id: "field_012", latitude: 40.93453, longitude: -96.18008, area_acres: 18.36, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.72, ph: 6.29, drainage_class: "Well drained", soil_health_score: 0.852, mean_ndvi: 0.434, yield_bu_acre: 67.65, predicted_bushels: 1242.34 },
  { field_id: "field_013", latitude: 40.92953, longitude: -96.15986, area_acres: 3.07, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.72, ph: 6.29, drainage_class: "Well drained", soil_health_score: 0.852, mean_ndvi: 0.434, yield_bu_acre: 67.65, predicted_bushels: 207.85 },
  { field_id: "field_014", latitude: 40.98824, longitude: -96.14299, area_acres: 11.16, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.95, ph: 6.30, drainage_class: "Well drained", soil_health_score: 0.866, mean_ndvi: 0.441, yield_bu_acre: 66.44, predicted_bushels: 741.20 },
  { field_id: "field_015", latitude: 41.09400, longitude: -96.13452, area_acres: 22.31, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.74, ph: 6.05, drainage_class: "Well drained", soil_health_score: 0.834, mean_ndvi: 0.441, yield_bu_acre: 66.44, predicted_bushels: 1482.21 },
  { field_id: "field_016", latitude: 41.07218, longitude: -96.15475, area_acres: 7.38, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.55, ph: 6.38, drainage_class: "Well drained", soil_health_score: 0.807, mean_ndvi: 0.441, yield_bu_acre: 66.44, predicted_bushels: 490.27 },
  { field_id: "field_017", latitude: 41.09578, longitude: -96.12171, area_acres: 9.47, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.74, ph: 6.05, drainage_class: "Well drained", soil_health_score: 0.834, mean_ndvi: 0.441, yield_bu_acre: 66.44, predicted_bushels: 628.99 },
  { field_id: "field_018", latitude: 41.09085, longitude: -96.12964, area_acres: 31.41, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.74, ph: 6.05, drainage_class: "Well drained", soil_health_score: 0.834, mean_ndvi: 0.441, yield_bu_acre: 66.44, predicted_bushels: 2086.89 },
  { field_id: "field_019", latitude: 41.08993, longitude: -96.13256, area_acres: 8.34, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 2.68, ph: 6.42, drainage_class: "Moderately well drained", soil_health_score: 0.96, mean_ndvi: 0.432, yield_bu_acre: 67.99, predicted_bushels: 566.90 },
  { field_id: "field_020", latitude: 40.98836, longitude: -96.20018, area_acres: 6.10, soil_series: "Ely", surface_texture: "Silt loam", hydrologic_group: "B", om_pct: 1.70, ph: 6.35, drainage_class: "Well drained", soil_health_score: 0.829, mean_ndvi: 0.423, yield_bu_acre: 69.50, predicted_bushels: 424.09 },
];

export function getAllFields(): FarmField[] {
  return FARM_DATA;
}

export function getFieldsFiltered(fieldId: string | null, soilType: string | null): FarmField[] {
  let fields = FARM_DATA;
  if (fieldId && fieldId !== "all") {
    fields = fields.filter((f) => f.field_id === fieldId);
  }
  if (soilType && soilType !== "all") {
    fields = fields.filter((f) => f.surface_texture === soilType);
  }
  return fields;
}

export function getFarmSummary(fieldId: string | null = null, soilType: string | null = null): FarmSummary {
  const fields = getFieldsFiltered(fieldId, soilType);
  const totalAcres = fields.reduce((sum, f) => sum + f.area_acres, 0);
  const avgNdvi = fields.length > 0 ? fields.reduce((sum, f) => sum + f.mean_ndvi, 0) / fields.length : 0;
  const totalBushels = fields.reduce((sum, f) => sum + f.predicted_bushels, 0);
  return {
    totalAcres,
    avgNdvi,
    totalBushels,
    fieldCount: fields.length,
  };
}

export function getUniqueSoilTypes(): string[] {
  const textures = new Set(FARM_DATA.map((f) => f.surface_texture));
  return Array.from(textures).toSorted();
}

export function getUniqueFieldIds(): string[] {
  return FARM_DATA.map((f) => f.field_id);
}

export function formatAcres(acres: number): string {
  return toNumber(acres.toFixed(0), 0).toLocaleString();
}

export function formatBushels(bushels: number): string {
  return toNumber(bushels.toFixed(0), 0).toLocaleString();
}

export function formatNdvi(ndvi: number): string {
  return ndvi.toFixed(3);
}
