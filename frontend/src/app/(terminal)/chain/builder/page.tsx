"use client";

import { useRouter } from "next/navigation";
import StrategyBuilder from "@/components/StrategyBuilder";

// Full-screen strategy builder for mobile (reached via the chain's sticky "Done" bar).
// Reuses StrategyBuilder in its `page` variant — same basket/margin/payoff/place logic,
// just full-height instead of the desktop side panel. The basket lives in the Zustand
// store, so client nav from the chain preserves the selected legs.
export default function BuilderPage() {
  const router = useRouter();
  return (
    <div className="flex h-full flex-col">
      <StrategyBuilder page onClose={() => router.push("/chain")} />
    </div>
  );
}
