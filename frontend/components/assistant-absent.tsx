"use client"

import { Empty, PageHead } from "@/components/ld"

/**
 * What an assistant page shows on a server that leaves the assistant out
 * (C-026): the page does not exist there, and says why.
 */
export function AssistantAbsent({ title }: { title: string }) {
  return (
    <>
      <PageHead title={title} />
      <Empty
        title="Page not found"
        note="The assistant is not part of this unbork install. An administrator can turn it on in the server's configuration file ([assistant] enabled)."
      />
    </>
  )
}
