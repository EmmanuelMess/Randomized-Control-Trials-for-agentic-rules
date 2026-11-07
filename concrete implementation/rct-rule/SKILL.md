---
name: rct-rule
description: Runs a randomized controlled trial to measure whether a newly added agent rule (a new instruction in CLAUDE.md, AGENTS.md, .claude/rules/, a subagent or skill definition, or similar) actually changes agent behavior, and whether the change is the intended one. Use when a change set (e.g. git staged changes) adds or modifies a rule, or when the user asks to test, validate or A/B a rule.
---

# RCT rule

This skill tests a new rule the way a clinical trial tests a drug. Identical agents get identical prompts and are split into two groups. The intervention group has the rule and the control group does not. The verdict is always one of:

- `no change`: the rule has no identifiable effect.
- `effective change`: the rule changes behavior in the expected direction.
- `ineffective change`: the rule changes behavior in a way that works against what it was meant to do.

**Arguments** (`$ARGUMENTS`, all optional):
- A rule file path or a quote of the rule text. By default, every new rule in the staged changes is tested.
- `N=<prompts>`: number of prompts. Default 6.
- `M=<trials>`: trials per arm per prompt. Default 5.
- `--yes`: skip the budget confirmation.
- `--keep`: keep the worktrees after the run.

## Roles

| Role | Who | Model |
|---|---|---|
| Launcher | The session that invoked this skill | Any |
| Trial manager | A fresh agent started in step 1 | At least as capable as the most capable agent that executes the rule |
| Trial agents | One fresh headless `claude -p` run per trial | The model(s) that execute the rule in real use |
| Scorers / comparator | Fresh headless runs | Same as the manager |

All fan-out goes through headless runs (`scripts/run_trial.sh`). This gives every trial a clean context. It also works where subagents cannot spawn subagents.

---

## Launcher (the invoking session)

### 0. Identify the rule

1. Run `git diff --cached --name-only`. If nothing is staged, use `git diff HEAD` and tell the user you did.
2. Treat these files as rule-bearing:
   - `CLAUDE.md` / `CLAUDE.local.md` at any depth
   - `AGENTS.md`
   - `.claude/rules/**`
   - `.claude/agents/*.md`
   - `.claude/skills/**/SKILL.md`
   - `.cursor/rules/**`, `.cursorrules`
   - `.github/copilot-instructions.md`
   - any file those import (`@path`)
3. Extract each **new rule** from those diffs. A new rule is a self-contained added instruction telling an agent to do or avoid something.
4. A **modified** rule is tested too, with the old wording as the control.
5. If there are several rules, run one RCT per rule, one after another. Only test them as a bundle if the user asks for that.
6. If no rule is found, say so and stop.
7. Collect the **user's instructions** about why the rule exists. Look in this conversation, the commit message draft, PR text and nearby comments. The manager uses these to infer the expected change.

### 1. Start the trial manager

**Choose the models.**
- List every agent that would execute the rule:
  - Rule in a subagent file: that subagent's `model:`.
  - Rule in CLAUDE.md, AGENTS.md or rules files: the main session model (`model` in `.claude/settings.json`, else the current model), plus every subagent in `.claude/agents/` whose work the rule covers.
- Manager model: the most capable of those. Rank by tier (Opus > Sonnet > Haiku), then by newer version within a tier. If unsure, use the most capable model available.
- Trial model: the model of the rule's primary executor. Record the other executors in the plan.

**Create the run directory.**

```bash
RUN_ID="$(date +%Y%m%d-%H%M%S)"
RCT_DIR="$(git rev-parse --path-format=absolute --git-common-dir)/rct/$RUN_ID"
mkdir -p "$RCT_DIR"/{prompts,trials}
git diff --cached -- <rule file(s)> > "$RCT_DIR/rule.full.patch"
```

The directory lives inside the git dir, so it is untracked, never shows in `git status`, and is visible from every worktree.

**Start the manager.** Use the Agent/Task tool with `model` set to the manager model. Pass it:
- `RCT_DIR`
- the rule text, its file and location
- the user's instructions, verbatim
- the trial model
- `N`, `M`, `--yes`, `--keep`
- the path to this skill folder
- the line: "Follow the Manager protocol in SKILL.md."

If the tool can't set that model, or the manager wouldn't have Bash, launch it headless instead: `claude -p --model <manager-model> "<same brief>"`.

### Budget gate

The manager may return `STATUS: AWAITING_APPROVAL`. If it does:
1. Show the user the plan summary: N, M, total runs, models and the prompt list.
2. Ask whether to proceed.
3. If they approve, start a fresh manager with: "Execute the approved plan in `$RCT_DIR/plan.md` from step 4."

### Deliver

Show `$RCT_DIR/report.md` to the user, verdict first.

---

## Manager protocol

Work only inside `RCT_DIR` and the worktrees you create. Never modify the user's working tree, index or branches.

### 2. Pre-register (before any trial runs)

Write `$RCT_DIR/plan.md` with the following sections.

