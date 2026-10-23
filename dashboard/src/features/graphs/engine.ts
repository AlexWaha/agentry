import type { Direction } from "./view";

/** What both graph views (the 2D canvas and the 3D scene) offer the component around them. */

export interface EngineOptions {
  /** Settle the layout before the first frame and never animate (prefers-reduced-motion). */
  reducedMotion: boolean;
  /** A click on a node (its id) or on the background (null). */
  onSelect: (id: string | null) => void;
}

export interface EngineView {
  selected: string | null;
  /** The node the keyboard is on; null when the view has no keyboard focus. */
  focused: string | null;
}

export interface GraphEngine {
  fit: () => void;
  reset: () => void;
  zoomBy: (factor: number) => void;
  setView: (view: EngineView) => void;
  /**
   * An arrow key. Both views return the id of the nearest node in that direction and bring it into view (the 3D
   * camera glides to it), or null when there is none.
   */
  move: (fromId: string, direction: Direction) => string | null;
  /** Shift and an arrow key in the 3D view: turn the camera around the middle of the graph. The 2D view has none. */
  orbit?: (direction: Direction) => void;
  reveal: (id: string) => void;
  destroy: () => void;
}
