"use client";

import { useState } from "react";

import { Notice, Section } from "@/components/layout/page-header";
import { LevelTag } from "@/components/level-tag";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Skeleton } from "@/components/ui/skeleton";
import { useApplyDecisions, useDraftDecisions, useIntegrateRound, useReviewQueue, useSaveDecisions } from "@/lib/api/queries";
import type { ReviewQueueProposal, ReviewQueueTag, RoundState } from "@/lib/api/schemas";
import {
  bulkTagDecisions,
  type DecisionInput,
  placementLabel,
  proposalDecision,
  reuseCandidates,
  tagDecision,
  type TagAction,
} from "@/lib/review";
import { useDebouncedValue } from "@/lib/use-debounced-value";

const PAGE_SIZE = 25;
const FILTERS = [
  { id: "undecided", label: "To decide" },
  { id: "decided", label: "Decided" },
  { id: "all", label: "All" },
] as const;

export function ReviewPanel({ round }: { round: RoundState }) {
  const [reviewer, setReviewer] = useState(round.reviewer ?? "");
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["id"]>("undecided");
  const [searchInput, setSearchInput] = useState("");
  const search = useDebouncedValue(searchInput.trim(), 300);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkReason, setBulkReason] = useState("");
  const { data: queue, isPending, error } = useReviewQueue(round, { state: filter, q: search, offset, limit: PAGE_SIZE });
  const save = useSaveDecisions(round.round_id);
  const locked = round.status === "running";
  const named = reviewer.trim().length > 0;

  function submit(decisions: DecisionInput[], clearTags: ReviewQueueTag["source"][] = [], clearProposals: string[] = []) {
    save.mutate(
      { reviewer: reviewer.trim(), decisions, clear_tags: clearTags, clear_proposals: clearProposals },
      { onSuccess: () => setSelected(new Set()) },
    );
  }

  function changeFilter(next: typeof filter) {
    setFilter(next);
    setOffset(0);
    setSelected(new Set());
  }

  const selectedTags = (queue?.tags ?? []).filter((tag) => selected.has(tag.source.tag_id));
  const canEdit = named && !locked && !save.isPending;

  return (
    <Section
      title="Review"
      aside={queue && <span className="text-sm text-muted-foreground">{queue.undecided} to decide · {queue.decided} decided</span>}
    >
      <div className="flex flex-col gap-1.5">
        <Label className="label-caps" htmlFor="review-reviewer">Reviewer</Label>
        <Input
          id="review-reviewer"
          className="max-w-xs"
          placeholder="Your name, recorded with each decision"
          value={reviewer}
          onChange={(e) => setReviewer(e.target.value)}
        />
      </div>

      <ApplyBar round={round} />
      <DraftDecisions round={round} reviewer={reviewer} disabled={!canEdit} />

      {save.error && (
        <Notice title="That decision wasn't saved" tone="error">
          {save.error.message}
        </Notice>
      )}
      {isPending && <Skeleton className="h-40 w-full" />}
      {error && (
        <Notice title="Could not load the review queue" tone="error">
          {error.message}
        </Notice>
      )}
      {queue && queue.proposals.length > 0 && (
        <div className="flex flex-col gap-3">
          <h3 className="font-heading text-base">Categories and specialities to decide first</h3>
          <p className="text-sm text-muted-foreground">
            Tags can only be created under these once each one is settled.
          </p>
          {queue.proposals.map((proposal) => (
            <ProposalCard
              key={proposal.proposal_id}
              proposal={proposal}
              disabled={!canEdit}
              onDecide={(decision) => submit([decision])}
              onClear={() => submit([], [], [proposal.proposal_id])}
            />
          ))}
        </div>
      )}

      {queue && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            {FILTERS.map(({ id, label }) => (
              <Button key={id} size="sm" variant={filter === id ? "secondary" : "ghost"} onClick={() => changeFilter(id)}>
                {label}
              </Button>
            ))}
            <Input
              aria-label="Search tags"
              className="ml-auto max-w-56"
              placeholder="Search tags…"
              value={searchInput}
              onChange={(e) => {
                setSearchInput(e.target.value);
                setOffset(0);
              }}
            />
          </div>

          {queue.tags.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/50 p-3">
              <label className="flex shrink-0 items-center gap-2 text-sm whitespace-nowrap">
                <input
                  type="checkbox"
                  checked={selectedTags.length === queue.tags.length}
                  onChange={(e) => setSelected(new Set(e.target.checked ? queue.tags.map((tag) => tag.source.tag_id) : []))}
                />
                Select this page ({selectedTags.length} selected)
              </label>
              <Input
                aria-label="Reason for selected tags"
                className="min-w-48 flex-1"
                placeholder="Reason for the selected tags (optional)"
                value={bulkReason}
                onChange={(e) => setBulkReason(e.target.value)}
              />
              {(["create", "exclude", "defer"] as const).map((action) => (
                <Button
                  key={action}
                  size="sm"
                  variant="outline"
                  disabled={!canEdit || selectedTags.length === 0}
                  onClick={() => submit(bulkTagDecisions(selectedTags, action, bulkReason))}
                >
                  {action === "create" ? "Create at best placement" : action === "exclude" ? "Exclude" : "Defer"}
                </Button>
              ))}
            </div>
          )}

          {queue.tags.length === 0 && (
            <Notice title={filter === "undecided" ? "Nothing left to decide here" : "No tags match"}>
              {filter === "undecided" ? "Apply your decisions to see whether the round is ready to integrate." : "Try another filter or search."}
            </Notice>
          )}
          {queue.tags.map((tag) => (
            <TagCard
              key={`${tag.source.source_dataset}-${tag.source.tag_id}`}
              tag={tag}
              selected={selected.has(tag.source.tag_id)}
              disabled={!canEdit}
              onToggle={(checked) => {
                const next = new Set(selected);
                if (checked) next.add(tag.source.tag_id);
                else next.delete(tag.source.tag_id);
                setSelected(next);
              }}
              onDecide={(decision) => submit([decision])}
              onClear={() => submit([], [tag.source])}
            />
          ))}
          {queue.total > PAGE_SIZE && (
            <div className="flex items-center gap-3 text-sm text-muted-foreground">
              <Button size="sm" variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
                Previous
              </Button>
              <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, queue.total)} of {queue.total}</span>
              <Button size="sm" variant="outline" disabled={offset + PAGE_SIZE >= queue.total} onClick={() => setOffset(offset + PAGE_SIZE)}>
                Next
              </Button>
            </div>
          )}
        </div>
      )}
      {!named && <p className="text-sm text-muted-foreground">Enter your name to make decisions.</p>}
    </Section>
  );
}