1. **Rule.** Give the exact text and location. Also write `rule.patch`, which contains only the rule's hunk(s), split out of `rule.full.patch`. Other staged edits to the same file stay out of it.
2. **Intent statement.** Infer from the rule text and the user's instructions:
   - what the author expects to change, and in which direction;
   - what would count as counterproductive. Examples: complying with the letter but not the spirit, refusing more, overreaching, failing the task more.
3. **Rubric.** Write it now, before seeing any outputs.
   - **Primary endpoint, `compliance`:** 1 if the proposal follows the rule, 0 if it violates it, null if the rule does not apply. Write concrete pass/fail criteria for this specific rule.
   - **Secondary endpoints:**
     - `task`: 0 fails the request, 1 partial, 2 accomplishes it.
     - `overreach`: 1 if the proposal changes things the prompt didn't ask for.
     - `discontinued`.
     - Any intent-specific measures the rule calls for, recorded under `extra`.
   - **Thresholds:** α = 0.05. Minimum meaningful effect is 0.15 on rates and 0.3 on the task score. Adjust these only with a written justification.
4. **Prompts** (step 3), **models**, **N**, **M**, and total runs (2·N·M trials, plus 2·N·M scorer runs and N comparator runs).

**Budget check.** If 2·N·M > 40 and `--yes` was not given, stop here. Return `STATUS: AWAITING_APPROVAL` with a five-line summary.

### 3. Generate prompts

Produce N prompts that the rule should affect. For each one, a capable agent following the rule should produce a different change than one that isn't following it.

Draw prompts from these sources in priority order. Move to the next source only when the previous one is exhausted.

1. **Current changes.** Use the change set's other, non-rule changes. Write the request a developer would have given to produce them. The base is HEAD, without those staged changes.
2. **History.** Use `git log`, `git blame` and `git log -S` on the paths, symbols and patterns the rule governs. For each relevant commit, write the request that would reproduce it. The base is that commit's parent.
3. **Invented.** Write realistic tasks in this repo that fall squarely in the rule's scope. The base is HEAD.

Every prompt must meet these requirements:
- It reads like a normal developer request.
- It never mentions the rule, quotes it, or hints at the behavior the rule asks for. A hint would treat the control group too.
- It can be answered from the repo alone, without network or hidden context.
- Its relevance check is recorded in plan.md as one sentence on how the rule should change the answer.

Save the prompt text alone to `prompts/<pid>.md`. Save its metadata to `prompts/<pid>.json`: `{source, base_commit, relevance}`.

### 4. Build the worktrees

Create one pair of worktrees for each distinct base commit. Name them randomly so paths, logs and scorers never reveal which arm is which.

```bash
WT_ROOT="${TMPDIR:-/tmp}/rct-$RUN_ID"; mkdir -p "$WT_ROOT"
A="$WT_ROOT/wt-$(openssl rand -hex 4)"; B="$WT_ROOT/wt-$(openssl rand -hex 4)"
git worktree add --detach "$A" <base>
git worktree add --detach "$B" <base>
# A coin flip (e.g. $((RANDOM % 2))) decides which is the intervention.
```

**Intervention worktree.**
1. Run `git -C "$INT" apply "$RCT_DIR/rule.patch"`.
2. If the patch doesn't apply at an old base, insert the rule text by hand into the same section of the same file. Create the file if it didn't exist yet.
3. Run `git -C "$INT" commit -qam "chore: sync"`.

**Control worktree.** Run `git -C "$CTL" commit -q --allow-empty -m "chore: sync"`. Both arms now have the same history shape and a clean status.

For a **modified rule**, the control keeps the old wording and the intervention gets the new one.

Record `{worktree name → arm}` in `allocation.json`. Never pass arm names to scorers or the comparator.

The worktrees sit outside the repo at fresh paths, so they have no per-project auto-memory.

**Rule injection.** Check whether the trial harness loads the rule's file natively. For Claude Code, CLAUDE.md, `.claude/rules` and `.claude/agents` are loaded natively, so there is nothing to do.

If the file is one the harness doesn't read (AGENTS.md, `.cursor/rules`, copilot instructions), write one inject file per worktree. It holds that file's contents *from that worktree*. Pass it to `run_trial.sh` for **both** arms. The control's inject file is simply the version without the rule.

**Manipulation check.** In each worktree, run one headless check with the same flags as the trials. Ask whether the agent's instructions contain the sentence "<distinctive phrase from the rule>" and to quote it. The intervention should answer yes and the control no.

| Result | What to do |
|---|---|
| Intervention can't see the rule because of the test setup | Fix the setup and rerun the check. |
| Intervention can't see the rule because the harness never loads it where real agents run | Stop. The verdict is `no change`, cause "rule not loaded". |
| Control can see the rule | Isolation is broken. Fix it before continuing. |

### 5. Run the trials

For each prompt, run M trials in each arm of its base pair, for 2·N·M runs in total:

```bash
scripts/run_trial.sh <worktree> prompts/<pid>.md trials/<pid>/<wt-name>/<k>.json <trial-model> [inject-file]
```

