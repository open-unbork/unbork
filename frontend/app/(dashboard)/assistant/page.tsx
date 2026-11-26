"use client"

import { Suspense } from "react"

import { Empty, PageHead } from "@/components/ld"
import { AssistantAbsent } from "@/components/assistant-absent"
import { can, useAuth } from "@/lib/auth"
import { useFeatures } from "@/lib/features"
import AssistantPage from "./client-page"

/**
 * The Suspense boundary is required, not decorative: `useSearchParams` in
 * the client page opts the route into client-side rendering, and Next
 * fails the production build outright without a boundary around it.
 *
 * A viewer has no `ai.use`: every request the assistant makes would be
 * refused, so they are told so instead of shown a page of errors.
 */
export default function Page() {
  const { user, loading } = useAuth()
  const features = useFeatures()
  if (loading || !features.ready) return null
  if (!features.assistant) return <AssistantAbsent title="Assistant" />
  if (!can(user, "ai.use")) {
    return (
      <>
        <PageHead title="Assistant" />
        <Empty title="Operators only" note="The assistant runs commands on your hosts, so it needs the operator role. Ask an admin for it." />
      </>
    )
  }
  return (
    <Suspense fallback={<p className="m-0 p-4 text-xs text-text-3">Loading the assistant…</p>}>
      <AssistantPage />
    </Suspense>
  )
}