/** What has to happen next: apply saved decisions, then integrate. */
function ApplyBar({ round }: { round: RoundState }) {
  const apply = useApplyDecisions(round.round_id);
  const integrate = useIntegrateRound(round.round_id);
  const running = round.status === "running";
  // A snapshot step that failed is retried from here too: its stage stops the round, but nothing is held.
  const canIntegrate = round.decisions_applied && round.pending_sources === 0 && !running;
  return (
    <div className="flex flex-wrap items-center gap-3 rounded-md border border-rule p-3">
      {!round.decisions_applied && (
        <>
          <p className="text-sm">Some saved decisions are not applied yet.</p>
          <Button disabled={running || apply.isPending} onClick={() => apply.mutate()}>Apply decisions</Button>
        </>
      )}
      {round.decisions_applied && round.pending_sources > 0 && (
        <p className="text-sm">{round.pending_sources} tag(s) still need a decision before this round can be integrated.</p>
      )}
      {canIntegrate && (
        <>
          <p className="text-sm">Nothing is held. Generating the snapshot adds these tags as a new taxonomy version.</p>
          <Button disabled={integrate.isPending} onClick={() => integrate.mutate()}>Generate enriched snapshot</Button>
        </>
      )}
      {running && <p className="text-sm text-muted-foreground">Working… this usually takes under a minute.</p>}
      {apply.error && <p className="text-sm text-destructive">{apply.error.message}</p>}
      {integrate.error && <p className="text-sm text-destructive">{integrate.error.message}</p>}
    </div>
  );
}

