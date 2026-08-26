import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RiskFlow 社媒风控实验台",
  description: "本地 Android 自动化策略配置与运行面板",
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
            __html: `(function(){try{var saved=localStorage.getItem("riskflow-theme");document.documentElement.dataset.theme=saved==="light"?"light":"dark"}catch(e){document.documentElement.dataset.theme="dark"}})();`,
          }}
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
