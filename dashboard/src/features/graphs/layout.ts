import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type Simulation,
} from "d3-force";
import type { SimulationNodeDatum } from "d3-force";

import type { GraphEdge, GraphNode } from "@/types/api";

import {
  CENTER_PULL,
  CHARGE,
  COLLIDE_GAP,
  LABEL_TOP_HUBS,
  LINK_DISTANCE,
  MAX_DRAWN_NODES,
  MAX_RADIUS,
  MIN_RADIUS,
  RADIUS_PER_SQRT_WEIGHT,
} from "./config";

/** A node of the live simulation; d3 writes `x`, `y`, `vx`, `vy` and the pin `fx`, `fy` onto it. */
export interface SimNode extends SimulationNodeDatum {
  id: string;
  kind: string;
  label: string;
  /** Edges of the drawn graph that touch this node. */
  degree: number;
  r: number;
}

export interface SimLink {
  source: SimNode;
  target: SimNode;
}

/** Everything the drawing, the keyboard and the simulation need about one graph, computed once. */
export interface Scene {
  nodes: SimNode[];
  links: SimLink[];
  byId: Map<string, SimNode>;
  neighbours: Map<string, Set<string>>;
  /** Nodes by degree, highest first; the order of Home, End, PageUp and PageDown. */
  ordered: SimNode[];
  /** The ids of the most linked nodes, labelled at every zoom. */
  hubs: Set<string>;
}

export interface CappedGraph {
  nodes: readonly GraphNode[];
  edges: readonly GraphEdge[];
}

/**
 * At most `limit` nodes that stay connected: the most linked half of the budget are seeds, then each seed's
 * heaviest neighbour, then the remaining neighbours (most linked first), then whole components when the seeds
 * run out of neighbours. Edges between kept nodes stay. The input is returned untouched when it already fits.
 * The server caps the code graph by degree alone, so this runs on the memory graph in practice, which the
 * server sends whole.
 */
export function capGraph(nodes: readonly GraphNode[], edges: readonly GraphEdge[], limit: number = MAX_DRAWN_NODES): CappedGraph {
  if (nodes.length <= limit) return { nodes, edges };
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const adjacent = new Map<string, string[]>();
  for (const edge of edges) {
    if (!byId.has(edge.source) || !byId.has(edge.target) || edge.source === edge.target) continue;
    adjacent.set(edge.source, [...(adjacent.get(edge.source) ?? []), edge.target]);
    adjacent.set(edge.target, [...(adjacent.get(edge.target) ?? []), edge.source]);
  }
  const heavier = (a: GraphNode, b: GraphNode): number => b.weight - a.weight || a.id.localeCompare(b.id);
  const ranked = [...nodes].sort(heavier);
  const kept = new Set<string>();
  const keep = (node: GraphNode | undefined): void => {
    if (node && kept.size < limit) kept.add(node.id);
  };

  const seeds = ranked.slice(0, Math.ceil(limit / 2));
  for (const seed of seeds) keep(seed);
  const neighboursOf = (id: string): GraphNode[] =>
    [...new Set(adjacent.get(id) ?? [])].filter((other) => !kept.has(other)).map((other) => byId.get(other) as GraphNode).sort(heavier);
  // Every seed gets its heaviest neighbour first, so no seed is left alone while another has many.
  for (const seed of seeds) keep(neighboursOf(seed.id)[0]);
  const rest = new Map<string, GraphNode>();
  for (const seed of seeds) for (const other of neighboursOf(seed.id)) rest.set(other.id, other);
  for (const other of [...rest.values()].sort(heavier)) keep(other);

  // Seeds without enough neighbours leave room: fill it with whole components, the largest first.
  if (kept.size < limit) {
    const seen = new Set<string>(kept);
    const components: GraphNode[][] = [];
    for (const start of ranked) {
      if (seen.has(start.id)) continue;
      const component: GraphNode[] = [];
      const queue = [start.id];
      seen.add(start.id);
      while (queue.length > 0) {
        const id = queue.shift() as string;
        component.push(byId.get(id) as GraphNode);
        for (const other of adjacent.get(id) ?? []) {
          if (!seen.has(other)) {
            seen.add(other);
            queue.push(other);
          }
        }
      }
      components.push(component);
    }
    components.sort((a, b) => b.length - a.length);
    for (const component of components) {
      if (kept.size + component.length > limit) continue;
      for (const node of component.sort(heavier)) keep(node);
    }
  }

  return {
    nodes: nodes.filter((node) => kept.has(node.id)),
    edges: edges.filter((edge) => kept.has(edge.source) && kept.has(edge.target)),
  };
}

/** Circle radius from the node weight, so hubs read larger. */
export function radiusOf(weight: number): number {
  return Math.min(MAX_RADIUS, MIN_RADIUS + Math.sqrt(Math.max(0, weight)) * RADIUS_PER_SQRT_WEIGHT);
}

/** The drawing model of a graph. Edges that point at an unknown node are ignored. */
export function buildScene(nodes: readonly GraphNode[], edges: readonly GraphEdge[]): Scene {
  const simNodes: SimNode[] = nodes.map((node) => ({ id: node.id, kind: node.kind, label: node.label, degree: 0, r: radiusOf(node.weight) }));
  const byId = new Map(simNodes.map((node) => [node.id, node]));
  const neighbours = new Map<string, Set<string>>(simNodes.map((node) => [node.id, new Set<string>()]));
  const links: SimLink[] = [];
  for (const edge of edges) {
    const source = byId.get(edge.source);
    const target = byId.get(edge.target);
    if (!source || !target) continue;
    links.push({ source, target });
    source.degree += 1;
    target.degree += 1;
    neighbours.get(source.id)?.add(target.id);
    neighbours.get(target.id)?.add(source.id);
  }
  const ordered = [...simNodes].sort((a, b) => b.degree - a.degree || a.label.localeCompare(b.label) || a.id.localeCompare(b.id));
  const hubs = new Set(ordered.slice(0, LABEL_TOP_HUBS).filter((node) => node.degree > 0).map((node) => node.id));
  return { nodes: simNodes, links, byId, neighbours, ordered, hubs };
}

/** A stopped simulation of the scene; the caller ticks it, so it owns no timer. Centred on the origin. */
export function createSimulation(scene: Scene): Simulation<SimNode, SimLink> {
  return forceSimulation(scene.nodes)
    .force("link", forceLink<SimNode, SimLink>(scene.links).distance(LINK_DISTANCE))
    .force("charge", forceManyBody<SimNode>().strength(CHARGE))
    .force("x", forceX<SimNode>(0).strength(CENTER_PULL))
    .force("y", forceY<SimNode>(0).strength(CENTER_PULL))
    .force("collide", forceCollide<SimNode>((node) => node.r + COLLIDE_GAP))
    .stop();
}

/** Ticks the simulation until it has cooled to rest, with no animation and no timer. */
export function settle(simulation: Simulation<SimNode, SimLink>): void {
  const ticks = Math.ceil(Math.log(simulation.alphaMin()) / Math.log(1 - simulation.alphaDecay()));
  for (let tick = 0; tick < ticks; tick += 1) simulation.tick();
}
