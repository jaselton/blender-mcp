---
name: competing-hypotheses
description: Stress-test a claim before acting on it, using the CIA's Analysis of Competing Hypotheses (ACH). Use when the user is about to act on a number, a summary, a diagnosis, a root-cause story, or an answer from another AI — especially when they say "is this right", "can I trust this", "before I send this", "check this claim", or paste an answer and ask whether to act on it. Also use when a single explanation is being treated as settled and no alternative has been named.
---

# Analysis of Competing Hypotheses

Richards Heuer developed ACH at CIA in the 1970s to defeat one failure mode: an
analyst picks the explanation that feels right, then collects evidence for it.
Every piece that fits feels like confirmation, so confidence rises while accuracy
does not.

The fix is structural. You do not evaluate the leading explanation. You list all
the explanations at once and try to **knock each one down**. The survivor — the
one with the least evidence *against* it — is the answer, whether or not it was
the one you liked.

## Core rule: diagnosticity

Evidence that is consistent with *every* hypothesis has **zero diagnostic value**.
It cannot separate them, so it is not proof of any of them — no matter how
compelling it sounds.

This is the whole trick, and it is what most people skip. "The report is detailed
and internally consistent" fits both *the report is correct* and *the source
confidently fabricated it*. Say so explicitly when it happens; do not let it sit
in the evidence column doing invisible work.

A hypothesis is eliminated by **inconsistent** evidence, never confirmed by
consistent evidence.

## Procedure

1. **Restate the claim** with confident wording stripped out. "Churn is up 40%
   because of the pricing change" becomes "a 40% churn figure was reported, and
   one proposed cause is the pricing change." Two claims are now visible where
   one was hiding.
2. **List the hypotheses** — at least three besides the one on offer. One of them
   must always be **"the source is simply wrong"** (bad data, hallucination,
   miscount, stale figure, misread question). Include it even when it feels rude;
   it is the hypothesis most often omitted and most often true.
3. **List the evidence**, including arguments and absences. A thing that *should*
   be there and is not is evidence.
4. **Build the matrix**: evidence down the side, hypotheses across the top. Mark
   each cell Consistent (C), Inconsistent (I), or Not applicable (N/A).
5. **Strike the non-diagnostic rows** — any row that is C across the board. Name
   each one as you strike it: "this proves nothing."
6. **Rank by inconsistency**, not by support. Fewest I's wins. Work down, not up.
7. **Test sensitivity**: if the one or two rows carrying the ranking turned out to
   be wrong, would the answer flip? If yes, say so — the conclusion is resting on
   a single leg.
8. **Name the check** that would settle it, and give a verdict.

## Output format

Produce exactly these five, in order:

1. **Claim restated**, confident wording removed, separated into its component
   assertions.
2. **Evidence table** — each piece, which hypotheses it fits and which it
   contradicts. Mark non-diagnostic rows plainly: *fits all — proves nothing*.
   Mark anything you cannot source as **UNVERIFIED**.
3. **The surviving hypothesis** — the one with the least evidence against it,
   with the inconsistencies that sank the others. Say what would change the
   ranking.
4. **One check the user can run themselves** — concrete, cheap, and *diagnostic*:
   it must produce different results under different hypotheses. "Read it again"
   is not a check. "Pull the same figure straight from the source table for one
   known row" is.
5. **Verdict: act / wait / verify first.**
   - **act** — the survivor holds, its key evidence is sourced, and the downside
     of being wrong is cheap and reversible.
   - **verify first** — the check in (4) is cheap and would move the ranking.
     This is the default; most claims land here.
   - **wait** — no cheap check exists and being wrong is expensive or hard to
     undo. Say what you are waiting for.

## Rules

- Try to break the claim. Do not defend it. If you find yourself accumulating
  support for the leading hypothesis, you have stopped doing ACH.
- Never drop a hypothesis for being unflattering, awkward, or unlikely-sounding.
  Drop it only when specific evidence is inconsistent with it.
- Mark every unsourced assertion **UNVERIFIED** — including the ones you supplied
  yourself and the ones inside the claim being tested. An unverified figure cannot
  carry a verdict.
- If the evidence is all non-diagnostic, say the claim is unsupported rather than
  ranking on vibes. "No diagnostic evidence either way" is a legitimate result.
- Absence of evidence is evidence when the evidence should have been there.
- Report the relative standing of *all* hypotheses, not just the winner. A close
  second place is information the user needs.

## Worked example

**Claim:** "Our API p99 latency regressed 3× after the caching deploy on Tuesday."

**Hypotheses:** H1 the deploy caused it · H2 traffic mix changed (new heavy
client) · H3 the measurement changed (new metrics agent, different percentile
window) · H4 the source is simply wrong (dashboard reading the wrong service)

| Evidence | H1 deploy | H2 traffic | H3 measurement | H4 wrong |
|---|---|---|---|---|
| p99 steps up Tuesday | C | C | C | C — *fits all, proves nothing* |
| Deploy shipped Tuesday | C | N/A | N/A | N/A |
| p50 flat, only p99 moved | C | C | C | C — *proves nothing* |
| Step change is instantaneous, not ramped | C | I | C | C |
| Metrics agent also upgraded Tuesday (UNVERIFIED) | N/A | N/A | C | N/A |
| Downstream service saw no latency change | I | I | C | C |

H2 is out — an organic traffic shift ramps. H1 takes a hard hit from the
downstream service seeing nothing: a real 3× server-side regression should be
visible to callers. H3 and H4 both survive, and they are separated by one
unverified row.

**Check:** replay ten fixed requests against the pre-deploy and post-deploy
builds with the *same* measurement path, and separately point the old metrics
agent at the new build. Different answers under H3 vs H1.

**Verdict: verify first.** The ranking rests on one unverified row about the
metrics agent, and the check is twenty minutes. Rolling back the deploy now would
be acting on the hypothesis with the *most* evidence against it.

## Pasteable version

For use outside this repo, see `PROMPT.md` in this directory — the same method as
a single prompt to paste into any assistant along with the claim.
