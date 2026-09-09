export type TaskProgress = { schema_supported?: boolean; processed_slots: number; successful_slots: number; failed_slots: number; unavailable_slots: number; unknown_actions: number; skipped_slots: number; waiting_reason?: string | null; next_check_at?: number | null; last_progress_at?: number | null; next_slot?: number | null; affected_device?: string; affected_capabilities?: string[]; available_actions?: string[]; evidence_dirs?: string[] };
export function taskStatusLabel(status: string, task?: { error?: string | null; result?: Record<string, unknown> | null }): string;
export function supportedTaskProgress(progress?: TaskProgress): TaskProgress | undefined;
export function taskControlActions(status: string, progress?: TaskProgress): string[];
export function isWaitingTask(status: string): boolean;
export function controlTask(transport: typeof fetch, base: string, taskId: string, action: string, requestId: string): Promise<{ ok: boolean; status: string; user_message?: string; result?: { task?: { status: string; progress: TaskProgress } } }>;
