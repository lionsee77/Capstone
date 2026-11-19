"use client";

import { useState } from "react";

import { Notice } from "@/components/layout/page-header";
import { RepeatedNames } from "@/components/repeated-names";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { useApplyDecisions, useDraftDecisions, useIntegrateRound, useReviewQueue, useSaveDecisions } from "@/lib/api/queries";
import type { ReviewQueue, ReviewQueueTag, RoundState } from "@/lib/api/schemas";
import {
  ACTION_LABELS,
  bulkTagDecisions,
  clampOffset,
  type DecisionInput,
  reviewNextStep,
  reviewProgress,
  type ReviewTab,
  reviewTab,
  savedMessage,
  tagNeedsDecision,
} from "@/lib/review";
import { fromScreeningWarning, sortByReach } from "@/lib/repeated-names";
import { useDebouncedValue } from "@/lib/use-debounced-value";

import { ProposalCard, TagRow } from "./review-items";

const PAGE_SIZE = 25;

export function ReviewPanel({ round }: { round: RoundState }) {
  const [reviewer, setReviewer] = useState(round.reviewer ?? "");
  const [chosenTab, setChosenTab] = useState<ReviewTab | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const search = useDebouncedValue(searchInput.trim(), 300);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [bulkReason, setBulkReason] = useState("");
  const [lastSaved, setLastSaved] = useState("");
  const { data: queue, isPending, error } = useReviewQueue(round, {
    state: chosenTab === null || chosenTab === "proposals" ? "undecided" : chosenTab, q: search, offset, limit: PAGE_SIZE,
  });
  const save = useSaveDecisions(round.round_id);
  const locked = round.status === "running";
  const tab = reviewTab(chosenTab, queue);

  // Both set during render, as React recommends for state that follows from new data, not in an effect.
  // Deciding the tags on a later page empties it; move back to the last page that still has some.
  const lastOffset = queue ? clampOffset(offset, queue.total, PAGE_SIZE) : offset;
  if (lastOffset !== offset) setOffset(lastOffset);
  // The tab closes when its last tag is settled, so go back to following the work that's left.
  if (chosenTab === "held" && queue?.held === 0) setChosenTab(null);

  const named = reviewer.trim().length > 0;
  const canEdit = named && !locked && !save.isPending;

  function submit(decisions: DecisionInput[], clearTags: ReviewQueueTag["source"][] = [], clearProposals: string[] = []) {
    save.mutate(
      { reviewer: reviewer.trim(), decisions, clear_tags: clearTags, clear_proposals: clearProposals },
      {
        onSuccess: () => {
          setSelected(new Set());
          setLastSaved(savedMessage(decisions, clearTags.length + clearProposals.length));
        },
      },
    );
  }

  function changeTab(next: ReviewTab) {
    setChosenTab(next);
    setOffset(0);
    setSelected(new Set());
  }

  const tabs: { id: ReviewTab; label: string; count?: number }[] = queue ? [
    ...(queue.proposals.length > 0 ? [{ id: "proposals" as const, label: "Categories & specialities", count: queue.proposals_undecided }] : []),
    { id: "undecided", label: "To decide", count: queue.undecided },
    { id: "decided", label: "Decided", count: queue.decided },
    { id: "all", label: "All" },
    ...(queue.held > 0 ? [{ id: "held" as const, label: "Still held", count: queue.held }] : []),
  ] : [];
  const selectableTags = (queue?.tags ?? []).filter(tagNeedsDecision);
  const selectedTags = selectableTags.filter((tag) => selected.has(tag.source.tag_id));
  const showTags = tab !== "proposals";

  return (
    <section aria-label="Review" className="flex flex-col gap-4">
      <ProgressBar round={round} queue={queue} onShowHeld={() => changeTab("held")}>
        {selectedTags.length > 0 && (
          <div className="flex flex-wrap items-center gap-2 rounded-md bg-accent p-2" data-testid="bulk-bar">
            <span className="text-sm font-medium whitespace-nowrap">{selectedTags.length} selected</span>
            <Input
              aria-label="Reason for selected tags"
              className="min-w-40 flex-1 bg-background"
              placeholder="Note for these (optional)"
              value={bulkReason}
              onChange={(e) => setBulkReason(e.target.value)}
            />
            {(["create", "exclude", "defer"] as const).map((action) => (
              <Button
                key={action}
                size="sm"
                variant="outline"
                disabled={!canEdit}
                onClick={() => submit(bulkTagDecisions(selectedTags, action, bulkReason))}
              >
                {action === "create" ? "Add as new at best placement" : ACTION_LABELS[action]}
              </Button>
            ))}
          </div>
        )}
      </ProgressBar>

      <div className="flex flex-wrap items-start gap-x-8 gap-y-4">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="review-reviewer">Your name</Label>
          <Input
            id="review-reviewer"
            className="w-64"
            placeholder="Recorded with each decision"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
          />
          {!named && <p className="text-sm text-muted-foreground">Enter your name to start deciding.</p>}
        </div>
        {queue && queue.undecided > 0 && (
          <DraftDecisions round={round} reviewer={reviewer} disabled={!canEdit} />
        )}
      </div>

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

      {queue && (
        <div className="flex flex-col gap-3">
          {queue.repeated_names.length > 0 && (
            <Notice title="Repeated names">
              <RepeatedNames names={sortByReach(queue.repeated_names.map(fromScreeningWarning))} />
              <p className="mt-2">Automatically accepted tags are available under All for individual review.</p>
            </Notice>
          )}
          <div className="flex flex-wrap items-center gap-1 border-b border-rule pb-2" role="group" aria-label="Review lists">
            {tabs.map(({ id, label, count }) => (
              <Button key={id} size="sm" variant={tab === id ? "secondary" : "ghost"} aria-pressed={tab === id} onClick={() => changeTab(id)}>
                {label} <span className="ml-1 text-muted-foreground">{count}</span>
              </Button>
            ))}
            {showTags && (
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
            )}
          </div>
          <p aria-live="polite" className="min-h-5 text-sm text-muted-foreground">{lastSaved}</p>

          {tab === "proposals" && (
            <div className="flex flex-col gap-3">
              <p className="text-sm text-muted-foreground">
                Held groups need a decision before tags can go under them. Repeated-name warnings are advisory;
                accepted groups remain visible as context without blocking the snapshot.
              </p>
              {queue.proposals.map((proposal) => (
                <ProposalCard
                  key={proposal.proposal_id}
                  proposal={proposal}
                  repeatedNames={queue.repeated_names.filter((warning) => warning.proposal_ids.includes(proposal.proposal_id)).map((warning) => warning.name)}
                  disabled={!canEdit}
                  onDecide={(decision) => submit([decision])}
                  onClear={() => submit([], [], [proposal.proposal_id])}
                />
              ))}
            </div>
          )}

          {showTags && selectableTags.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <label className="flex shrink-0 items-center gap-2 text-sm whitespace-nowrap">
                <input
                  type="checkbox"
                  disabled={!canEdit}
                  checked={selectedTags.length === selectableTags.length}
                  onChange={(e) => setSelected(new Set(e.target.checked ? selectableTags.map((tag) => tag.source.tag_id) : []))}
                />
                Select pending tags on this page
              </label>
            </div>
          )}

          {showTags && queue.tags.length === 0 && <EmptyTab tab={tab} searching={search.length > 0} round={round} />}
          {showTags && (
            <div className="flex flex-col divide-y divide-rule">
              {queue.tags.map((tag) => (
                <TagRow
                  key={`${tag.source.source_dataset}-${tag.source.tag_id}`}
                  tag={tag}
                  held={tab === "held"}
                  selected={tagNeedsDecision(tag) && selected.has(tag.source.tag_id)}
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
            </div>
          )}
          {showTags && (queue.total > PAGE_SIZE || offset > 0) && (
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
    </section>
  );
}

function EmptyTab({ tab, searching, round }: { tab: ReviewTab; searching: boolean; round: RoundState }) {
  if (searching) return <Notice title="No tags match">Try another search or list.</Notice>;
  if (tab === "decided") return <Notice title="Nothing decided yet">Decisions you make appear here, and can be changed until the snapshot is generated.</Notice>;
  if (tab === "all") return <Notice title="No tags in this review queue">Advisory and reviewed tags appear here when available.</Notice>;
  return (
    <Notice title="Nothing left to decide here">
      {round.decisions_applied ? "Use the bar at the top to finish." : "Apply your decisions, using the bar at the top."}
    </Notice>
  );
}

const WORKFLOW = [
  { id: "decide", label: "Decide" },
  { id: "apply", label: "Apply" },
  { id: "generate", label: "Generate snapshot" },
] as const;

/** Stays on screen while scrolling a long list: how far along the review is and the one thing to do next. */
function ProgressBar({
  round, queue, onShowHeld, children,
}: { round: RoundState; queue: ReviewQueue | undefined; onShowHeld: () => void; children: React.ReactNode }) {
  const apply = useApplyDecisions(round.round_id);
  const integrate = useIntegrateRound(round.round_id);
  const next = reviewNextStep(round, queue);
  const progress = queue ? reviewProgress(queue) : null;
  const unsettled = queue ? queue.undecided + queue.proposals_undecided : 0;
  // Applying is on offer as soon as something's saved, but deciding is still the job while tags are left.
  const position = next === "apply" && unsettled > 0 ? "decide" : next;

  return (
    <div data-testid="review-bar" className="sticky top-0 z-20 flex flex-col gap-2 border-b border-rule bg-background py-3">
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
        <ol aria-label="Review steps" className="flex items-center gap-2 text-sm">
          {WORKFLOW.map(({ id, label }, index) => (
            <li key={id} aria-current={position === id ? "step" : undefined} className={position === id ? "font-medium" : "text-muted-foreground"}>
              {index > 0 && <span aria-hidden className="mr-2 text-muted-foreground">→</span>}
              {index + 1}. {label}
            </li>
          ))}
        </ol>
        {progress && <span className="text-sm text-muted-foreground">{progress.done.toLocaleString()} of {progress.total.toLocaleString()} tags settled</span>}
      </div>
      {progress && (
        <div
          role="progressbar"
          aria-label="Tags settled"
          aria-valuemin={0}
          aria-valuemax={progress.total}
          aria-valuenow={progress.done}
          className="h-1.5 overflow-hidden rounded-full bg-muted"
        >
          <div className="h-full rounded-full bg-primary transition-[width]" style={{ width: `${progress.percent}%` }} />
        </div>
      )}
      <div className="flex min-h-8 flex-wrap items-center justify-between gap-3">
        {next === "working" && <p className="text-sm text-muted-foreground">Working… this usually takes under a minute.</p>}
        {next === "apply" && (
          <>
            <p className="text-sm">
              {unsettled > 0
                ? `Your saved decisions aren't checked yet. Apply when you're ready; ${unsettled.toLocaleString()} still to decide.`
                : "All decided. Apply to check your decisions against the taxonomy."}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              {queue && queue.held > 0 && (
                <Button size="sm" variant="outline" onClick={onShowHeld}>See the {queue.held.toLocaleString()} still held</Button>
              )}
              <Button size="sm" variant={unsettled > 0 ? "outline" : "default"} disabled={apply.isPending} onClick={() => apply.mutate()}>Apply decisions</Button>
            </div>
          </>
        )}
        {next === "decide" && (
          <>
            <p className="text-sm">{round.pending_sources.toLocaleString()} tag(s) must be settled before the new taxonomy version can be generated.</p>
            {queue && queue.held > 0 && (
              <Button size="sm" variant="outline" onClick={onShowHeld}>See the {queue.held.toLocaleString()} still held</Button>
            )}
          </>
        )}
        {next === "generate" && (
          <>
            <p className="text-sm">Everything is settled. Generating adds these tags as a new taxonomy version.</p>
            <Button size="sm" disabled={integrate.isPending} onClick={() => integrate.mutate()}>Generate enriched snapshot</Button>
          </>
        )}
      </div>
      {children}
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
    <div className="flex min-w-64 flex-1 flex-col gap-1.5">
      <p className="text-sm font-medium">Save time on the clear-cut tags</p>
      <p className="text-sm text-muted-foreground">
        Adds tags nothing resembles and links near-identical ones, at their most confident placement. You can change any draft before applying.
      </p>
      <div>
        <Button
          size="sm"
          variant="outline"
          disabled={disabled || draft.isPending || !round.decisions_applied}
          onClick={() => draft.mutate({ reviewer: reviewer.trim(), create_below: Number(createBelow), reuse_above: Number(reuseAbove) })}
        >
          Draft clear-cut decisions
        </Button>
      </div>
      {!round.decisions_applied && <p className="text-sm text-muted-foreground">Apply your saved decisions first.</p>}
      {counts && (
        <p className="text-sm">
          Drafted {counts.create ?? 0} to add and {counts.reuse ?? 0} to link. The rest need your judgement.
        </p>
      )}
      {draft.error && <p className="text-sm text-destructive">{draft.error.message}</p>}
      <details className="text-sm">
        <summary className="cursor-pointer text-muted-foreground">Cut-offs (uncalibrated, so check the results)</summary>
        <div className="mt-2 flex flex-wrap items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="draft-create">Add as new below</Label>
            <Input id="draft-create" className="w-24" type="number" min="0" max="1" step="0.05" value={createBelow} onChange={(e) => setCreateBelow(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="draft-reuse">Link at or above</Label>
            <Input id="draft-reuse" className="w-24" type="number" min="0" max="1" step="0.05" value={reuseAbove} onChange={(e) => setReuseAbove(e.target.value)} />
          </div>
        </div>
      </details>
    </div>
  );
}
