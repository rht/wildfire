import { useEffect, useState } from "react";
import {
  savedHistory,
  historyKey,
  historyEntry,
  mergeHistory,
} from "./history-store.mjs";
export function useTimeline(incidents, source) {
  // Only live coordination snapshots are kept on the device; generated data stays in memory.
  const simulated = source === "design_demo";
  const persist = source === "connected";
  const scope = JSON.stringify([
    source,
    ...incidents.map((i) => [i.id, i.epoch, i.input_mode]),
  ]);
  const key = historyKey(incidents);
  const [archive, setArchive] = useState({ scope: null, entries: [] });
  const [selection, setSelection] = useState(null);
  const [storageError, setStorageError] = useState(false);
  useEffect(() => {
    let cancelled = false;
    setStorageError(false);
    const load = async () => {
      try {
        const entries = simulated
          ? (
              await (await fetch("/assets/design-history.json")).json()
            ).entries.map((row) => historyEntry(row.incidents))
          : persist
            ? await savedHistory(scope)
            : [];
        if (!cancelled)
          setArchive((previous) => ({
            scope,
            entries: mergeHistory([
              ...entries,
              ...(previous.scope === scope ? previous.entries : []),
            ]),
          }));
      } catch {
        if (!cancelled) setStorageError(true);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [simulated, persist, scope]);
  useEffect(() => {
    if (!incidents.length) return;
    const entry = historyEntry(incidents);
    setArchive((previous) => ({
      scope,
      entries: mergeHistory([
        ...(previous.scope === scope ? previous.entries : []),
        entry,
      ]),
    }));
    if (persist)
      void savedHistory(scope, entry).catch(() => setStorageError(true));
  }, [incidents, persist, scope]);
  const entries =
    archive.scope === scope
      ? archive.entries.filter(
          (entry) => simulated || entry.revision <= incidents[0]?.revision,
        )
      : [];
  const selected = entries.find((entry) => entry.key === selection);
  const historical = !!selected && selected.key !== key;
  const index = historical
    ? entries.indexOf(selected)
    : Math.max(
        0,
        entries.findIndex((entry) => entry.key === key),
      );
  return {
    entries,
    index,
    historical,
    storageError,
    simulated,
    historyLabel: simulated
      ? "Generated demo history · illustrative simulated spread"
      : persist
        ? "History saved on this device"
        : "This browser session only",
    incidents: historical ? selected.incidents : incidents,
    select: (index) =>
      setSelection(
        entries[index]?.key === key ? null : entries[index]?.key || null,
      ),
    latest: () => setSelection(null),
  };
}
