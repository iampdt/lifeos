# Starter template

A Notion layout that LifeOS maps completely on its own. Anyone who duplicates it while
connecting Notion only has to review the setup screen and save. Any other layout works too;
the setup screen then asks for what it can't guess.

To offer it, build these databases on one public page in a Notion workspace and set that page's
URL as the connection's template ("Notion URL for optional template", see
[deployment.md](deployment.md) §9).

Only **Tasks** is required. The suggestions match names case-insensitively and by type, so the
property names below can carry emoji or extra words ("📅 Due") and still match.

## Tasks

| Property | Type | Notes |
| --- | --- | --- |
| Task | Title | |
| Status | Select | Inbox, Next, Doing, Waiting, Done, Cancelled |
| Due | Date | |
| Priority | Select | P0, P1, P2, P3 (highest first) |
| Area | Select | Any areas |
| Completed | Date | Set when a task is marked done |
| Recurring | Checkbox | Recurring tasks don't count toward the daily limit |
| Goal | Relation | To Goals |
| Week | Relation | To Weekly Progress |
| Month | Relation | To Monthly Progress |

## Habits

One row per day. Every checkbox is a habit and every number a metric; add or remove any.

| Property | Type | Notes |
| --- | --- | --- |
| Name | Title | Anything; LifeOS writes the date here for new rows |
| Date | Date | The day the row is for |
| Exercise, Read, Deep work | Checkbox | Example habits |
| Sleep (hours) | Number | Example metric; "hours" is picked up as its unit |
| Notes | Text | The check-in appends what you learned |

## Weekly Progress and Monthly Progress

Two databases with the same properties; tasks link to the page for their due date.

| Property | Type | Notes |
| --- | --- | --- |
| Week (or Month) | Title | |
| Start | Date | |
| End | Date | |
| Status | Select | Upcoming, Current, Closed |
| Theme | Text | |
| Review | Text | The weekly review writes here |
| Tasks | Relation | To Tasks |

## Goals

| Property | Type | Notes |
| --- | --- | --- |
| Goal | Title | |
| Status | Select | Planned, Active, Done, Dropped |
| Type | Select | Any horizons, e.g. Year, Quarter |
| Current | Number | |
| Target | Number | |
| Deadline | Date | |

## Reading list

| Property | Type | Notes |
| --- | --- | --- |
| Book | Title | |
| Author | Text | |
| Status | Select | Want to Read, Reading, Finished, Abandoned |
| Pages | Number | |
| Pages Read | Number | The check-in updates it |
| Started | Date | |
| Finished | Date | |
