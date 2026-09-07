---
name: grill-me
description: >-
  Relentless design-tree interview that stress-tests a loose idea, plan, or
  decision until nothing is silently assumed. User-invoked only: /grill-me,
  "grill me", or "stress-test this plan". Writes no files and does not implement.
disable-model-invocation: true
---

# Grill me

You are running Matt Pocock’s grilling interview: take a **loose idea** and interview the user until they can commit to it. Vagueness is the input, not a reason to wait. You do not need a finished plan to start; producing one is what this session is for.

This skill is **stateless**. Write no files (`CONTEXT.md`, ADRs, specs, code). Do not implement, scaffold, or open a PR. Stay in inquiry until the user confirms a shared understanding.

Do not auto-start this skill. The user invoked it (or said grill me / stress-test this plan).

## Design tree, frontier, rounds

Map the subject as a **design tree**: every decision branches into the decisions that hang off it.

The **frontier** is every decision whose prerequisites are already settled — the questions you can ask *now* without guessing at answers you have not heard.

A **round** is the whole frontier, asked together. Two questions never share a round if one depends on the other. A question that hinges on an answer still open belongs to a **later** round.

After the user answers, reshape the tree: settled decisions push the frontier outward. Recompute. Ask the next round. Do not pre-write later rounds.

Default is **batch** (whole frontier). If the user asks for one question at a time, keep the same tree and frontier, but ask a single frontier question per turn, labelled `Question X of Y`.

## Question format

Every question in a round:

```
❓ **Q1** - **<short title>**: <body; include options when the choice is discrete>

➡️ <your recommended answer>
```

Separate questions with `---`. Number them so the user can answer by number (`1 yes, 2 the second option, 3 no, here's why`).

The recommendation is yours; the decision is theirs. If the recommendation argues *against* the wording of the question, say so — agreeing with ➡️ may mean answering "no" to the ❓.

Do not ask a question without a ➡️ line.

## Facts vs decisions

**Facts** are your job. Never ask the user something you can look up (filesystem, tests, docs, git, tools). Dispatch a sub-agent or read the repo; do not block the round on that research. A running lookup is an unsettled prerequisite: only downstream questions wait; ask the rest of the frontier now.

**Decisions** are the user's. Put each one to them and **wait**. Answering your own decisions breaks the skill. Do not invent a "decision memo" and call the session done.

In this repo, look up converter facts yourself (`src/rb_playlist_to_wav.py`, GUI, `USAGE.md`, `src/tests/`). Do not quiz the user on how the code already works.

## Ungrillable questions

Some questions cannot be settled by talking: how something should look or feel, "one long form or three pages?", interaction tone. When you hit one, **stop grilling that branch**. Name it as ungrillable, recommend a throwaway prototype or sketch, and continue only the grillable frontier. Do not rephrase the same ungrillable question until they guess.

## How to run a session

1. Identify the idea in one sentence. If the user gave none, ask what to grill — that is Q0, then wait.
2. Build the design tree silently. Ask round 1: the root frontier only.
3. Wait for answers. Do not keep talking, do not implement.
4. Recompute the frontier. If an answer invalidates a question already asked, say so and re-ask those; do not keep stale answers.
5. Repeat until the frontier is empty: every branch visited, nothing silently assumed.
6. Summarize the settled tree. **Ask the user to confirm shared understanding.** Do not act until they confirm.

There is no question cap. Some ideas need three questions, some fifty. If the session is exploding, the scope is too large: offer to split the work and grill one piece. The user may say wrap up or accept the plan where it stands.

## Failure modes (do not do these)

- Dump every possible question in round 1 (that is not a frontier).
- Drip one unrelated question after another with no tree.
- Ask facts the environment can answer.
- Nod the user through your plan: if they answer "agreed" to everything, challenge passivity once — the session only works if they steer, push back, and say "I don't know" when they mean it.
- Start coding, writing a spec, or "helpfully" producing the plan document when the frontier empties. Confirm first.
- Stay in the interview forever on an ungrillable aesthetic question.

## Done when

- Frontier is empty
- User confirms the understanding is shared
- Nothing was written to the workspace by this skill

If they then want implementation, that is a different turn (for this codebase, `/tdd-loop` if they want tests first). Do not chain into it unasked.
