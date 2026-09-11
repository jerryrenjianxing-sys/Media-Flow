export type ModelError = { kind?: string; attempts?: number | null; retryable?: boolean; status_code?: number | null; diagnostics?: { stage?: string; elapsed_ms?: number; transport_error?: string } };
export function modelDiagnosticText(error?: ModelError | null): string;
