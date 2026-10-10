---
name: community-ux-foundations
description: 介面上「有什麼、依什麼順序、講什麼話，以及沒東西／出錯／還在載入時看到什麼」——資訊架構、流程與接縫、三態、介面文案、可用性啟發式稽核、研究綜整。當要決定一個畫面放哪些東西、怎麼排、或補空態／錯誤態／載入態時使用。畫面長什麼樣見 `community-frontend-design`；色票與字型的既有值域見 `community-ui-design-system`。
---

# UX Foundations

What is on the screen, in what order, saying what — and what a person sees when
there is nothing yet, when it broke, and while it is still coming.

## Where this sits

| The question | The skill |
|---|---|
| What goes on this screen, in what order, saying what | **this one** |
| What does it look like | `community-frontend-design` |
| Which palette, type pairing, component state | `community-ui-design-system` |
| Does the built thing violate a rule | `community-web-guidelines`, `community-a11y-audit` |

The boundary is real, not bureaucratic: a screen can be beautiful, accessible,
and perfectly on-system while showing the wrong things in the wrong order to
someone who cannot tell what to do next. **None of the other four notice that.**

## The three states

Any surface fed by data has three states beyond the happy one. Ship a surface
with only the happy path and you have shipped a demo.

- **Empty** — no data *yet*. Distinguish **first-run empty** (never had data; the
  screen's job is to teach and offer the one action that creates the first item)
  from **filtered empty** (data exists, this query found none; the screen's job is
  to say which filter to loosen). Collapsing the two is the most common miss —
  first-run copy shown to someone who just typed a bad search reads as broken.
- **Error** — it failed. Name **what** failed, whether it is **retryable**, and the
  **one action** available. "Something went wrong" tells the person nothing they
  can act on and hides the retryable/terminal distinction they need.
- **Loading** — still coming. Below ~1s show nothing (a flashed spinner is worse
  than a beat of stillness). Beyond that, prefer a skeleton that matches the shape
  of what arrives, so the layout does not jump. Past ~10s the wait needs progress
  or a way out.

**Completion criterion:** every data-fed surface in scope has all three specified,
with first-run and filtered empty distinguished wherever both can occur. Listing
the surfaces and marking one "N/A" is an answer; silence is not.

Permission-denied and offline are the two that hide behind "error" and usually
deserve their own copy — denied is not retryable, offline is.

## Information architecture

IA is the order and grouping decision, made before anything is styled.

1. **List what this screen must answer.** One line per question a user arrives with.
2. **Rank by arrival frequency, not by data model.** The order of fields in a table
   is not the order of importance on a screen.
3. **Group by the user's mental model.** The test: name each group in the user's
   words. A group that can only be named with an internal term (`counterparty`,
   `settlement batch`) is a database grouping wearing a UI costume.
4. **Cut to one primary action per screen.** Two co-equal primary actions is a
   decision passed to the user that the design should have made.

**Completion criterion:** every question from step 1 is answerable on the screen,
and every element on the screen traces to one of those questions. Elements that
trace to nothing are the deletion list.

## Flows and seams

A flow is what someone does across screens. **Seams** — the joins between screens,
between systems, and between people — are where flows break, because each side was
designed as if the other were reliable.

Walk the flow and write down, at each seam:

- What carries over (and what silently does not — a filter, a draft, a scroll position).
- What happens on **back**, on **refresh**, and on a **shared link** into the middle.
- Who is waiting, and what they see while they wait — a seam that hands off to a
  human or a queue has a state on both sides.
- What is irreversible past this point, and whether that is said before or after.

**Completion criterion:** each seam has an answer for carry-over, back, refresh,
deep link, and reversibility. "Not applicable" needs the reason.

## Interface copy

Copy is interface, not decoration — it is often the whole of what a state
communicates.

- **Label the action, not the abstraction.** `Send quote` beats `Submit`.
- **Say what happens next**, especially when it involves someone else or a delay.
- **Errors name the fix**, not the failure: what to change, not what went wrong.
- **Numbers and time need units and a reference point.** "3 days" from when.
- **One term per concept, everywhere.** Two words for one thing teaches users that
  they are two things. Where the project keeps a glossary, that is the source of
  truth and copy follows it.

## Heuristic audit

For reviewing an existing interface, walk Nielsen's ten and produce a finding only
where you can name the screen and the concrete failure. A heuristic with no
evidence attached is an opinion.

1. Visibility of system status
2. Match between system and the real world
3. User control and freedom (undo, exit, back)
4. Consistency and standards
5. Error prevention
6. Recognition rather than recall
7. Flexibility and efficiency of use
8. Aesthetic and minimalist design
9. Help users recognise, diagnose, and recover from errors
10. Help and documentation

**Completion criterion:** all ten walked and stated — each either carries a finding
with a screen name, or is explicitly marked clean. A report listing four findings
and nothing else does not say whether the other six were checked.

## Research synthesis

When handed raw material — interviews, tickets, session notes, competitor teardowns:

- **Separate observation from interpretation** in the write-up. "Seven of nine
  users scrolled past the filter bar" is an observation; "the filter bar is
  invisible" is an interpretation, and only the first survives someone disagreeing
  with you.
- **Count, and give the denominator.** "Most users" hides whether it was 3 of 4 or
  30 of 50.
- **Keep the disconfirming cases.** The two people who did find the filter bar are
  the most informative rows in the table.
- **End on decisions, not themes.** A theme changes nothing; a decision names what
  the interface will do differently and what would reverse it.

## Output

Write findings as a table — surface / state or seam / what is missing / proposed
copy or order — so the result is actionable line by line rather than a narrative
someone has to re-derive the work items from.

Where the project keeps its own journey, flow, or scenario canon, that canon is the
source of truth for names and IDs: cite them rather than inventing a parallel
vocabulary, and raise the conflict instead of resolving it silently.
