"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { RepeatedName } from "@/lib/repeated-names";

/** How many names show before "Show all"; enough for a glance without pushing the review queue off screen. */
export const REPEATED_NAMES_SHOWN = 8;

/**
 * Repeated-name warnings as a compact list: one row per name with its parent count, parents behind a
 * disclosure. Used by the round page and its review panel so both read the same way.
 */
export function RepeatedNames({ names }: { names: RepeatedName[] }) {
  const [showAll, setShowAll] = useState(false);
  if (names.length === 0) return null;
  const shown = showAll ? names : names.slice(0, REPEATED_NAMES_SHOWN);
  const levels = new Set(names.map((item) => item.level));
  const noun = levels.size === 1 ? `new ${[...levels][0]} name` : "new name";

  return (
    <div className="flex w-full flex-col gap-2">
      <p>
        {names.length} {noun}
        {names.length === 1 ? " is" : "s are"} proposed under more than one parent. Advisory only: placements and
        approvals are unchanged.
      </p>
      <ul className="flex w-full flex-col divide-y divide-rule border-y border-rule">
        {shown.map((item) => (
          <li key={`${item.level}:${item.name}`} className="py-1.5">
            {item.parents.length > 0 ? (
              <details className="group">
                <summary className="flex cursor-pointer items-baseline justify-between gap-3">
                  <span className="font-medium text-foreground">{item.name}</span>
                  <span className="shrink-0 tabular-nums">
                    {item.parents.length} parents
                  </span>
                </summary>
                <ul className="mt-1.5 columns-1 gap-6 pl-4 text-xs sm:columns-2">
                  {item.parents.map((parent) => (
                    <li key={parent} className="break-inside-avoid py-0.5">{parent}</li>
                  ))}
                </ul>
              </details>
            ) : (
              <p className="break-words">{item.message}</p>
            )}
          </li>
        ))}
      </ul>
      {names.length > REPEATED_NAMES_SHOWN && (
        <Button size="sm" variant="ghost" onClick={() => setShowAll((value) => !value)}>
          {showAll ? "Show fewer" : `Show all ${names.length}`}
        </Button>
      )}
    </div>
  );
}
