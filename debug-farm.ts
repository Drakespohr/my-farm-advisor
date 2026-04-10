// Simple test to verify farm data and dashboard function
import { getFarmSummary } from './ui/src/ui/farm-data.ts';

// Test the farm summary calculation
console.log('=== FARM DATA TEST ===');
const allFieldsSummary = getFarmSummary(null, null);
console.log('All fields summary:', allFieldsSummary);
console.log('Average NDVI:', allFieldsSummary.avgNdvi);
console.log('Below 0.5 threshold?', allFieldsSummary.avgNdvi < 0.5);

// Test individual fields that should definitely trigger alert
console.log('\n=== INDIVIDUAL FIELD TESTS ===');
const lowNdviFields = ['field_001', 'field_002', 'field_003'];
lowNdviFields.forEach(fieldId => {
  const summary = getFarmSummary(fieldId, null);
  console.log(`${fieldId}: NDVI = ${summary.avgNdvi.toFixed(3)}, Below 0.5? ${summary.avgNdvi < 0.5}`);
});

// Test a higher NDVI field
console.log('\n=== HIGHER NDVI FIELD TEST ===');
const higherNdviField = getFarmSummary('field_006', null);
console.log(`field_006: NDVI = ${higherNdviField.avgNdvi.toFixed(3)}, Below 0.5? ${higherNdviField.avgNdvi < 0.5}`);