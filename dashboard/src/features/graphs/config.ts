/*
 * Tuning values of the graph view (FR-106 to FR-108). They belong to this feature alone, so they live here and
 * not in the global config. Distances are in graph units, sizes in screen pixels.
 */

/** Most nodes the drawing takes; the code graph API caps at the same number. */
export const MAX_DRAWN_NODES = 400;

/** The code graph is requested at the API maximum so the connected cap has a full set to choose from. */
export const CODE_GRAPH_LIMIT = 400;

/** The wheel, pinch and Fit controls never leave this scale range. */
export const ZOOM_MIN = 0.2;
export const ZOOM_MAX = 8;
/** Fit to view does not blow a small graph up beyond this scale. */
export const FIT_MAX_SCALE = 2;
export const FIT_PADDING = 36;
/** One keyboard zoom step (+ and -) multiplies the scale by this factor. */
export const ZOOM_STEP = 1.25;
export const VIEW_ANIMATION_MS = 320;

/** Labels of hubs show at every zoom; every node shows its label once the scale reaches the threshold. */
export const LABEL_ZOOM_THRESHOLD = 1.6;
export const LABEL_TOP_HUBS = 10;
export const LABEL_FONT_PX = 11;

/** Physics of the live simulation. */
export const LINK_DISTANCE = 56;
export const CHARGE = -110;
export const CENTER_PULL = 0.05;
export const COLLIDE_GAP = 3;
/** The alpha a held node keeps the simulation at, so the rest of the graph keeps moving around it. */
export const DRAG_ALPHA = 0.3;
/** The alpha a Reset restarts the simulation at. */
export const RESET_ALPHA = 0.6;
/** Ticks per animation frame while the simulation runs. */
export const TICKS_PER_FRAME = 2;
/** The first automatic fit happens once the simulation has cooled below this alpha. */
export const EARLY_FIT_ALPHA = 0.35;

/** Node circle radius from its weight, so hubs read larger. */
export const MIN_RADIUS = 5;
export const MAX_RADIUS = 16;
export const RADIUS_PER_SQRT_WEIGHT = 2.2;
/** A pointer within this many pixels of a node still hits it. */
export const HIT_SLACK_PX = 4;

/** Drawing size used before the canvas has a layout (and in tests). */
export const FALLBACK_WIDTH = 800;
export const FALLBACK_HEIGHT = 520;

/** Rows per page of the "Show as table" lists. */
export const TABLE_PAGE_SIZE = 100;
