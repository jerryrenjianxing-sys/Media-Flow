export type InspectionMode = "home_badge" | "legacy";
export type HomeBadge = {
  state?: "present" | "absent" | "unknown";
  reason_code?: string;
  message?: string;
  badge_text?: string | null;
  message_count?: number | null;
  count_is_lower_bound?: boolean;
  source?: "local" | "vision";
  target_bounds?: number[] | null;
  evidence_missing?: string[];
};
export type InspectionPresentation = { label: string; kind: "alert" | "clear" | "incomplete"; message?: string };

export function inspectionModeFromPayload(payload?: { inspection_mode?: string; inspection_workflow_version?: string } | null): InspectionMode;
export function inspectionTaskLabel(payload?: { inspection_mode?: string; inspection_workflow_version?: string } | null): string;
export function homeBadgePresentation(homeBadge?: HomeBadge | null): Required<Pick<InspectionPresentation, "label" | "kind">> & { message: string };
export function homeBadgeAlertPresentation(summary?: { confirmed?: boolean; last_checked_at?: string; last_check_message?: string; home_badge?: HomeBadge } | null, checkedAt?: string): Required<Pick<InspectionPresentation, "label" | "kind">> & { message: string };
export function inspectionReceiptPresentation(inspection?: { workflow_version?: string; status?: string; result_kind?: string; summary?: { home_badge?: HomeBadge } } | null): InspectionPresentation;
