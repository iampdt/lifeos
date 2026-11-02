from evals.scoring import score_checkin, score_plan, summarise


def parsed(**values):
    base = {"done": [], "partial": [], "habits": {}, "sleep": None, "screen_time": None,
            "pages": {}, "learned": None, "new_tasks": []}  # fmt: skip
    return {**base, **values}


CASE = {
    "id": "c1",
    "optional_habits": ["reading"],
    "expect": {"done": ["A"], "habits": {"dsa": True}, "sleep": 7, "new_tasks": 1,
               "new_task_keywords": ["passport"]},
}  # fmt: skip


def test_exact_parse_passes_and_optional_habits_are_allowed():
    result = score_checkin(CASE, parsed(done=["A"], habits={"dsa": True, "reading": True},
                                        sleep=7.1, new_tasks=["Renew passport"]))  # fmt: skip
    assert result.passed and result.done_tp == 1 and result.habits_right == 1


def test_errors_are_reported_and_counted():
    result = score_checkin(CASE, parsed(done=["A", "B"], habits={"dsa": False, "workout": True},
                                        screen_time=3, new_tasks=[]))  # fmt: skip
    assert not result.passed
    assert (result.done_tp, result.done_fp, result.done_fn) == (1, 1, 0)
    assert result.unexpected_fields == 2  # workout, screen_time
    assert any("passport" in e for e in result.errors)
    assert any("sleep: expected 7" in e for e in result.errors)


def test_learned_checked_only_when_stated():
    case = {"id": "c2", "expect": {}}
    assert score_checkin(case, parsed(learned="something")).passed
    case = {"id": "c3", "expect": {"learned": True}}
    assert not score_checkin(case, parsed()).passed


def test_plan_scoring():
    case = {"id": "p", "slots": 1, "must_pick": ["A"], "must_not_pick": ["Z"], "adversarial": True}
    assert score_plan(case, ["A"], valid=True).passed
    assert not score_plan(case, ["Z"], valid=True).passed
    assert not score_plan(case, ["A", "B"], valid=True).passed
    assert not score_plan(case, [], valid=False).passed


def test_summary():
    good = score_checkin(CASE, parsed(done=["A"], habits={"dsa": True}, sleep=7,
                                      new_tasks=["passport"]))  # fmt: skip
    bad = score_plan({"id": "p", "slots": 1, "adversarial": True}, ["A", "B"], valid=True)
    usage = [{"latency_ms": 100, "input_tokens": 10, "output_tokens": 4},
             {"latency_ms": 300, "input_tokens": 30, "output_tokens": 6}]  # fmt: skip
    s = summarise([good, bad], usage)
    assert (s.cases, s.answered, s.passed, s.errors) == (2, 2, 1, 0)
    assert (s.adversarial_total, s.adversarial_passed) == (1, 0)
    assert s.latency_p50_ms == 200 and s.input_tokens == 20 and s.done_precision == 1.0


def test_report_renders_both_suites():
    from evals.run import render

    s = summarise([score_plan({"id": "p", "slots": 1}, ["A"], valid=True)], [])
    text = render({"m": {"planning": (s, [])}}, ["m"], "2026-11-02")
    assert "## Day planning" in text and "| `m` | 1/1 (100%) | 0/0 |" in text
    assert "Passed (of answered)" in text
    assert "## Check-in parsing" not in text


def test_unanswered_cases_are_errors_not_failures():
    from evals.scoring import CaseScore

    answered = score_plan({"id": "a", "slots": 1, "adversarial": True}, ["A"], valid=True)
    missing = CaseScore("b", False, ["no model answer"], adversarial=True, no_answer=True)
    s = summarise([answered, missing], [])
    assert (s.cases, s.answered, s.passed, s.errors) == (2, 1, 1, 1)
    assert s.pass_rate == 1.0 and (s.adversarial_passed, s.adversarial_total) == (1, 1)
