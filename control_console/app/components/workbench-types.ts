export const API = "http://127.0.0.1:48138";

export type ContentMode = "general" | "mixed" | "search" | "hybrid";
export type WorkbenchConfig = {
  hybrid_probability_mode?: "topic" | "legacy"; round_interval_basis?: "completion" | "scheduled";
  device_id: string; device_ids: string[]; content_mode: ContentMode; search_query: string; search_trust_results: boolean;
  search_segment_min: number; search_segment_max: number; home_segment_min: number; home_segment_max: number;
  topic_prompt: string; content_plan_id?: string | null; content_plan_revision_id?: string | null;
  video_count: number; round_count: number; round_interval_minutes: number; dwell_min: number; dwell_max: number;
  like_probability: number; favorite_probability: number; comment_probability: number;
  matched_like_probability: number; matched_favorite_probability: number; matched_comment_probability: number;
  preview_only: boolean; engagement_inspection_enabled: boolean; inspection_every_rounds: number; inspection_mode?: "home_badge" | "legacy";
  comment_policy_enabled: boolean; comment_policy_prompt: string; max_gate_skips: number; seed: number; [key: string]: unknown;
};
export type RunDraft = { name: string; revision: number; config: WorkbenchConfig; updated_at: string };
export type DeviceStatus = { device_id: string; state: string; device_type?: "physical" | "virtual"; friendly_name?: string; model?: string; profile_verified?: boolean; initialization_status?: string };
export type VirtualDevice = {
  virtual_device_id: string; provider: string; provider_instance_id: string; name: string; state: string;
  adb_endpoint?: string | null; last_adb_endpoint?: string | null; discovery_source?: string;
  presence_status?: string; profile_status?: string; task_ready?: boolean; last_connected_at?: string | null; last_error?: string | null;
  standard_status?: "standard" | "nonstandard" | "requires_verification"; standard_message?: string | null;
  management_status?: "managed_standard" | "managed_nonstandard" | "unmanaged" | "identity_conflict";
  connected_device?: DeviceStatus | null;
  managed?: boolean; display_index?: number | null; connection_status?: string; can_start?: boolean;
  environment_status?: "standard" | "needs_display_fix" | "unverified"; environment_mismatches?: Array<{ field: string; actual: number | null; expected: number }>;
  auto_managed?: boolean; capabilities?: Record<string, { status: "ready" | "preparable" | "unavailable"; reason: string; remediation?: string | null }>;
  task_eligibility?: Partial<Record<"screen" | "browse" | "search" | "engagement_inspection" | "writes", boolean>>;
  profile_bundle_id?: string | null; ui_compatibility_id?: string | null; remediations?: string[];
  onboarding_status?: string; issue_status?: string; blocking_scope?: string; reason_code?: string;
  user_message?: string; suggested_action?: string; available_actions?: string[]; retryable?: boolean; diagnostic_id?: string;
  readiness_steps?: Array<{ id: string; label: string; status: "ready" | "waiting" | "pending" | "optional" | "blocked"; message: string }>;
  recipe?: Partial<{ cpu: number; memory_gb: number; width: number; height: number; dpi: number; fps: number; root: boolean; auto_rotate: boolean; muted: boolean }>;
  active_operation?: import("../lib/virtual-device-operations").VirtualOperation | null;
};
export type WorkbenchPreview = {
  ready: boolean; draft_revision: number; plan_hash: string; mode: ContentMode; topic: string; search_query: string;
  segments?: { search: { min: number; max: number }; home: { min: number; max: number } } | null;
  devices: { device_id: string; name: string; available: boolean; reason: string; initialization_status: string }[];
  eligible_device_ids: string[]; video_task_count: number; inspection_task_count: number; total_task_count: number; estimated_seconds: number;
  probabilities: { like: number; favorite: number; comment: number }; comment_mode: string; write_actions: string[];
  requires_confirmation: boolean; warnings: string[]; blockers: string[];
};
export type VirtualDeviceIssue = Pick<VirtualDevice, "virtual_device_id" | "name" | "issue_status" | "blocking_scope" | "reason_code" | "user_message" | "suggested_action" | "available_actions" | "retryable" | "diagnostic_id"> & { updated_at?: string };
export type StatusPayload = { paused: boolean; devices: DeviceStatus[]; device_preferences?: { physical_devices_enabled: boolean }; task_summary: Record<string, number>; stop_requested_device_ids: string[]; virtualization?: { devices: VirtualDevice[]; issues?: VirtualDeviceIssue[]; device_count: number; ready_count: number; stopped_count?: number } };
export type Preset = { name: string; builtin: boolean; config: Partial<WorkbenchConfig> };
export type ContentPlan = { plan_id: string; revision_id: string; revision_number: number; document: { name: string; themes: { name: string; enabled: boolean }[] } };
