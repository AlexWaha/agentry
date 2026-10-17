import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from "d3-force";

import type { GraphEdge, GraphNode } from "@/types/api";

export const GRAPH_WIDTH = 800;
export const GRAPH_HEIGHT = 520;

/** Most nodes the drawing takes; the code graph API caps at the same number, the memory graph is capped here. */
export const MAX_DRAWN_NODES = 400;

const PADDING = 28;
const LINK_DISTANCE = 56;
const CHARGE = -110;
const CENTER_PULL = 0.06;
const MIN_RADIUS = 5;
const RADIUS_PER_SQRT_WEIGHT = 2.2;
const MAX_RADIUS = 16;
const COLLIDE_GAP = 3;

export interface Placed {
  x: number;
  y: number;
  r: number;
}

interface SimNode extends SimulationNodeDatum {
  id: string;
  r: number;
}

/** The `limit` heaviest nodes and the edges between them; the input untouched when it already fits. */
export function capGraph(
  nodes: readonly GraphNode[],
  edges: readonly GraphEdge[],
  limit: number = MAX_DRAWN_NODES,
): { nodes: readonly GraphNode[]; edges: readonly GraphEdge[] } {
  if (nodes.length <= limit) return { nodes, edges };
  const kept = [...nodes].sort((a, b) => b.weight - a.weight).slice(0, limit);
  const ids = new Set(kept.map((node) => node.id));
  return { nodes: kept, edges: edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)) };
}

/** Circle radius from the node weight (its degree), so hubs read larger. */
export function radiusOf(weight: number): number {
  return Math.min(MAX_RADIUS, MIN_RADIUS + Math.sqrt(Math.max(0, weight)) * RADIUS_PER_SQRT_WEIGHT);
}

const clamp = (value: number, low: number, high: number): number => Math.min(high, Math.max(low, value));

/**
 * A static d3-force layout (FR-106): the simulation runs to rest synchronously, so the SVG is drawn once, in
 * place, with no animation and no timers. d3 seeds its own random source, so the same graph always lays out
 * the same way. Edges that point at an unknown node are ignored.
 */
export function layoutGraph(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): Map<string, Placed> {
  const simNodes: SimNode[] = nodes.map((node) => ({ id: node.id, r: radiusOf(node.weight) }));
  const known = new Set(simNodes.map((node) => node.id));
  const links: SimulationLinkDatum<SimNode>[] = edges
    .filter((edge) => known.has(edge.source) && known.has(edge.target))
    .map((edge) => ({ source: edge.source, target: edge.target }));

  const simulation = forceSimulation(simNodes)
    .force("link", forceLink<SimNode, SimulationLinkDatum<SimNode>>(links).id((node) => node.id).distance(LINK_DISTANCE))
    .force("charge", forceManyBody().strength(CHARGE))
    .force("center", forceCenter(GRAPH_WIDTH / 2, GRAPH_HEIGHT / 2))
    .force("x", forceX(GRAPH_WIDTH / 2).strength(CENTER_PULL))
    .force("y", forceY(GRAPH_HEIGHT / 2).strength(CENTER_PULL))
    .force("collide", forceCollide<SimNode>((node) => node.r + COLLIDE_GAP))
    .stop();

  const ticks = Math.ceil(Math.log(simulation.alphaMin()) / Math.log(1 - simulation.alphaDecay()));
  for (let tick = 0; tick < ticks; tick += 1) simulation.tick();

  return new Map(
    simNodes.map((node) => [
      node.id,
      {
        x: clamp(node.x ?? GRAPH_WIDTH / 2, PADDING, GRAPH_WIDTH - PADDING),
        y: clamp(node.y ?? GRAPH_HEIGHT / 2, PADDING, GRAPH_HEIGHT - PADDING),
        r: node.r,
      },
    ]),
  );
}
