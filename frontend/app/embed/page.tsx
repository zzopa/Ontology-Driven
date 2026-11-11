import type { Metadata } from "next";
import { Workspace } from "@/components/workspace";
export const metadata: Metadata = { title: "嵌入问询" };

export default function Embed() { return <Workspace embedded />; }
