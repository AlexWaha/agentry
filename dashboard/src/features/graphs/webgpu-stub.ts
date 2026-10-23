/*
 * Stands in for `three/webgpu` (see vite.config.ts). three-render-objects, which 3d-force-graph builds on, imports
 * the WebGPU renderer for an opt-in this dashboard never turns on (it always asks for WebGL). The real module is
 * about a megabyte of shader-node code, so the build aliases it to this stub to keep the 3D chunk small.
 */
export class WebGPURenderer {
  constructor() {
    throw new Error("WebGPU is not available in this build");
  }
}
