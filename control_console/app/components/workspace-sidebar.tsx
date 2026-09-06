"use client";

/* eslint-disable @next/next/no-html-link-for-pages, @next/next/no-img-element -- Vinext production navigation uses full-page links; this fixed local SVG avoids image-pipeline dependencies. */

import type { RefObject } from "react";
import { BRAND } from "../brand";

export type WorkspaceIconName = "create" | "run" | "results" | "devices" | "assets";

type WorkspaceRoute = {href:string;label:string;icon:WorkspaceIconName;description:string};
export const workspaceGroups: readonly {label:string;links:readonly WorkspaceRoute[]}[] = [
  {
    label: "任务生命周期",
    links: [
      { href: "/", label: "MediaFlow 助手", icon: "create" as const, description: "对话、计划与执行" },
      { href: "/manage", label: "管理中心", icon: "assets" as const, description: "任务、设备与完整设置" },
      { href: "/workbench", label: "任务台", icon: "create" as const, description: "创建、预览并启动任务" },
      { href: "/run", label: "运行", icon: "run" as const, description: "队列监控与安全控制" },
      { href: "/records", label: "结果", icon: "results" as const, description: "任务、纠错与互动凭证" },
      { href: "/devices", label: "设备", icon: "devices" as const, description: "健康、画面与初始化" },
    ],
  },
  {
    label: "配置与治理",
    links: [{ href: "/content", label: "资产与设置", icon: "assets" as const, description: "内容、模型与评测设置" }],
  },
] as const;

export const workspaceRoutes = workspaceGroups.flatMap((group) => group.links);

export function WorkspaceIcon({ name }: { name: WorkspaceIconName }) {
  if (name === "create") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>;
  if (name === "run") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m8 5 11 7-11 7Z"/></svg>;
  if (name === "results") return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h10M7 10h10M7 15h6"/><path d="m15 17 2 2 4-5"/></svg>;
  if (name === "devices") return <svg viewBox="0 0 24 24" aria-hidden="true"><rect x="7" y="3" width="10" height="18" rx="2"/><path d="M10 6h4M11 18h2"/></svg>;
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 7.5h7l2 2h9v9.5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 7.5V5a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v2.5"/></svg>;
}

type WorkspaceSidebarProps = {
  collapsed: boolean;
  pathname: string;
  deviceLabel: string;
  queueLabel: string;
  statusError: boolean;
  paused: boolean;
  online: number;
  sidebarRef: RefObject<HTMLElement | null>;
  scrimRef: RefObject<HTMLButtonElement | null>;
  toggleRef: RefObject<HTMLButtonElement | null>;
  onToggle: () => void;
  onCollapse: () => void;
};

export default function WorkspaceSidebar({
  collapsed,
  pathname,
  deviceLabel,
  queueLabel,
  statusError,
  paused,
  online,
  sidebarRef,
  scrimRef,
  toggleRef,
  onToggle,
  onCollapse,
}: WorkspaceSidebarProps) {
  return <>
    <button ref={scrimRef} type="button" className="workspace-sidebar-scrim" aria-label="收起左侧导航" tabIndex={collapsed ? -1 : 0} onClick={onCollapse}/>
    <aside ref={sidebarRef} className="workspace-sidebar" aria-label={`${BRAND.name} 工作台导航`}>
      <header className="workspace-sidebar-head">
        <button ref={toggleRef} type="button" className="workspace-sidebar-toggle" aria-controls="workspace-navigation" aria-expanded={!collapsed} aria-label={collapsed ? "展开左侧导航" : "收起左侧导航"} title={collapsed ? "展开左侧导航" : "收起左侧导航"} onClick={onToggle}>
          <span className="workspace-menu-icon" aria-hidden="true"><i/><i/><i/></span>
        </button>
        <a className="workspace-brand" href="/" aria-label={`${BRAND.name} 任务台`}><img src="/favicon.svg" alt=""/><span><strong>{BRAND.name}</strong><small>{BRAND.tagline}</small></span></a>
      </header>

      <nav id="workspace-navigation" className="workspace-nav">
        {workspaceGroups.map((group) => <section key={group.label}>
          <h2>{group.label}</h2>
          {group.links.filter((link) => link.href === "/" || link.href === "/manage").map((link) => {
            const active = pathname === link.href || (link.href !== "/" && pathname.startsWith(`${link.href}/`));
            return <a key={link.href} href={link.href} className={active ? "active" : ""} aria-current={active ? "page" : undefined} title={collapsed ? `${link.label} · ${link.description}` : undefined}>
              <span className="workspace-nav-mark"><WorkspaceIcon name={link.icon}/></span>
              <span className="workspace-nav-copy"><span className="workspace-nav-label">{link.label}</span><small>{link.description}</small></span>
            </a>;
          })}
        </section>)}
      </nav>

      <div className="workspace-sidebar-status" aria-live="polite" title={collapsed ? `${deviceLabel} · ${queueLabel}` : undefined}>
        <span className={statusError ? "dot danger" : paused ? "dot paused" : online ? "dot online" : "dot"}/>
        <span className="workspace-sidebar-status-copy"><strong>{deviceLabel}</strong><small>{queueLabel} · 本机</small></span>
      </div>
    </aside>
  </>;
}
