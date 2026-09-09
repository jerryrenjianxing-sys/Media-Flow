export type InspectionMode = "home_badge" | "legacy";
export type HomeBadge = {
  state?: "present" | "absent" | "unknown";
  reason_code?: string;
  message?: string;
  badge_text?: string | null;
  message_count?: number | null;
  count_is_lower_bound?: boolean;
  source?: "local" | "vision";
  quantity_status?: "recognized" | "none" | "dot" | "unreadable" | "conflict";
  quantity_source?: "ui_tree" | "local_glyph" | "vision" | null;
  badge_bounds?: number[] | null;
  rule_version?: string;
  target_bounds?: number[] | null;
  evidence_missing?: string[];
};
export type InspectionPresentation = { label: string; kind: "alert" | "clear" | "incomplete"; message?: string };

export function inspectionModeFromPayload(payload?: { inspection_mode?: string; inspection_workflow_version?: string } | null): InspectionMode;
export function inspectionTaskLabel(payload?: { inspection_mode?: string; inspection_workflow_version?: string } | null): string;
export function homeBadgePresentation(homeBadge?: HomeBadge | null): Required<Pick<InspectionPresentation, "label" | "kind">> & { message: string };
export function homeBadgeQuantityNote(homeBadge?: HomeBadge | null): string;
export function homeBadgeSourceLabel(homeBadge?: HomeBadge | null): string;
export function resolveInspection(task?: { payload?: { inspection_mode?: string; inspection_workflow_version?: string }; inspection_mode?: string; inspection_workflow_version?: string; status?: string; result?: object | null; summary?: object; workflow_version?: string; result_kind?: string }): InspectionPresentation & { mode: InspectionMode | "unknown" | "conflict"; phase: "pending" | "running" | "final"; showLegacySections: boolean };
export function homeBadgeAlertPresentation(summary?: { confirmed?: boolean; last_checked_at?: string; last_check_message?: string; home_badge?: HomeBadge } | null, checkedAt?: string): Required<Pick<InspectionPresentation, "label" | "kind">> & { message: string };
export function inspectionReceiptPresentation(inspection?: { workflow_version?: string; status?: string; result_kind?: string; summary?: { home_badge?: HomeBadge } } | null): InspectionPresentation;
