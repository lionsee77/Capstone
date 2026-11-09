"use client";

import { useState } from "react";

import { LevelTag } from "@/components/level-tag";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import type { ReviewQueueProposal, ReviewQueueTag } from "@/lib/api/schemas";
import {
  ACTION_LABELS,
  actionLabel,
  closestMatchLabel,
  type DecisionInput,
  heldExplanation,
  placementLabel,
  proposalDecision,
  reasonLabels,
  reusableMatches,
  reuseCandidateLabel,
  reuseCandidates,
  tagDecision,
  type TagAction,
} from "@/lib/review";

function SavedDecision({ decision, disabled, onClear }: { decision: { action: string; reason: string }; disabled: boolean; onClear: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm">
      <span><span className="font-medium">{actionLabel(decision.action)}</span>: {decision.reason}</span>
      <Button size="xs" variant="ghost" disabled={disabled} onClick={onClear}>Change</Button>
    </div>
  );
}

/**
 * One held tag: why it was held and the choices, on as few lines as possible, because a queue can run to
 * hundreds of these. The evidence behind the plain reason is a click away for the tags that need it.
 */
export function TagRow({
  tag, held, selected, disabled, onToggle, onDecide, onClear,
}: {
  tag: ReviewQueueTag;
  held: boolean;
  selected: boolean;
  disabled: boolean;
  onToggle: (checked: boolean) => void;
  onDecide: (decision: DecisionInput) => void;
  onClear: () => void;
}) {
  const [placement, setPlacement] = useState(0);
  const candidates = reuseCandidates(tag);
  // Nothing preselected: these are only the closest names, and most aren't the same concept.
  const [reuseTagId, setReuseTagId] = useState<number | undefined>(undefined);
  const [reason, setReason] = useState("");

  function decide(action: TagAction) {
    const decision = tagDecision(tag, action, { placement, reuseTagId, reason });
    if (decision) onDecide(decision);
  }

  return (
    <article className="flex flex-col gap-2 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <input type="checkbox" aria-label={`Select ${tag.tag}`} checked={selected} onChange={(e) => onToggle(e.target.checked)} />
        <h3 className="font-medium">{tag.tag}</h3>
        {reasonLabels(tag).map((label) => <Badge key={label} variant="secondary">{label}</Badge>)}
      </div>
      {held && <p className="text-sm font-medium">{heldExplanation(tag)}</p>}
      {tag.decision ? (
        <SavedDecision decision={tag.decision} disabled={disabled} onClear={onClear} />
      ) : (
        <>
          <div className="grid gap-2 md:grid-cols-2">
            {tag.placements.length > 0 ? (
              <div className="flex min-w-0 flex-col gap-1">
                <Label htmlFor={`place-${tag.source.tag_id}`} className="text-muted-foreground">
                  Place it under{tag.placements.length > 1 ? ` (${tag.placements.length} options, best first)` : ""}
                </Label>
                <NativeSelect id={`place-${tag.source.tag_id}`} value={placement} onChange={(e) => setPlacement(Number(e.target.value))}>
                  {tag.placements.map((place, index) => (
                    <option key={placementLabel(place)} value={index}>
                      {placementLabel(place)}{place.confidence === null ? "" : ` (${place.confidence.toFixed(2)})`}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No placement was proposed, so this tag can only be left out or decided later.</p>
            )}
            {candidates.length > 0 && tag.placements.length > 0 && (
              <div className="flex min-w-0 flex-col gap-1">
                <Label htmlFor={`reuse-${tag.source.tag_id}`} className="text-muted-foreground">Is it the same as an existing tag?</Label>
                <NativeSelect
                  id={`reuse-${tag.source.tag_id}`}
                  value={reuseTagId ?? ""}
                  onChange={(e) => setReuseTagId(e.target.value ? Number(e.target.value) : undefined)}
                >
                  <option value="">No, or not sure</option>
                  {candidates.map((candidate) => (
                    <option key={candidate.tag_id} value={candidate.tag_id}>
                      {reuseCandidateLabel(candidate)}
                    </option>
                  ))}
                </NativeSelect>
              </div>
            )}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {reuseTagId === undefined ? (
              <Button size="sm" disabled={disabled || tag.placements.length === 0} onClick={() => decide("create")}>{ACTION_LABELS.create}</Button>
            ) : (
              <Button size="sm" disabled={disabled} onClick={() => decide("reuse")}>{ACTION_LABELS.reuse}</Button>
            )}
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("exclude")}>{ACTION_LABELS.exclude}</Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("defer")}>{ACTION_LABELS.defer}</Button>
          </div>
        </>
      )}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">{tag.decision ? "Details" : "Details and a note"}</summary>
        <div className="mt-2 flex flex-col gap-2">
          <p><span className="text-muted-foreground">Why it&apos;s here: </span>{tag.reason}</p>
          {tag.closest.length > 0 && (
            <p><span className="text-muted-foreground">Closest existing: </span>{tag.closest.map(closestMatchLabel).join("; ")}</p>
          )}
          {tag.duplicates.length > 0 && (
            <p><span className="text-muted-foreground">May duplicate: </span>{tag.duplicates.map((other) => other.tag).join(", ")}</p>
          )}
          {!tag.decision && (
            <Input aria-label={`Reason for ${tag.tag}`} className="max-w-lg" placeholder="Note for your decision (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          )}
        </div>
      </details>
    </article>
  );
}

export function ProposalCard({
  proposal, disabled, onDecide, onClear,
}: {
  proposal: ReviewQueueProposal;
  disabled: boolean;
  onDecide: (decision: DecisionInput) => void;
  onClear: () => void;
}) {
  const reusable = reusableMatches(proposal);
  const [conceptId, setConceptId] = useState(reusable[0]?.id ?? undefined);
  const [reason, setReason] = useState("");
  const blocked = proposal.create_blocked_reason;

  function decide(action: TagAction) {
    const decision = proposalDecision(proposal, action, { conceptId: conceptId ?? undefined, reason });
    if (decision) onDecide(decision);
  }

  return (
    <article className="flex flex-col gap-2 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-medium">{proposal.path.join(" › ")}</h3>
        <LevelTag level={proposal.level} />
        <span className="text-sm text-muted-foreground">{proposal.source_tags.toLocaleString()} tag(s) wait on this</span>
      </div>
      {proposal.decision ? (
        <SavedDecision decision={proposal.decision} disabled={disabled} onClear={onClear} />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">{blocked ?? proposal.reason}</p>
          {reusable.length > 0 && (
            <div className="flex flex-col gap-1">
              <Label htmlFor={`concept-${proposal.proposal_id}`} className="text-muted-foreground">Existing {proposal.level} it may be</Label>
              <NativeSelect
                id={`concept-${proposal.proposal_id}`}
                value={String(conceptId)}
                onChange={(e) => setConceptId(reusable.find((match) => String(match.id) === e.target.value)?.id ?? undefined)}
              >
                {reusable.map((match) => (
                  <option key={String(match.id)} value={String(match.id)}>
                    {match.path.join(" › ")}
                  </option>
                ))}
              </NativeSelect>
            </div>
          )}
          <Input aria-label={`Reason for ${proposal.name}`} placeholder="Note for your decision (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            {reusable.length > 0 && <Button size="sm" disabled={disabled} onClick={() => decide("reuse")}>{ACTION_LABELS.reuse}</Button>}
            {!blocked && (
              <Button size="sm" variant={reusable.length > 0 ? "outline" : "default"} disabled={disabled} onClick={() => decide("create")}>
                {ACTION_LABELS.create}
              </Button>
            )}
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("exclude")}>{ACTION_LABELS.exclude}</Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("defer")}>{ACTION_LABELS.defer}</Button>
          </div>
        </>
      )}
    </article>
  );
}
