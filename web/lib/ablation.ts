/**
 * The policy ON / OFF contrast.
 *
 * `POLICY_ENABLED=False` is a real flag in the service (config.py). It routes
 * every action through `PolicyEngine.bypass()` instead of `evaluate()`, which
 * returns ALLOW with an EMPTY `evaluated` tuple and `policy_enabled=False` — so
 * the trail records, in the record itself, that nothing was checked. The
 * violation detector still runs, which is exactly what makes the two arms
 * comparable.
 *
 * WHAT THIS MODULE IS NOT
 * -----------------------
 * It is not a benchmark. Nothing here has been measured. Every number this
 * module returns is *derived* — it re-reads the same fixture actions and
 * reports what the rules say about them, which is arithmetic over the data on
 * this page and nothing more. The OFF column is what the flag would let
 * through, not an observation of it having been run.
 *
 * severity mapping is policy/models.py :: Severity:
 *   critical — an action a rule would have DENIED was executed anyway.
 *   high     — an action that required human approval was executed without it.
 */

import { ACTIONS, type Effect, type QueueAction, type Severity } from "./fixtures";

export type ArmOutcome = {
  action: QueueAction;
  /** What the engine decides with the guard on. */
  on: Effect;
  /** Whether the tool call actually reaches the billing API with the guard on. */
  onExecuted: boolean;
  /** With the guard off, every effectful action executes. Always true. */
  offExecuted: boolean;
  /** Violations the detector records against the OFF arm. Empty when the
   *  action was legal anyway — the guard is not what made those fine. */
  offViolations: { ruleId: string; severity: Severity; rationale: string }[];
  /** Money that moves in the OFF arm but not the ON arm. */
  moneyAtRisk: number;
};

export function armOutcome(a: QueueAction): ArmOutcome {
  const offending = a.evaluated.filter((o) => o.effect !== "allow");
  const offViolations = offending.map((o) => ({
    ruleId: o.ruleId,
    severity: (o.effect === "deny" ? "critical" : "high") as Severity,
    rationale: o.rationale,
  }));
  const onExecuted = a.effect === "allow";
  const moneyAtRisk = !onExecuted && a.tool === "issue_refund" ? (a.valueUsd ?? 0) : 0;
  return {
    action: a,
    on: a.effect,
    onExecuted,
    offExecuted: true,
    offViolations,
    moneyAtRisk,
  };
}

export function allOutcomes(): ArmOutcome[] {
  return ACTIONS.map(armOutcome);
}

export type AblationSummary = {
  total: number;
  /** ON arm */
  onExecuted: number;
  onHeld: number;
  onBlocked: number;
  /** OFF arm */
  offExecuted: number;
  offCritical: number;
  offHigh: number;
  offClean: number;
  /** Refund dollars the guard keeps from leaving in this fixture set. */
  refundDollarsHeld: number;
};

export function ablationSummary(): AblationSummary {
  const outs = allOutcomes();
  const critical = outs.filter((o) => o.offViolations.some((v) => v.severity === "critical"));
  const high = outs.filter(
    (o) => !o.offViolations.some((v) => v.severity === "critical") && o.offViolations.some((v) => v.severity === "high"),
  );
  return {
    total: outs.length,
    onExecuted: outs.filter((o) => o.on === "allow").length,
    onHeld: outs.filter((o) => o.on === "escalate").length,
    onBlocked: outs.filter((o) => o.on === "deny").length,
    offExecuted: outs.length,
    offCritical: critical.length,
    offHigh: high.length,
    offClean: outs.filter((o) => o.offViolations.length === 0).length,
    refundDollarsHeld: outs.reduce((s, o) => s + o.moneyAtRisk, 0),
  };
}
