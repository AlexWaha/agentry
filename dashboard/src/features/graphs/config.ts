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
/** Space kept between two labels, so they never overlap. */
export const LABEL_GAP_PX = 4;

/** How edges and nodes look in the 2D view: opacity and width in screen pixels. */
export const BASE_LINK_ALPHA = 0.6;
export const BASE_LINK_PX = 1.25;
export const DIM_LINK_ALPHA = 0.04;
export const DIM_NODE_ALPHA = 0.1;
export const LIT_LINK_PX = 3;
export const RING_PX = 2;
export const ACTIVE_RING_PX = 3.5;

/** 3D view: the camera starts this far from the centre, and an arrow key orbits it by this angle (radians). */
export const GRAPH3D_START_DISTANCE = 220;
export const GRAPH3D_ORBIT_STEP = 0.2;
export const GRAPH3D_FOCUS_DISTANCE = 200;
/** The camera also stays this many node radii away from a node it flies to, so a big hub does not fill the view. */
export const GRAPH3D_FOCUS_RADII = 7;
/** The 3D node radius is the 2D one times this, as the 3D layout is larger. */
export const GRAPH3D_RADIUS_SCALE = 1.4;
/** How far dimmed colours blend into the background (0 none, 1 invisible) in the 3D view. */
export const GRAPH3D_DIM_MIX = 0.8;
/** The 3D link colour blends this far into the background so base links read as lines, not as bars. */
export const GRAPH3D_BASE_LINK_MIX = 0.05;
export const GRAPH3D_LIT_LINK_WIDTH = 7;
export const GRAPH3D_BASE_LINK_WIDTH = 1.3;
export const GRAPH3D_PARTICLES = 5;
export const GRAPH3D_PARTICLE_WIDTH = 7;
export const GRAPH3D_PARTICLE_SPEED = 0.012;
/** The halo around an active node is its radius times this. */
export const GRAPH3D_HALO_SCALE = 1.6;
export const GRAPH3D_HALO_OPACITY = 0.6;
export const GRAPH3D_ACTIVE_HALO_OPACITY = 0.8;
/** Fit to view in 3D ignores the farthest nodes: those beyond this many times the 90th percentile distance from the centre. */
export const GRAPH3D_FIT_OUTLIER_FACTOR = 1.1;
export const GRAPH3D_FIT_MIN_NODES = 20;
/** The 3D forces: links this long, nodes pushing each other apart this hard. */
export const GRAPH3D_LINK_DISTANCE = 42;
export const GRAPH3D_CHARGE = -45;
/** The 3D layout is computed this many ticks ahead before the first frame when motion is reduced. */
export const GRAPH3D_WARMUP_TICKS = 200;
/** The automatic fits of the 3D view, as the layout spreads: the last one follows the end of the simulation. */
export const GRAPH3D_FIT_DELAYS_MS = [1200, 4000] as const;
/** A lit node is drawn this many times larger, the active one more, so the selection reads at fit. */
export const GRAPH3D_LIT_NODE_SCALE = 1.9;
export const GRAPH3D_ACTIVE_NODE_SCALE = 2.4;
/** The 3D render loop sleeps this long after the layout has settled and nothing is highlighted. */
export const GRAPH3D_IDLE_SLEEP_MS = 2000;
/** The 3D camera stays between these distances from what it orbits, however it is moved. */
export const GRAPH3D_MIN_DISTANCE = 25;
export const GRAPH3D_MAX_DISTANCE = 6000;
/** The camera's vertical field of view in degrees when it cannot be read (three.js default of 3d-force-graph). */
export const GRAPH3D_DEFAULT_FOV = 50;
/** Labels of the active node's neighbours in 3D: how many at most, and how wide and tall each is estimated. */
export const GRAPH3D_NEIGHBOUR_LABELS = 14;
export const GRAPH3D_LABEL_CHAR_PX = 6.4;
export const GRAPH3D_LABEL_PAD_PX = 14;
export const GRAPH3D_LABEL_HEIGHT_PX = 22;

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
