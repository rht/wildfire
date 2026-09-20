// The generated dashboard lives in sessionStorage only: it ends with the browser tab and
// never reaches the backend. With nothing stored, the dashboard uses its configured source.
import { useSyncExternalStore } from "react";
import { isConfig } from "./config.mjs";

export const SESSION_KEY = "responsara.onboarding.v1";

function stored() {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw);
    return isConfig(value) ? value : null;
  } catch {
    // A blocked or corrupt session store must leave the configured source working.
    return null;
  }
}

const listeners = new Set();
let current;
const snapshot = () => (current === undefined ? (current = stored()) : current);
const publish = (value) => {
  current = value;
  for (const listener of listeners) listener();
};
const subscribe = (listener) => {
  listeners.add(listener);
  return () => listeners.delete(listener);
};

export function saveOnboarding(config) {
  if (!isConfig(config)) throw new Error("Incomplete onboarding configuration");
  sessionStorage.setItem(SESSION_KEY, JSON.stringify(config));
  publish(config);
  return config;
}

export function clearOnboarding() {
  sessionStorage.removeItem(SESSION_KEY);
  publish(null);
}

export function useOnboarding() {
  return useSyncExternalStore(subscribe, snapshot, () => null);
}