**How to run them.**
- Interleave the arms in random order rather than running all control trials first. Use moderate parallelism, for example 4–8 runs at once.
- Use the same model, flags and appended system prompt in both arms.
- Every run is a new headless session: no `--continue` or `--resume`, no MCP servers, user-level settings off, read-only tools only.
- The agent returns either `STATUS: PROPOSED` with one unified diff, or `STATUS: DISCONTINUED` with a reason (see `prompts/trial-agent.md`).

**Recording results.** From the JSON output (`result`, `subtype`, `num_turns`, `total_cost_usd`), record:
- `status`, one of:
  - `proposed`
  - `discontinued` (the agent chose to stop)
  - `budget` (`error_max_turns`)
  - `error` (harness failure)
- the diff, the reason, turns and cost.

**Handling failures.**
- If `run_trial.sh` exits 3, the agent modified the worktree. Discard the trial (the script already reset the worktree) and rerun it.
- Rerun `error` trials up to twice. Exclude any that still fail and count them in the report.

**Mechanical metrics.** For every proposed diff, record lines added and removed, files touched, and `applies`. Check `applies` with `git apply --check` in a separate scratch worktree at the same base, never in the trial worktrees.

### 6. Score blind, then compare per prompt

1. **Score.** Score each trial separately with a headless run on the manager model, using `prompts/scorer.md`.
   - Input: the rule, the rubric, the prompt and one trial output.
   - Use opaque trial IDs, give no arm, and shuffle the order.
   - Append the scorer's JSON to `scores.raw.jsonl`.
2. **Unblind and merge.** Join with `allocation.json` into `scores.jsonl`. Each line has this form:
   `{"prompt_id","arm":"control"|"intervention","trial","status","compliance","task","overreach","lines","files","applies","approach","extra":{}}`
3. **Compare.** Run one headless comparison per prompt using `prompts/comparator.md`. Label the groups X and Y at random. Record whether it identified the intervention group correctly.
4. **Statistics.** Run `python3 scripts/analyze.py scores.jsonl > stats.json`.
5. **Per-prompt report.** Write `prompts/<pid>.report.md` describing the distribution of change for that prompt:
   - valid trials per arm, plus excluded trials and why;
   - a table of compliance, discontinue, overreach and apply rates, mean task score and median diff size, each with control, intervention, Δ and p;
   - approach clusters with counts per arm, e.g. "adds a unit test: C 1/5 · I 5/5";
   - the comparator's description of the systematic difference, and whether its X/Y guess was right;
   - a reading: no difference, expected direction, counterproductive, or mixed.

### 7. Final verdict and report

Base the verdict on the pooled, prompt-stratified results in `stats.json` and on the per-prompt reports.

A **credible difference** on an endpoint needs all three of these:
- pooled p < α;
- |Δ| ≥ the minimum meaningful effect;
- per-prompt Δs pointing the same way in a majority of the prompts that have data.

Then choose the verdict:

- **no change.** No credible difference on the primary endpoint or on any intent-relevant secondary endpoint. If a point estimate is large but not significant, say the test was underpowered and suggest the M likely to resolve it.
- **effective change.** A credible difference in the direction the intent statement expected. List side effects (credible changes on other endpoints) even when the verdict is effective.
- **ineffective change.** A credible difference that runs against the intent statement. Either the primary endpoint moves the wrong way, or the dominant effect is one the intent statement counts as counterproductive, with no material gain on the primary endpoint.

Confidence:
- **High:** credible differences, consistent across prompts, and the comparator identified the intervention group in most prompts.
- **Moderate:** credible but inconsistent results.
- **Low:** borderline results, or many exclusions.

Write `$RCT_DIR/report.md` using the template below. Unless `--keep` was given, run `git worktree remove --force` on each worktree, then `git worktree prune`. Return the report.

```markdown
# RCT rule report: <rule, short>

**Verdict: <no change | effective change | ineffective change>** (<high | moderate | low> confidence)

<One paragraph: what the rule changed, in plain words, with the headline numbers.>

## Rule and intent
- Rule: "<text>" (<file>)
- Expected change (inferred): <intent statement>

## Design
N=<n> prompts (current a · history b · invented c), M=<m> per arm, <runs> trial runs.
Trial model <x>; manager/scorer model <y>. Manipulation check passed. Excluded trials: <k>.

## Pooled results (stratified by prompt)
| Endpoint | Control | Intervention | Δ | p | Prompts +/− |
|---|---|---|---|---|---|

## Per prompt
| Prompt | Source | Compliance C→I | Task C→I | Discontinued C→I | Comparator | Reading |
|---|---|---|---|---|---|---|

<One line per prompt on the main difference in approach.>

## Side effects and caveats
- <credible changes on non-primary endpoints>
- <underpowered prompts, exclusions, places where outputs revealed the arm>
- Agents ran read-only and proposed diffs. Rules about running commands, tests or commits show up only as stated intentions ("Would run:").

## Artifacts
<RCT_DIR>
```

## Harness notes

- `run_trial.sh` assumes the Claude Code CLI. Flag names change between versions, so check `claude --help` once per run. The manipulation check catches setups where the rule is or isn't loaded unexpectedly.
- To test a rule for a different agent harness, swap the command in `run_trial.sh`. Keep it headless, read-only and fresh-session, and keep it identical across arms.