function DraftDecisions({ round, reviewer, disabled }: { round: RoundState; reviewer: string; disabled: boolean }) {
  const [createBelow, setCreateBelow] = useState("0.7");
  const [reuseAbove, setReuseAbove] = useState("0.9");
  const draft = useDraftDecisions(round.round_id);
  const counts = draft.data?.counts;
  return (
    <details className="rounded-md border border-border p-3">
      <summary className="cursor-pointer text-sm font-medium">Draft the clear-cut decisions</summary>
      <div className="mt-3 flex flex-col gap-3">
        <p className="text-sm text-muted-foreground">
          Creates tags nothing resembles and reuses near-identical ones, at their most confident placement. Drafts can be
          changed before you apply them. These cut-offs are uncalibrated, so check the results.
        </p>
        <div className="flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="draft-create">Create below</Label>
            <Input id="draft-create" className="w-24" type="number" min="0" max="1" step="0.05" value={createBelow} onChange={(e) => setCreateBelow(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="draft-reuse">Reuse at or above</Label>
            <Input id="draft-reuse" className="w-24" type="number" min="0" max="1" step="0.05" value={reuseAbove} onChange={(e) => setReuseAbove(e.target.value)} />
          </div>
          <Button
            variant="outline"
            disabled={disabled || draft.isPending || !round.decisions_applied}
            onClick={() => draft.mutate({ reviewer: reviewer.trim(), create_below: Number(createBelow), reuse_above: Number(reuseAbove) })}
          >
            Draft decisions
          </Button>
        </div>
        {!round.decisions_applied && <p className="text-sm text-muted-foreground">Apply your saved decisions first.</p>}
        {counts && (
          <p className="text-sm">
            Drafted {counts.create ?? 0} to create and {counts.reuse ?? 0} to reuse; {counts.left_for_review ?? 0} are too
            close to call and stay with you.
          </p>
        )}
        {draft.error && <p className="text-sm text-destructive">{draft.error.message}</p>}
      </div>
    </details>
  );
}

function SavedDecision({ decision, disabled, onClear }: { decision: { action: string; reason: string }; disabled: boolean; onClear: () => void }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-muted/50 px-3 py-2 text-sm">
      <span><span className="font-medium capitalize">{decision.action}</span>: {decision.reason}</span>
      <Button size="xs" variant="ghost" disabled={disabled} onClick={onClear}>Undo</Button>
    </div>
  );
}

function TagCard({
  tag, selected, disabled, onToggle, onDecide, onClear,
}: {
  tag: ReviewQueueTag;
  selected: boolean;
  disabled: boolean;
  onToggle: (checked: boolean) => void;
  onDecide: (decision: DecisionInput) => void;
  onClear: () => void;
}) {
  const [placement, setPlacement] = useState(0);
  const candidates = reuseCandidates(tag);
  const [reuseTagId, setReuseTagId] = useState<number | undefined>(candidates[0]?.tag_id);
  const [reason, setReason] = useState("");

  function decide(action: TagAction) {
    const decision = tagDecision(tag, action, { placement, reuseTagId, reason });
    if (decision) onDecide(decision);
  }

  return (
    <article className="flex flex-col gap-3 rounded-md border border-border p-3">
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1"
          aria-label={`Select ${tag.tag}`}
          checked={selected}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <div className="flex flex-col gap-1">
          <h4 className="font-medium">{tag.tag}</h4>
          <p className="text-sm text-muted-foreground">{tag.reason}</p>
        </div>
      </div>
      {tag.closest.length > 0 && (
        <p className="text-sm">
          <span className="text-muted-foreground">Closest existing: </span>
          {tag.closest.map((match) => `${match.name ?? "unnamed"} (${match.level ?? "unknown level"}, ${match.basis} ${match.score.toFixed(2)})`).join("; ")}
        </p>
      )}
      {tag.duplicates.length > 0 && (
        <p className="text-sm">
          <span className="text-muted-foreground">May duplicate: </span>
          {tag.duplicates.map((other) => other.tag).join(", ")}
        </p>
      )}
      {tag.decision ? (
        <SavedDecision decision={tag.decision} disabled={disabled} onClear={onClear} />
      ) : (
        <div className="flex flex-col gap-2">
          {tag.placements.length > 0 && (
            <div className="flex flex-col gap-1">
              <Label htmlFor={`place-${tag.source.tag_id}`}>Place it under ({tag.placements.length} proposed)</Label>
              <NativeSelect
                id={`place-${tag.source.tag_id}`}
                value={placement}
                onChange={(e) => setPlacement(Number(e.target.value))}
              >
                {tag.placements.map((place, index) => (
                  <option key={placementLabel(place)} value={index}>
                    {placementLabel(place)}{place.confidence === null ? "" : ` (${place.confidence.toFixed(2)})`}
                  </option>
                ))}
              </NativeSelect>
            </div>
          )}
          {candidates.length > 0 && (
            <div className="flex flex-col gap-1">
              <Label htmlFor={`reuse-${tag.source.tag_id}`}>Or map it to an existing tag</Label>
              <NativeSelect
                id={`reuse-${tag.source.tag_id}`}
                value={reuseTagId}
                onChange={(e) => setReuseTagId(Number(e.target.value))}
              >
                {candidates.map((candidate) => (
                  <option key={candidate.tag_id} value={candidate.tag_id}>
                    {candidate.name} ({candidate.score.toFixed(2)})
                  </option>
                ))}
              </NativeSelect>
            </div>
          )}
          <Input aria-label={`Reason for ${tag.tag}`} placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" disabled={disabled || tag.placements.length === 0} onClick={() => decide("create")}>Create</Button>
            <Button size="sm" variant="outline" disabled={disabled || candidates.length === 0 || tag.placements.length === 0} onClick={() => decide("reuse")}>Reuse existing</Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("exclude")}>Exclude</Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("defer")}>Defer</Button>
          </div>
        </div>
      )}
    </article>
  );
}

