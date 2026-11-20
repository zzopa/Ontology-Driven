import type { Metadata } from "next";
import { Providers } from "@/components/ui";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "知航 · ACDM 智能问询", template: "%s · 知航" },
  applicationName: "知航",
  description: "知航，以业务本体连接航班、保障作业与资源，让每次 ACDM 问询都有数据依据。",
  icons: {
    icon: [{ url: "/brand/favicon.ico", sizes: "32x32", type: "image/x-icon" }, { url: "/brand/logo.svg", type: "image/svg+xml", sizes: "any" }],
    apple: "/brand/apple-touch-icon.png",
  },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>
        <Providers>{children}</Providers>
        <script src="/swarm/swarm-trigger.js" data-src="/swarm/swarm.js" defer />
      </body>
    </html>
  );
}
