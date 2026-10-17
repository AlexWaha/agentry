/**
 * i18n keys of the stage labels the server can send (build and plan pipelines). A stage name that is not
 * listed here (a project may add stages in pipeline.json) is shown as the raw name.
 */
export const STAGE_LABEL_KEYS: Record<string, string> = {
  plan: "shell.stage.plan",
  "plan-approval": "shell.stage.planApproval",
  implement: "shell.stage.implement",
  test: "shell.stage.test",
  review: "shell.stage.review",
  ready: "shell.stage.ready",
  done: "shell.stage.done",
  formalize: "shell.stage.formalize",
  draft: "shell.stage.draft",
  "plan-review": "shell.stage.planReview",
  approval: "shell.stage.approval",
  breakdown: "shell.stage.breakdown",
};

/** i18n keys of the journal event kinds (events.KINDS). */
export const EVENT_KIND_KEYS: Record<string, string> = {
  registered: "shell.event.registered",
  transition: "shell.event.transition",
  gate: "shell.event.gate",
  blocked: "shell.event.blocked",
  parked: "shell.event.parked",
  approved: "shell.event.approved",
  rejected: "shell.event.rejected",
  continued: "shell.event.continued",
  merged: "shell.event.merged",
  closed: "shell.event.closed",
  paused: "shell.event.paused",
  resumed: "shell.event.resumed",
  abandoned: "shell.event.abandoned",
};

/** The last stage of a pipeline: a run there has merged, been abandoned, or is parked waiting for the merge. */
export const FINAL_STAGE = "done";
