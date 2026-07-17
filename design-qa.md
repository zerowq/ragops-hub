# Design QA

## Evidence

- Source visual truth: `/Users/anqi.liu/.codex/generated_images/019f6daf-8c20-7511-8de9-f32b293b2012/call_upIqtixkVjCT2DGO6jGTlDTJ.png`
- Browser-rendered implementation: `/tmp/ragops-redesign/implementation-qa-final.jpg`
- Normalized full-view comparison: `/tmp/ragops-redesign/qa-comparison-final.jpg`
- Focused conversation and business-context comparison: `/tmp/ragops-redesign/qa-focused-final.jpg`
- URL: `http://127.0.0.1:8000/`
- Viewport: `1280 × 720`; the source was top-cropped to the same 16:9 region and normalized to the same pixel size before comparison.
- State: `CASE-1001` selected, customer and order context loaded, a knowledge answer with four live citations shown, and a ticket Pending Action waiting for human confirmation.

## Primary interactions tested

- Selected an assigned support case and loaded customer, order, entitlement and pending-action context.
- Ran a Hybrid RAG question through SSE and opened a live citation source preview.
- Prepared and cancelled a ticket Pending Action; automated tests separately verified confirmation, idempotent creation and Case/Order/Customer linkage.
- Opened Knowledge Operations, loaded three documents, ran retrieval acceptance and received four hits.
- Opened Runtime & Evaluation, loaded six live metrics, service health and eight recent audit events.
- Checked browser console errors after the complete flow: none.

## Findings

No actionable P0, P1 or P2 differences remain.

- Typography: the implementation uses the platform UI font stack with comparable hierarchy, weight and compact small-text density. Chinese wrapping and truncation remain readable at the target viewport.
- Spacing and layout rhythm: the three-column queue / conversation / context hierarchy, sticky header, panel boundaries, compact cards and bottom composer match the source composition. Internal scrolling prevents message growth from pushing persistent regions off the page.
- Colors and tokens: teal primary actions, cool neutral surfaces, thin borders and restrained semantic red/yellow/green states align with the reference palette and maintain readable contrast.
- Image and asset fidelity: the source does not depend on product photography or illustration. The implementation does not substitute source artwork with generated or hand-drawn assets; avatars are data-driven initials. The earlier invented brand tile was removed.
- Copy and content: labels were adapted from a generic mock to the implemented B2B SaaS after-sales scenario. Customer, order, entitlement, evidence and ticket copy all comes from real demo APIs or live Agent events.

## Comparison history

### Iteration 1

- Earlier finding [P1]: the visible “准备工单” action used wording that the deterministic router classified as a knowledge request, so the core confirmation state did not appear.
- Fix: changed the action payload to an explicit “创建工单” request while preserving the user-facing “准备工单” label.
- Post-fix evidence: browser flow displayed the Pending Action details and `确认创建` / `取消` controls; cancellation returned the page to the initial state.

### Iteration 2

- Earlier finding [P2]: after a long answer, the conversation flex child could expand the page and shift the header and queue headings above the viewport.
- Fix: added `min-height: 0`, bounded overflow on the workbench and conversation column, and locked document scrolling while the workbench view is active.
- Earlier finding [P2]: an invented `R` brand tile drifted from the text-only source brand and acted as an unnecessary substitute asset.
- Fix: removed the tile and retained the source-aligned text brand.
- Post-fix evidence: `/tmp/ragops-redesign/qa-comparison-final.jpg` and `/tmp/ragops-redesign/qa-focused-final.jpg` show stable three-column proportions, visible primary regions and source-aligned branding in the matched state.

## Open questions

- The reference includes a separate uncertainty notice and suggested-reply block. The implementation intentionally keeps the grounded answer and live citations in one auditable message because the local extractive mode does not expose calibrated confidence. This is treated as a product constraint, not visual drift.

## Follow-up polish

- [P3] When a production model exposes calibrated confidence and answer-policy metadata, add the reference-style uncertainty notice and separate editable reply draft.
- [P3] Replace short source filenames in citation chips with knowledge-base display titles when the content connector supplies them.

## Implementation checklist

- [x] Match three-column information architecture and visual hierarchy.
- [x] Make queue, navigation, composer, citations and ticket confirmation functional.
- [x] Verify desktop layout after long streamed content.
- [x] Verify knowledge and operations views with live APIs.
- [x] Check console errors and automated regression tests.

final result: passed
