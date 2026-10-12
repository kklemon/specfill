---
name: specfill
description: Fill the gaps in a project specification prompt before a coding agent builds from it. Finds what is underspecified in a seed prompt, brief, spec, or PRD, interviews the user about it in rounds of multiple-choice questions, then rewrites the prompt with the answers woven in while preserving its original wording and structure. Use when the user wants to refine, complete, or de-ambiguate a project prompt or spec, or asks to be interviewed about the gaps in one.
license: MIT
metadata:
  homepage: https://github.com/kklemon/specfill
---

# specfill

Fill the gaps in a project "seed prompt": a brief the user will hand to an AI
coding agent. Find the aspects that are underspecified enough that the coding
agent would have to guess, interview the user about them, then return the
prompt with the answers woven in. The deliverable is the refined prompt itself,
in the user's own words. Do not plan or implement the project.

Work through the steps in order.

## 1. Collect the inputs

- **Seed prompt.** If the user names a file, read it. If they pasted the
  prompt, use that text. If you have neither, ask them to paste the prompt or
  name a file, and wait.
- **Custom instructions.** Treat any other request in the user's message, such
  as "also correct spelling mistakes" or "challenge me on UI/UX decisions", as
  custom instructions. Follow them during the interview and the rewrite; when
  they conflict with the rules below, the custom instructions win.

## 2. Research

Make sure your questions and options are accurate and current before asking
anything:

- If a web search tool is available, research before questioning: look up
  unfamiliar tools, libraries, services, or claims named in the prompt, and
  search again during the interview whenever an answer introduces something
  you are unsure about.
- If the prompt concerns a codebase in your workspace, read the relevant files.
- Never ask the user something you can resolve yourself with a quick search or
  by reading the code.

Keep the research to yourself; at most, mention in one line what you looked
into.

## 3. Interview

Ask in rounds. A round is a batch of questions; after each round, reassess what
is still open.

What to ask:

- Only ask questions whose answers materially change what gets built. Never ask
  about anything the prompt already answers or explicitly defers.
- Prefer concrete, opinionated options over open-ended asks: 2-6 options, each
  with a short label and a one-line description of its implications.
- Use single choice for mutually exclusive options and multiple choice when
  several options can apply simultaneously.
- Ask 3-6 questions per round, most important first, each with a 1-3 word
  topic header such as "Auth" or "Data model".
- Write everything the user sees — questions, option labels, descriptions, and
  summaries — in the same language as the seed prompt.

How to ask:

- If you have a structured question tool, use it: `AskUserQuestion` in Claude
  Code, `request_user_input` in Codex, `ask_user` in Gemini CLI or GitHub
  Copilot CLI, `question` in OpenCode. Check the tool's limits before calling
  it — `AskUserQuestion` takes at most 4 questions with 2-4 options each,
  `request_user_input` at most 3 questions with 2-3 options — and keep headers
  to 12 characters. When the tool takes fewer questions per call than the
  round has, spread the round over consecutive calls; when it takes fewer
  options than you would offer, keep the strongest ones. These tools offer a
  free-text "Other" answer themselves, so don't add one.
- Without such a tool, or when the tool is unavailable in the current mode,
  send the round as a single message, with numbered questions and options
  lettered a, b, c, in this format, then end your turn to wait for the reply:

  ```
  **1. Auth** — How should users sign in? (pick one)
  a. GitHub OAuth — no passwords to store; developers only
  b. Email + password — works for everyone; needs a reset flow

  **2. Platforms** — Which platforms must it support? (pick any)
  a. Web — ...
  ```

  Accept compact answers such as `1b, 2a+c, 3: free text, 4 skip`.
- Before the first round, tell the user once that they can answer in free
  text, say `skip` to leave a question open, or say `finish` to stop the
  interview and get the refined prompt.

Handling answers:

- Treat a skipped question as "leave unspecified / implementer's discretion"
  and never re-ask it. A question counts as skipped when the user says so,
  leaves it unanswered, or defers to you ("your call", "don't care").
- When the user says `finish`, stop asking and go straight to the rewrite with
  the answers so far.
- Keep a transcript for the rewrite: each question with its header, the
  selected options and any free text, or that it was skipped.

When to stop:

- Be exhaustive: keep asking follow-up rounds as long as material ambiguity
  remains, including ambiguity newly created by the user's answers.
- Stop once the prompt is fully specified from an implementer's perspective —
  when a competent coding agent could execute it without having to guess on
  anything consequential — or after 12 rounds.

## 4. Rewrite

Revise the original prompt to incorporate the transcript. These rules govern
the revised document:

- Preserve the original verbatim wherever possible: same headings, same section
  order, same list structure, same tone, same phrasing, same formatting quirks,
  even the same typos. You are a careful line editor, not an author.
- Write in the same language as the original prompt.
- Every change must encode information from the transcript, nothing else. Weave
  each answer into the most natural existing spot: extend a sentence, add a
  clause, add a bullet to an existing list. Add a new subsection only when no
  existing location fits, matching the surrounding heading style.
- When an answer conflicts with a statement in the original prompt, the latest
  answer wins: minimally edit that statement so it reflects the answer.
- If a question was skipped or the interview ended early, leave the aspect it
  covered exactly as ambiguous as the original left it — never invent an
  answer or resolve an open decision yourself.
- Do not summarize, restructure, reorder, "improve", or fix grammar/style.
- Never mention the interview, the questions, or that this is a revision.
- Output ONLY the revised prompt as markdown — no preamble, no closing remarks,
  no code fence around the document.

If no question was answered, there is nothing to weave in: tell the user so
and leave the prompt unchanged.

## 5. Deliver

- If the prompt came from a file, apply the revision to that file as targeted
  edits, so the user can review exactly what changed. If the user named a
  different destination, write the complete revised prompt there instead.
- Otherwise, reply with the revised prompt and nothing else, so the reply can
  be copied as is.

Then stop. Don't start implementing the prompt unless the user asks you to.