function ProposalCard({
  proposal, disabled, onDecide, onClear,
}: {
  proposal: ReviewQueueProposal;
  disabled: boolean;
  onDecide: (decision: DecisionInput) => void;
  onClear: () => void;
}) {
  const reusable = proposal.matches.filter((match) => match.reusable && match.id !== null);
  const [conceptId, setConceptId] = useState(reusable[0]?.id ?? undefined);
  const [reason, setReason] = useState("");

  function decide(action: TagAction) {
    const decision = proposalDecision(proposal, action, { conceptId: conceptId ?? undefined, reason });
    if (decision) onDecide(decision);
  }

  return (
    <article className="flex flex-col gap-3 rounded-md border border-border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="font-medium">{proposal.path.join(" › ")}</h4>
        <LevelTag level={proposal.level} />
      </div>
      <p className="text-sm text-muted-foreground">{proposal.reason} {proposal.source_tags} tag(s) depend on this.</p>
      {proposal.decision ? (
        <SavedDecision decision={proposal.decision} disabled={disabled} onClear={onClear} />
      ) : (
        <div className="flex flex-col gap-2">
          {reusable.length > 0 && (
            <div className="flex flex-col gap-1">
              <Label htmlFor={`concept-${proposal.proposal_id}`}>Existing {proposal.level} it may be</Label>
              <NativeSelect
                id={`concept-${proposal.proposal_id}`}
                value={String(conceptId)}
                onChange={(e) => setConceptId(reusable.find((match) => String(match.id) === e.target.value)?.id ?? undefined)}
              >
                {reusable.map((match) => (
                  <option key={String(match.id)} value={String(match.id)}>
                    {match.path.join(" › ")}{match.same_parent ? "" : " (different parent)"}
                  </option>
                ))}
              </NativeSelect>
            </div>
          )}
          <Input aria-label={`Reason for ${proposal.name}`} placeholder="Reason (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" disabled={disabled || reusable.length === 0} onClick={() => decide("reuse")}>Use existing</Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("create")}>Create new</Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("exclude")}>Exclude</Button>
            <Button size="sm" variant="ghost" disabled={disabled} onClick={() => decide("defer")}>Defer</Button>
          </div>
        </div>
      )}
    </article>
  );
}
