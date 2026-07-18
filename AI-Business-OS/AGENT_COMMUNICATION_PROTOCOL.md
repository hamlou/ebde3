# ==========================================================
# AI BUSINESS OS
# AGENT COMMUNICATION PROTOCOL
# Version 1.0
# ==========================================================

## PURPOSE
Define how agents communicate. Every interaction must be: Structured, Traceable, Understandable, Reviewable.

---

## MESSAGE STRUCTURE
Every agent message follows:
```json
{
    "sender": "",
    "receiver": "",
    "objective": "",
    "context": "",
    "input_data": "",
    "requested_action": "",
    "confidence": "",
    "expected_output": "",
    "deadline": ""
}
```

---

## AGENT HANDOFF RULE
An agent cannot simply say "Done."
Every completed task must include:
- Summary (What was done?)
- Evidence (Why should this be trusted?)
- Results (What changed?)
- Risks (What could go wrong?)
- Recommendation (What should happen next?)

---

## DEPARTMENT FLOWS

**RESEARCH → BUSINESS ANALYST**
MI sends: Opportunity, Problem, Customer, Evidence, Competition, Market size, Confidence.
BA returns: Financial model, Risk, ROI, Recommendation.

**BUSINESS → PRODUCT**
BA sends: Approved opportunity, Customer, Problem, Revenue model, Success criteria.
Product returns: MVP, Features, Timeline, Requirements.

**PRODUCT → ENGINEERING**
Product sends: Product specification, User flows, Priority, Acceptance criteria.
Engineering returns: Architecture, Implementation, Risks, Progress.

**ENGINEERING → QA**
Engineering sends: Build, Changes, Testing completed.
QA returns: Approved, Problems, Required fixes.

**PRODUCT → GROWTH**
Product sends: Value proposition, Target audience, Benefits, Positioning.
Growth returns: Channels, Experiments, Acquisition plan.

**GROWTH → SALES**
Growth sends: Audience, Messaging, Lead sources.
Sales returns: Customer objections, Conversion data, Feedback.

**ALL AGENTS → MEMORY**
Important info sent to Memory Manager.
Format: Information, Category, Source, Confidence, Future value.

---

## CONFLICT RESOLUTION
1. Present arguments.
2. Compare evidence.
3. Devil's Advocate reviews.
4. Executive Reviewer decides.

---

## CONFIDENCE SYSTEM
90-100: Strong evidence.
70-89: Reasonable confidence.
50-69: Needs testing.
Below 50: Do not act without more research.

---

## TRACEABILITY
Records: Agent, Time, Decision, Reason, Tools, Output, Next step.
