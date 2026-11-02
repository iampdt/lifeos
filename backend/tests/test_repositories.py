from datetime import date

from app.notion.repositories import HabitRepository, PeriodRepository, TaskRepository
from tests.factories import page, relation, rich, specs, task_page


class FakeClient:
    """Stands in for NotionClient: returns canned pages and records queries."""

    def __init__(self, pages=(), relation_ids=None):
        self.pages = list(pages)
        self.relation_ids = relation_ids or {}
        self.queries = []

    async def query_data_source(self, data_source_id, *, filter=None, sorts=None, page_size=100):
        self.queries.append({"data_source_id": data_source_id, "filter": filter, "sorts": sorts})
        for item in self.pages:
            yield item

    async def get_relation_ids(self, page_id, property_id):
        return self.relation_ids[(page_id, property_id)]

    async def get_page(self, page_id):
        return next(p for p in self.pages if p["id"] == page_id)


async def test_task_page_becomes_domain_model():
    repo = TaskRepository(FakeClient([task_page()]), specs()["tasks"])
    [task] = await repo.active()
    assert task.title == "Write tests"
    assert task.status == "Next" and task.priority == "P1" and task.area == "Career Switch"
    assert task.due == date(2026, 11, 3)
    assert task.goal_ids == ("goal-1",)
    assert task.week_key == "2026-W45" and task.month_key == "2026-11"
    assert repo.client.queries[0]["data_source_id"] == "ds-tasks"


async def test_timestamp_due_dates_are_read_in_india_time():
    repo = TaskRepository(FakeClient([task_page(due="2026-11-01T20:00:00.000Z")]), specs()["tasks"])
    [task] = await repo.all()
    assert task.due == date(2026, 11, 2)


async def test_truncated_relations_are_fetched_in_full():
    client = FakeClient(
        [task_page(goals=("g1",), goal_has_more=True)],
        relation_ids={("task-1", "go"): ["g1", "g2", "g3"]},
    )
    [task] = await TaskRepository(client, specs()["tasks"]).all()
    assert task.goal_ids == ("g1", "g2", "g3")


async def test_blank_task_decodes_without_errors():
    blank = task_page(
        title="", status=None, priority=None, due=None, goals=(), week_key=None, month_key=""
    )
    [task] = await TaskRepository(FakeClient([blank]), specs()["tasks"]).all()
    assert task.title == "" and task.status is None and task.due is None
    assert task.week_key is None and task.month_key is None


async def test_non_text_formula_values_are_ignored():
    broken = task_page()
    broken["properties"]["Month Key"]["formula"] = {"type": "boolean", "boolean": False}
    [task] = await TaskRepository(FakeClient([broken]), specs()["tasks"]).all()
    assert task.month_key is None and task.week_key == "2026-W45"


async def test_trashed_pages_are_ignored():
    trashed = {**task_page("gone"), "in_trash": True}
    tasks = await TaskRepository(FakeClient([trashed, task_page("kept")]), specs()["tasks"]).all()
    assert [t.id for t in tasks] == ["kept"]


def habit_page(page_id: str, title: str, reading: bool = False) -> dict:
    return page(
        page_id,
        {
            "Date": {"id": "title", "type": "title", "title": rich(title)},
            "Reading": {"id": "rd", "type": "checkbox", "checkbox": reading},
            "DSA": {"id": "ds", "type": "checkbox", "checkbox": False},
            "Sleep": {"id": "sl", "type": "number", "number": 7.5},
            "Notes": {"id": "no", "type": "rich_text", "rich_text": rich("ok")},
        },
    )


async def test_habit_rows_are_found_by_date_whatever_the_title_format():
    rows = [
        habit_page("h1", "1/11/26", reading=True),
        habit_page("h2", "28/10/26"),
        habit_page("h3", "x"),
    ]
    repo = HabitRepository(FakeClient(rows), specs()["habits"])
    [day] = await repo.for_day(date(2026, 11, 1))
    assert day.id == "h1" and day.checks["reading"] is True and day.metrics["sleep"] == 7.5
    assert set(day.checks) == set(specs()["habits"].habit_fields)  # every checkbox is a habit
    assert [r.day for r in await repo.all()][-1] is None  # unparseable title is kept, flagged


async def test_period_pages_decode_dates_and_tasks():
    week = page(
        "w1",
        {
            "Week": {"id": "title", "type": "title", "title": rich("26 Oct – 1 Nov")},
            "Start": {"id": "s", "type": "date", "date": {"start": "2026-10-26"}},
            "End": {"id": "e", "type": "date", "date": {"start": "2026-11-01"}},
            "Status": {"id": "st", "type": "select", "select": {"name": "Current"}},
            "Theme": {"id": "th", "type": "rich_text", "rich_text": []},
            "Review": {"id": "rv", "type": "rich_text", "rich_text": []},
            "Tasks": {"id": "tk", **relation("t1", "t2")},
        },
    )
    repo = PeriodRepository(FakeClient([week]), specs()["weeks"])
    [current] = await repo.starting_on(date(2026, 10, 26))
    assert (current.start, current.end, current.status) == (
        date(2026, 10, 26),
        date(2026, 11, 1),
        "Current",
    )
    assert current.task_ids == ("t1", "t2")
    assert repo.client.queries[0]["filter"] == {
        "property": "Start",
        "date": {"equals": "2026-10-26"},
    }
