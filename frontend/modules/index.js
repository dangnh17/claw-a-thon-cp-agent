// ── Module manifest ───────────────────────────────────────────────────────────
//
// Each team member:
//   1. Creates  frontend/modules/feature-x.js  (exports a Module object)
//   2. Adds ONE import line below + ONE entry to the array
//
// Git conflict here = add your line back. That's it.
// ─────────────────────────────────────────────────────────────────────────────

import featureA        from "./feature-a.js";         // member 1
import funnelAnalysis  from "./funnel-analysis.js";    // funnel analysis
import debugInvestigator from "./debug-investigator.js"; // debug investigator
import journeyInsight  from "./journey-insight.js";    // journey insight

export default [
  funnelAnalysis,
  debugInvestigator,
  journeyInsight,
];
