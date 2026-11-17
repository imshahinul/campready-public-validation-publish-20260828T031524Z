# CampReady Phase 5-5 bounded internal review-only pilot protocol

**Frozen version:** `campready.phase5.pilot_protocol.v1` / 1.0.0. This document and its machine-readable companions are immutable after freeze. Thresholds cannot be changed after outcomes are seen. This checkpoint does not execute the pilot.

## Governance and boundary

Phase 5-4 did not pass its original two-human qualification gate.

Phase 5-5 proceeded under explicit owner governance override.

The immutable Phase 5-4 result is `CLOSE_PHASE5_4_HOLD_FINAL_HUMAN_QUALIFICATION_GATE_NOT_MET`; the predecessor is `PASS_PHASE5_5A_OWNER_OVERRIDE_BOUNDED_PILOT_AUTHORIZED`. R-I's final result remains PASS. R-J's Y507 result remains a genuine individual comprehension error. The pilot is internal, review-only, human-mediated, bounded, non-consumer-facing, non-notifying, and non-delivering. It tests operation of the frozen review workflow, not consumer behavior, public usability, notification effectiveness, trip safety, road passability, or national scale.

It authorizes no pilot execution, runtime change, notification, delivery, public user, public launch, safety/passability claim, national expansion, or new event category. A separate Phase 5-5C execution-readiness decision is required to start.

## Cohort, observation, and finite window

The cohort is exactly the ten records copied from `campready-phase5/config.json` into `protocol.json`: Andrews Cove, Low Gap, Van Hook Glade, Big Rock, Spinreel, Cold Springs, Carolina Beach State Park, Stone Mountain Family, Elkmont, and Big Meadows. Destination authority families are only USFS, NC_STATE_PARKS, and NPS; source roles are ACTIVE_ALERT, CAMPGROUND_PAGE, and ALERT_DETAIL; existing semantic event categories are WEATHER_ALERT and CAMPGROUND_CONDITION. Quality-only observations keep a null category and make no semantic claim. No destination, authority family, source role, geography, or event category may be added.

One observation is one review item shown to the primary reviewer through the frozen workflow, plus source evidence, system classification, guard/quality state, disposition, applicable acknowledgement, timing, correction/disagreement, and attributable maintenance impact. A fetch, page, campground, scheduler run, or secondary review alone is not another observation.

A later execution authorization fixes `pilot_started_at`. Prospective collection ends at the first of: 100 valid observations; 336 hours after that timestamp; or an earlier STOP/HOLD. Nothing before the timestamp is backfilled. Historical evidence is context only.

## Review and blinded control

Exactly two internal reviewers and one separate owner adjudicator are bound to stable blinded codes before execution. PRIMARY_REVIEWER reviews every item. SECOND_REVIEWER independently reviews intake sequence 5, 10, 15, and so on, without the first outcome, for at most 20 selected items. This deterministic selection occurs before outcomes and cannot be cherry-picked. OWNER_ADJUDICATOR classifies disagreements and structured incompatibilities while preserving both originals. This is routine operation, not the qualification rule requiring two independent passes on every item.

## Error, discretion, correction, and Y507

An error is a bounded-adjudication-confirmed incompatibility between structured facts/the frozen workflow contract and a reviewer's recorded action. It maps to a frozen error class in `protocol.json`. Different permitted actions are `ALLOWED_DISCRETIONARY_DIFFERENCE`, not errors. Parser, source, transport, and retrieval defects are not human comprehension errors.

`unknown_none_dq_hold_conflict` is true exactly when relevance is UNKNOWN, quality defect basis is NONE, and disposition is DATA_QUALITY_HOLD; otherwise it is false. It is audit logic only. UNKNOWN remains distinct from DATA_QUALITY and neither reviewer nor runtime state is rewritten.

The disposition-comprehension rate numerator is observations with an adjudicated confirmed structured disposition incompatibility (maximum one per observation). Its denominator is observations with a primary disposition. A zero denominator is NOT_MEASURABLE. Corrections append evidence, preserve the original, and remain counted; correction never erases an error.

## STOP, HOLD, and interpretation

STOP immediately for notification/delivery activation, unexpected canonical semantic write, a guard bypass permitting prohibited transition, any trip-safety or road-passability endorsement, source-silence safety/resolution inference, an uncontained system false lifecycle event accepted as truth, material protocol/data corruption, or any unsafe inference.

The first contained and corrected UNKNOWN/NONE to DATA_QUALITY_HOLD error is counted but does not alone stop the pilot. Two distinct-item occurrences cause `PILOT_HOLD_REVIEWER_COMPREHENSION`. The same two-distinct-item HOLD rule applies separately to guard-comprehension errors, acknowledgement-workflow errors, and the combined source/system/reviewer-boundary category. Guard bypass and unsafe inference remain immediate STOP conditions and do not wait for repetition.

One isolated contained reviewer mistake is not automatically a system-safety failure. Corrected errors count; allowed discretion does not. No percentage alone decides closeout. Product/runtime, source/retrieval, reviewer comprehension, workload, and maintenance are separate dimensions. Source silence and guarded failure never resolve semantics; acknowledgement never means safety, notification approval, or resolution.

## Accounting, retention, and completion

Report guard, UNKNOWN/DATA_QUALITY, acknowledgement, disagreement, correction, workload, scheduler, retrieval, parser, and source/role measures listed in `protocol.json`; never create a composite health score. Ordinary review, adjudication, and correction time are reviewer workload. Only existing-ledger `OPERATIONAL_MAINTENANCE` needed to keep the pilot functioning is maintenance. Development/debugging, feature work, protocol/qualification design, research, and historical audit are excluded and separately reported.

All observations, original annotations, secondary reviews, corrections, adjudications, maintenance rows, and closeout metrics are append-only immutable evidence with no consumer-identifying data. A wrong original is never overwritten.

Normal closeout requires the finite window, selected controls, structural validity, integrity, metrics, guard/acknowledgement accounting, maintenance ledger, and historical statements to be complete. Outcomes are `PILOT_CONTINUE_TO_CLOSEOUT`, `PILOT_HOLD_REVIEWER_COMPREHENSION`, `PILOT_HOLD_OPERATIONAL_BURDEN`, `PILOT_HOLD_SOURCE_RELIABILITY`, `PILOT_STOP_SAFETY_BOUNDARY`, or `PILOT_INVALID_INSUFFICIENT_EVIDENCE`. A clean closeout means only that bounded internal review operation was acceptable under this protocol.
