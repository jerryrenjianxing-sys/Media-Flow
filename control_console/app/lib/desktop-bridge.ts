"use client";

import {useEffect, useState} from "react";

export type DesktopUpdatePhase = "idle" | "checking" | "available" | "downloading" | "verifying" | "waiting_idle" | "countdown" | "installing" | "current" | "error";
export interface DesktopState {
  desktopVersion: string;
  sourceRevision: string;
  packaged: boolean;
  startup: {phase: string; message: string; ready: boolean};
  instance: {apiUrl: string; uiUrl: string};
  update: {
    phase: DesktopUpdatePhase;
    version?: string;
    releaseNotes?: string;
    percent?: number;
    bytesPerSecond?: number;
    transferred?: number;
    total?: number;
    message?: string;
    canCancel?: boolean;
    differential?: string | boolean;
    countdownSeconds?: number;
    operationId?: string;
    blockers?: Array<{kind: string; message: string; id?: string}>;
  };
}
export interface DesktopBridge {
  getState(): Promise<DesktopState>;
  subscribe(callback: (state: DesktopState) => void): () => void;
  checkForUpdates(): Promise<unknown>;
  startUpdate(): Promise<unknown>;
  deferUpdate(): Promise<unknown>;
  cancelUpdate(): Promise<unknown>;
  retryStartup(): Promise<unknown>;
  openLogs(): Promise<unknown>;
}

declare global {
  interface Window {mediaflowDesktop?: DesktopBridge}
}

export function useDesktopState() {
  const [state, setState] = useState<DesktopState | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const bridge = window.mediaflowDesktop;
    if (!bridge) return;
    let active = true;
    let received = false;
    const unsubscribe = bridge.subscribe(next => {
      received = true;
      if (active) {setState(next); setError(null);}
    });
    void bridge.getState().then(next => {
      if (active && !received) setState(next);
    }).catch(() => {if (active) setError("桌面状态暂不可用，请重新打开窗口。");});
    return () => {active = false; unsubscribe();};
  }, []);
  async function invoke(action: keyof Omit<DesktopBridge, "getState" | "subscribe">) {
    const bridge = window.mediaflowDesktop;
    if (!bridge) return;
    setError(null);
    try {await bridge[action]();}
    catch (failure) {setError(failure instanceof Error ? failure.message : "操作暂未完成，请重试。");}
  }
  return {state, error, invoke};
}
