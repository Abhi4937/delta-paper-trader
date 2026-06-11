"use client";

import { X } from "lucide-react";
import OrderBook from "@/components/OrderBook";

// Mobile-only bottom sheet that surfaces the L2 depth on demand (tap a strike's mark in
// view mode) instead of the always-present side panel. The chain stays mounted underneath.
// Desktop keeps its persistent side OrderBook, so this is `lg:hidden`.
export default function MarketDepthSheet({
  open,
  symbol,
  mark,
  ltp,
  onClose,
}: {
  open: boolean;
  symbol: string | null;
  mark: number | null;
  ltp: number | null;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-40 lg:hidden" role="dialog" aria-modal="true" aria-label="Market depth">
      <button aria-label="Close depth" className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="absolute inset-x-0 bottom-0 flex max-h-[78vh] flex-col rounded-t-2xl border-t border-line bg-surface shadow-2xl">
        <div className="flex items-center justify-between px-3 py-2">
          <span className="text-[13px] font-semibold text-text">Market Depth</span>
          <button
            aria-label="Close depth"
            onClick={onClose}
            className="grid h-9 w-9 place-items-center rounded text-text-mute hover:text-text-dim"
          >
            <X size={18} />
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-auto">
          <OrderBook symbol={symbol} mark={mark} ltp={ltp} />
        </div>
      </div>
    </div>
  );
}
