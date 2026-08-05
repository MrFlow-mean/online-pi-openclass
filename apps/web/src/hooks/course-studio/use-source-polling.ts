"use client";

import { useEffect } from "react";

import type { SourceIngestionRecord } from "@/types";

const ACTIVE_SOURCE_STATUSES = new Set<SourceIngestionRecord["status"]>([
  "queued",
  "fetching",
  "parsing",
  "indexing",
]);
const SOURCE_POLL_DELAYS_MS = [1000, 2000, 5000, 10000] as const;

type UseSourcePollingOptions = {
  disabled: boolean;
  sources: SourceIngestionRecord[];
  refreshSources: () => Promise<SourceIngestionRecord[] | null | undefined>;
};

export function useSourcePolling({ disabled, sources, refreshSources }: UseSourcePollingOptions) {
  const fingerprint = activeSourceFingerprint(sources);

  useEffect(() => {
    if (disabled || !fingerprint) {
      return;
    }
    let cancelled = false;
    let attempt = 0;
    let timerId: number | null = null;

    const schedule = () => {
      const delay = SOURCE_POLL_DELAYS_MS[Math.min(attempt, SOURCE_POLL_DELAYS_MS.length - 1)];
      timerId = window.setTimeout(() => {
        void refreshSources().then((nextSources) => {
          if (cancelled) {
            return;
          }
          if (!nextSources || activeSourceFingerprint(nextSources) === fingerprint) {
            attempt += 1;
            schedule();
          }
        });
      }, delay);
    };
    schedule();
    return () => {
      cancelled = true;
      if (timerId !== null) {
        window.clearTimeout(timerId);
      }
    };
  }, [disabled, fingerprint, refreshSources]);
}

export function sourceNeedsRefresh(source: SourceIngestionRecord) {
  if (ACTIVE_SOURCE_STATUSES.has(source.status)) {
    return true;
  }
  return Boolean(source.ingestion_job && ACTIVE_SOURCE_STATUSES.has(source.ingestion_job.status));
}

export function activeSourceFingerprint(sources: SourceIngestionRecord[]) {
  return sources
    .filter(sourceNeedsRefresh)
    .map((source) =>
      [
        source.id,
        source.status,
        source.ingestion_job?.id ?? "",
        source.ingestion_job?.status ?? "",
        source.ingestion_job?.progress ?? "",
        source.updated_at,
      ].join(":")
    )
    .sort()
    .join("|");
}
