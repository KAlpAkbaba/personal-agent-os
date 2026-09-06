import type { Metadata } from "next";

import { CORE_BUILD_ID } from "../lib/uistate/contract";

/**
 * The `/core` route layout exists for one server-rendered fact: the build marker.
 *
 * `scripts/core/owner-m18-3-core.ps1` fetches `/core` with no session and reads
 * `<meta name="pagentos-core-build" content="...">` from the document head to prove the
 * served build is the Living Core before asking the owner to look at anything. The gate,
 * the voice session and the Core itself are all client-side; this is the only thing the
 * server can say about the build, and it says it before sign-in on purpose.
 */
export const metadata: Metadata = {
  title: "PersonalAgentOS Core",
  other: { "pagentos-core-build": CORE_BUILD_ID },
};

export default function CoreLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return children;
}
