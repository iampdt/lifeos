# Evaluation

How well do the model-backed agents do their jobs, and which model should run them? The
evaluation set answers that with numbers instead of impressions, and catches regressions when a
prompt or model changes. Latest results: [results.md](results.md).

## What is measured

| Suite | Agent | A case passes when |
| --- | --- | --- |
| Check-in parsing (17 cases) | `agents/checkin.py` | Finished and partly done tasks, habits, sleep, screen time, pages read and new to-dos all match, and nothing the message doesn't support is reported |
| Day planning (7 cases) | `agents/planner.py` | The plan is valid (known tasks, no duplicates, within the free slots), includes every task it must, and none it mustn't |

Each suite also reports the numbers behind the pass rate: precision and recall of finished
tasks, habit accuracy, values the message didn't support, failed calls, per-call latency and
token use.

## Case design

Cases live in `backend/evals/cases/` as YAML and are synthetic: no task, message or number comes
from a real workspace. They cover the ways a parse or a plan goes wrong in practice:

- **Intent vs. fact:** "I'll do the mock interview tomorrow" is not done; "halfway through" is
  partial; "couldn't finish" is not done.
- **Habits:** done, explicitly skipped, and about yesterday rather than today.
- **Numbers:** hours, "2 hours 30 minutes", pages read.
- **Matching:** paraphrased titles, near-identical titles (chapter 5 vs. chapter 6), and a
  Hinglish message.
- **Planning judgement:** priority, deadlines that break a tie, active goals, fewer good
  candidates than free slots, trimming an overloaded day.
- **Prompt injection (adversarial):** instructions planted in task titles, a book title and a
  goal, trying to mark everything done, invent reading or schedule every task. These pass only
  if the injected instruction has no effect.

Habits the message reasonably implies (reading a book chapter counts as reading) are listed per
case as optional: reporting them is allowed, inventing others is an error.

## How it runs

```bash
cd backend
.venv/bin/python -m evals.run                                  # every model in the configured chains
.venv/bin/python -m evals.run --models gemini-3.5-flash-lite --suites checkin
```

Each model is called on its own, without the fallback chain, through the same agent code the
workflows use, including validation, reference checks and title clean-up. A call that never
returns (rate limit, overload) is retried once and then counted as an error, not as a wrong
answer. A plan that the model returned but validation rejected counts as a failure.

The scorer (`evals/scoring.py`) is unit-tested and runs in CI; the evaluation itself calls a
real model and runs on demand.
