import type { Metadata } from "next";
import { Providers } from "@/components/ui";
import "./globals.css";

export const metadata: Metadata = {
  title: "智能问数 · Airport Intelligence",
  description: "基于业务本体、实时 SQL 与来源证据的机场集团数据工作台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body><Providers>{children}</Providers></body></html>;
}
