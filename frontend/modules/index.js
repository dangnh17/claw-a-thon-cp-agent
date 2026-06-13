// ── Module manifest ───────────────────────────────────────────────────────────
//
// Each team member:
//   1. Creates  frontend/modules/feature-x.js  (exports a Module object)
//   2. Adds ONE import line below + ONE entry to the array
//
// Git conflict here = add your line back. That's it.
// ─────────────────────────────────────────────────────────────────────────────

import dataIngest from "./data-ingest.js";   // shared data tool (always first)
import featureA   from "./feature-a.js";     // member 1
// import featureB from "./feature-b.js";    // member 2  ← uncomment when ready
// import featureC from "./feature-c.js";    // member 3

export default [
  dataIngest,   // shared — always visible
  featureA,
  // featureB,
  // featureC,
];
