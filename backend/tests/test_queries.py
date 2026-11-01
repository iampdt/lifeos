from datetime import date

from app.notion.mapping import PropertySpec
from app.notion.queries import TaskQueries
from tests.factories import specs

TODAY = date(2026, 11, 1)
OPEN = [
    {"property": "Status", "select": {"does_not_equal": "Done"}},
    {"property": "Status", "select": {"does_not_equal": "Cancelled"}},
]


def views() -> TaskQueries:
    return TaskQueries(specs()["tasks"])


def test_active_excludes_done_and_cancelled():
    q = views().active()
    assert q.filter == {"and": OPEN}
    assert q.sorts == [{"property": "Due", "direction": "ascending"}]


def test_today_includes_everything_due_up_to_today():
    q = views().today(TODAY)
    assert q.filter == {"and": [*OPEN, {"property": "Due", "date": {"on_or_before": "2026-11-01"}}]}
    assert [s["property"] for s in q.sorts] == ["Priority", "Due"]


def test_overdue_is_strictly_before_today():
    assert views().overdue(TODAY).filter["and"][-1] == {
        "property": "Due",
        "date": {"before": "2026-11-01"},
    }


def test_next_7_days_window():
    conditions = views().next_7_days(TODAY).filter["and"]
    assert conditions[-2:] == [
        {"property": "Due", "date": {"on_or_after": "2026-11-01"}},
        {"property": "Due", "date": {"on_or_before": "2026-11-08"}},
    ]


def test_inbox_and_done_history():
    v = views()
    assert v.inbox().filter == {"property": "Status", "select": {"equals": "Inbox"}}
    done = v.done_due_between(date(2026, 10, 26), date(2026, 11, 1)).filter["and"]
    assert done[0] == {"property": "Status", "select": {"equals": "Done"}}


def test_status_type_properties_use_the_status_filter_key():
    spec = specs()["tasks"]
    spec.properties["status"] = PropertySpec(
        name="Status",
        type="status",
        values={"done": "Done", "cancelled": "Cancelled", "inbox": "Inbox", "next": "Next"},
    )
    assert TaskQueries(spec).active().filter["and"][0] == {
        "property": "Status",
        "status": {"does_not_equal": "Done"},
    }
