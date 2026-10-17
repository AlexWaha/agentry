import { useSyncExternalStore } from "react";

import { STORAGE_KEYS } from "@/config/storage-keys";

/*
 * The per-launch token (FR-79). The launch URL carries it in the fragment, `/#token=<t>`, which the browser
 * never sends to the server. bootToken() stores it and removes the fragment before the first render; later
 * loads use the stored copy. A 401 marks the token expired and the shell shows the expired-link page.
 */

let memoryToken = "";
let expired = false;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

function readStored(): string {
  try {
    return localStorage.getItem(STORAGE_KEYS.token) ?? "";
  } catch {
    return "";
  }
}

function store(token: string): void {
  memoryToken = token;
  try {
    localStorage.setItem(STORAGE_KEYS.token, token);
  } catch {
    // Storage blocked: the token lasts for this page load only.
  }
}

/** Reads `#token=` from the URL, stores it and removes the fragment. Call once, before the first render. */
export function bootToken(): void {
  const fromHash = new URLSearchParams(window.location.hash.replace(/^#/, "")).get("token");
  if (!fromHash) return;
  store(fromHash);
  expired = false;
  window.history.replaceState(window.history.state, "", window.location.pathname + window.location.search);
}

/** The token to send; empty when there is none (the first request then answers 401). */
export function getToken(): string {
  return memoryToken || readStored();
}

export function markTokenExpired(): void {
  if (expired) return;
  expired = true;
  emit();
}

export function isTokenExpired(): boolean {
  return expired;
}

/** Forgets the token and the expired flag. Used by tests. */
export function resetToken(): void {
  memoryToken = "";
  expired = false;
  try {
    localStorage.removeItem(STORAGE_KEYS.token);
  } catch {
    // Nothing stored.
  }
  emit();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useTokenExpired(): boolean {
  return useSyncExternalStore(subscribe, isTokenExpired, () => false);
}
