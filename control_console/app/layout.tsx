import type { Metadata } from "next";
import "./globals.css";
import ConsoleShell from "./components/console-shell";
import { BRAND } from "./brand";

export const metadata: Metadata = {
  title: BRAND.fullName,
  description: "本地媒体自动化任务、设备与运行管理平台",
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script
          dangerouslySetInnerHTML={{
            __html: `(function(){try{var saved=localStorage.getItem("mediaflow-theme")||localStorage.getItem("riskflow-theme");document.documentElement.dataset.theme=saved==="light"?"light":"dark"}catch(e){document.documentElement.dataset.theme="dark"}var key="mediaflow-asset-recovery";window.addEventListener("error",function(event){var target=event.target;var url=target&&(target.src||target.href)||"";if(!url||url.indexOf("/_next/static/")<0)return;try{var marker=location.pathname;if(sessionStorage.getItem(key)===marker)return;sessionStorage.setItem(key,marker);var next=new URL(location.href);next.searchParams.set("asset_reload",Date.now().toString());location.replace(next.toString())}catch(e){}},true);window.addEventListener("pageshow",function(){setTimeout(function(){try{sessionStorage.removeItem(key)}catch(e){}},10000)})})();`,
          }}
        />
      </head>
      <body><ConsoleShell>{children}</ConsoleShell></body>
    </html>
  );
}
