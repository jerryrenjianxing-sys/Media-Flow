"use client";

import gsap from "gsap";
import { usePathname } from "next/navigation";
import { fetchLocalApi } from "../lib/local-api";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent as ReactMouseEvent } from "react";
import InteractionAlertBanner from "./interaction-alert-banner";
import WorkspaceSidebar, { WorkspaceIcon, workspaceRoutes } from "./workspace-sidebar";

const API = "http://127.0.0.1:48138";

type StatusPayload = {
  paused?: boolean;
  devices?: { state?: string }[];
  task_summary?: { pending?: number; running?: number };
  product_version?: { channel?: "development" | "release"; display_version?: string };
  virtualization?: { issues?: Array<{ diagnostic_id: string; issue_status: string }> };
};

export default function ConsoleShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const shellRef = useRef<HTMLDivElement>(null);
  const sidebarRef = useRef<HTMLElement>(null);
  const sidebarScrimRef = useRef<HTMLButtonElement>(null);
  const sidebarToggleRef = useRef<HTMLButtonElement>(null);
  const sidebarFromWidthRef = useRef<number | null>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const themeButtonRef = useRef<HTMLButtonElement>(null);
  const themeLayerRef = useRef<HTMLDivElement>(null);
  const [status, setStatus] = useState<StatusPayload | null>(null);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [statusError, setStatusError] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const response = await fetchLocalApi(`${API}/api/status`, { cache: "no-store" }, 5_000);
      if (!response.ok) throw new Error("status unavailable");
      setStatus(await response.json() as StatusPayload);
      setStatusError(false);
    } catch {
      setStatusError(true);
    }
  }, []);

  const transitionSidebar = useCallback((next: boolean) => {
    if (next === sidebarCollapsed) return;
    const sidebar = sidebarRef.current;
    if (sidebar && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      sidebarFromWidthRef.current = sidebar.getBoundingClientRect().width;
    }
    window.localStorage.setItem("mediaflow-sidebar-collapsed", String(next));
    setSidebarCollapsed(next);
  }, [sidebarCollapsed]);

  useEffect(() => {
    const themeSync = window.setTimeout(() => setTheme(document.documentElement.dataset.theme === "light" ? "light" : "dark"), 0);
    const compactSidebar = window.matchMedia("(max-width: 1180px)");
    const sidebarSync = window.setTimeout(() => {
      const stored = window.localStorage.getItem("mediaflow-sidebar-collapsed") ?? window.localStorage.getItem("riskflow-sidebar-collapsed");
      setSidebarCollapsed(stored === "true" || compactSidebar.matches);
    }, 0);
    const initial = window.setTimeout(() => void refresh(), 0);
    const timer = window.setInterval(() => void refresh(), 5000);
    const collapseForCompactViewport = (event: MediaQueryListEvent) => {
      if (event.matches) setSidebarCollapsed(true);
    };
    compactSidebar.addEventListener("change", collapseForCompactViewport);
    return () => {
      window.clearTimeout(themeSync);
      window.clearTimeout(sidebarSync);
      window.clearTimeout(initial);
      window.clearInterval(timer);
      compactSidebar.removeEventListener("change", collapseForCompactViewport);
    };
  }, [refresh]);

  useLayoutEffect(() => {
    const stage = stageRef.current;
    if (!stage || window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const context = gsap.context(() => {
      // Keep content readable even when a browser throttles or suspends animations.
      // Motion may decorate the page, but it must never be the mechanism that reveals it.
      gsap.fromTo(stage, { y: 10 }, { y: 0, duration: 0.42, ease: "power2.out", clearProps: "transform" });
      const sections = stage.querySelectorAll<HTMLElement>("[data-motion]");
      if (sections.length) {
        gsap.fromTo(sections, { y: 14 }, { y: 0, duration: 0.46, stagger: 0.055, delay: 0.06, ease: "power2.out", clearProps: "transform" });
      }
    }, stage);
    return () => context.revert();
  }, [pathname]);

  useLayoutEffect(() => {
    const fromWidth = sidebarFromWidthRef.current;
    sidebarFromWidthRef.current = null;
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const sidebar = sidebarRef.current;
    if (!sidebar || fromWidth === null) return;
    const labels = sidebar?.querySelectorAll<HTMLElement>(
      ".workspace-brand > span:last-child, .workspace-nav h2, .workspace-nav-copy, .workspace-sidebar-status-copy",
    ) ?? [];
    const scrim = sidebarScrimRef.current;
    const targetWidth = sidebar.getBoundingClientRect().width;
    const context = gsap.context(() => {
      gsap.killTweensOf([sidebar, scrim, ...Array.from(labels)]);
      gsap.fromTo(sidebar, { width: fromWidth }, { width: targetWidth, duration: 0.34, ease: "power2.inOut", clearProps: "width" });
      if (sidebarCollapsed) {
        gsap.fromTo(labels, { autoAlpha: 1, x: 0 }, { autoAlpha: 0, x: -8, duration: 0.14, stagger: 0.008, ease: "power2.in", clearProps: "opacity,visibility,transform" });
        if (scrim) gsap.fromTo(scrim, { autoAlpha: 1 }, { autoAlpha: 0, duration: 0.16, ease: "power1.out", clearProps: "opacity,visibility" });
      } else {
        gsap.fromTo(labels, { autoAlpha: 0, x: -10 }, { autoAlpha: 1, x: 0, duration: 0.22, stagger: 0.015, delay: 0.08, ease: "power2.out", clearProps: "opacity,visibility,transform" });
        if (scrim) gsap.fromTo(scrim, { autoAlpha: 0 }, { autoAlpha: 1, duration: 0.2, ease: "power1.out", clearProps: "opacity,visibility" });
      }
    }, shellRef);
    return () => context.revert();
  }, [sidebarCollapsed]);

  useEffect(() => {
    if (sidebarCollapsed) return;
    function collapseSidebarFromWorkspace(event: MouseEvent) {
      const target = event.target;
      if (!(target instanceof Node)) return;
      if (sidebarRef.current?.contains(target) || sidebarScrimRef.current?.contains(target)) return;
      transitionSidebar(true);
    }
    document.addEventListener("click", collapseSidebarFromWorkspace);
    return () => document.removeEventListener("click", collapseSidebarFromWorkspace);
  }, [sidebarCollapsed, transitionSidebar]);

  const online = useMemo(
    () => status?.devices?.filter((device) => device.state === "device").length ?? 0,
    [status],
  );
  const pending = status?.task_summary?.pending ?? 0;
  const running = status?.task_summary?.running ?? 0;
  const issueCount = status?.virtualization?.issues?.length ?? 0;
  const currentRoute = workspaceRoutes.find((route) => route.href === pathname)
    ?? workspaceRoutes.find((route) => route.href !== "/" && pathname.startsWith(`${route.href}/`))
    ?? (pathname === "/interactions" ? workspaceRoutes.find((route) => route.href === "/records") : undefined)
    ?? (pathname === "/governance" ? workspaceRoutes.find((route) => route.href === "/content") : undefined)
    ?? workspaceRoutes[0];
  const deviceLabel = statusError ? "本机服务未连接" : status ? `${online} 台设备在线` : "正在读取设备";
  const queueLabel = !status || statusError ? "队列状态未知" : running ? `${running} 个任务执行中` : pending ? `${pending} 个任务排队` : "正式队列为空";

  function applyTheme(next: "dark" | "light") {
    document.documentElement.dataset.theme = next;
    window.localStorage.setItem("mediaflow-theme", next);
    setTheme(next);
  }

  function toggleTheme(event: ReactMouseEvent<HTMLButtonElement>) {
    const next = theme === "dark" ? "light" : "dark";
    const layer = themeLayerRef.current;
    if (!layer || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      applyTheme(next);
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    const originX = bounds.left + bounds.width / 2;
    const originY = bounds.top + bounds.height / 2;
    const radius = Math.ceil(Math.hypot(Math.max(originX, window.innerWidth - originX), Math.max(originY, window.innerHeight - originY)));
    gsap.killTweensOf(layer);
    gsap.set(layer, {
      display: "block",
      opacity: 1,
      backgroundColor: next === "light" ? "#f5f6f7" : "#010102",
      clipPath: `circle(0px at ${originX}px ${originY}px)`,
    });
    gsap.to(layer, {
      clipPath: `circle(${radius}px at ${originX}px ${originY}px)`,
      duration: 0.48,
      ease: "power3.inOut",
      onComplete: () => {
        applyTheme(next);
        gsap.to(layer, { opacity: 0, duration: 0.16, ease: "power1.out", onComplete: () => gsap.set(layer, { display: "none" }) });
      },
    });
    if (themeButtonRef.current) gsap.fromTo(themeButtonRef.current, { rotate: 0 }, { rotate: 180, duration: 0.55, ease: "power2.inOut", clearProps: "transform" });
  }

  function toggleSidebar() {
    transitionSidebar(!sidebarCollapsed);
  }

  return (
    <div ref={shellRef} className={`workspace-shell${sidebarCollapsed ? " sidebar-collapsed" : ""}${pathname === "/" ? " assistant-shell" : ""}`}>
      <WorkspaceSidebar collapsed={sidebarCollapsed} pathname={pathname} deviceLabel={deviceLabel} queueLabel={queueLabel} statusError={statusError} paused={Boolean(status?.paused)} online={online} sidebarRef={sidebarRef} scrimRef={sidebarScrimRef} toggleRef={sidebarToggleRef} onToggle={toggleSidebar} onCollapse={() => transitionSidebar(true)}/>
      <div className="workspace-main">
        <header className="workspace-topbar">
          <div className="workspace-current-page">
            <span><WorkspaceIcon name={currentRoute.icon}/></span>
            <div><strong>{currentRoute.label}</strong><small>{currentRoute.description}</small></div>
          </div>
          <div className="workspace-topbar-actions" aria-live="polite">
            {statusError
              ? <button type="button" className="workspace-status-pill issue danger" onClick={() => void refresh()}><i className="dot danger"/>本机服务未连接 · 点击重试</button>
              : <span className="workspace-status-pill"><i className={status && online ? "dot online" : "dot"}/>{deviceLabel}</span>}
            <a href="/run" className="workspace-status-pill queue">{queueLabel}</a>
            <a href="/run" className="workspace-status-pill safety-control">暂停 / 安全停止</a>
            {!statusError && issueCount > 0 && <a className="workspace-status-pill issue" href="/devices#device-issues"><i className="dot warning"/>{issueCount} 项问题待处理</a>}
            <span className="environment" title={status?.product_version?.channel === "development" ? "当前为源码开发运行，不是安装包" : "当前为安装发行版"}>{status?.product_version?.display_version ? `${status.product_version.channel === "development" ? "开发版" : "安装版"} ${status.product_version.display_version}` : "本机 · 内部安全测试"}</span>
            <button ref={themeButtonRef} type="button" className="theme-toggle" onClick={toggleTheme} aria-label={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"} title={theme === "dark" ? "切换到浅色主题" : "切换到深色主题"}><span className={`theme-icon ${theme === "dark" ? "sun" : "moon"}`} aria-hidden="true"/></button>
          </div>
        </header>
        <InteractionAlertBanner />
        <div ref={stageRef} className="workspace-page-stage" key={pathname}>{children}</div>
      </div>
      <div ref={themeLayerRef} className="theme-transition-layer" aria-hidden="true"/>
    </div>
  );
}
