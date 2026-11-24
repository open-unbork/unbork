"use client"

import { AssistantAbsent } from "@/components/assistant-absent"
import { useFeatures } from "@/lib/features"
import AIProvidersPage from "./client-page"

export default function Page() {
  const features = useFeatures()
  if (!features.ready) return null
  if (!features.assistant) return <AssistantAbsent title="AI providers" />
  return <AIProvidersPage />
}
