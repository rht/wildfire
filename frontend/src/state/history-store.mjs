// Only public browser-projected snapshots are stored, isolated by origin and source scope.
const database = () =>
  new Promise((resolve, reject) => {
    const request = indexedDB.open("responsara-snapshot-history", 1);
    request.onupgradeneeded = () =>
      request.result.createObjectStore("snapshots", { keyPath: "id" });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
export async function savedHistory(scope, entry) {
  const db = await database();
  try {
    return await new Promise((resolve, reject) => {
      const transaction = db.transaction(
        "snapshots",
        entry ? "readwrite" : "readonly",
      );
      const store = transaction.objectStore("snapshots");
      if (entry) store.put({ id: `${scope}:${entry.key}`, scope, entry });
      const request = entry ? null : store.getAll();
      transaction.oncomplete = () =>
        resolve(
          request
            ? request.result
                .filter((row) => row.scope === scope)
                .map((row) => row.entry)
            : [],
        );
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally {
    db.close();
  }
}
export const historyKey = (incidents) =>
  JSON.stringify(incidents.map((i) => [i.id, i.snapshot_id, i.revision]));
export const historyEntry = (incidents) => ({
  key: historyKey(incidents),
  incidents,
  at: incidents[0].as_of,
  revision: incidents[0].revision,
});
export function mergeHistory(entries) {
  return [...new Map(entries.map((entry) => [entry.key, entry])).values()].sort(
    (a, b) => Date.parse(a.at) - Date.parse(b.at) || a.revision - b.revision,
  );
}
