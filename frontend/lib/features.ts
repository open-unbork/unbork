"use client"

import { useQuery } from "@tanstack/react-query"

import { apiFetch } from "@/lib/api"
import type { Features, VersionInfo } from "@/lib/types"

/**
 * What a server without a `features` field (older than C-026) is taken to
 * have: everything, which is what it had.
 */
export const ALL_FEATURES: Features = { assistant: true }

/** The features a `/api/version` answer reports. */
export function featuresOf(info: VersionInfo | undefined): Features {
  return { ...ALL_FEATURES, ...(info?.features ?? {}) }
}

/**
 * Optional parts this server loads, from the public `/api/version`.
 *
 * Shares the About panel's query. `ready` is false until the answer is in,
 * so a page that hides itself without the assistant can wait instead of
 * flashing its content first. While loading, everything reads as present:
 * the server's own refusals (no route, no permission) are the real guard.
 */
export function useFeatures(): Features & { ready: boolean } {
  const { data, isSuccess, isError } = useQuery<VersionInfo>({
    queryKey: ["version"],
    queryFn: () => apiFetch<VersionInfo>("/api/version"),
    staleTime: Infinity,
    retry: 1,
  })
  return { ...featuresOf(data), ready: isSuccess || isError }
}
