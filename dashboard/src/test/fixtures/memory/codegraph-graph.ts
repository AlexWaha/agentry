import type { Graph, GraphMeta } from "@/types/api";

import fixture from "./codegraph-graph.json";

/* Body of GET /api/v1/codegraph/root/graph; the JSON file is the one test_dashboard_contract compares with a live answer. */
export const codeGraph: Graph = fixture.body.data;
export const codeGraphMeta: GraphMeta = fixture.body.meta;
