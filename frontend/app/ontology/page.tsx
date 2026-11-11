import type { Metadata } from "next";
import { OntologyManager } from "@/components/ontology-manager";
export const metadata: Metadata = { title: "业务本体" };

export default function Ontology() { return <OntologyManager />; }
