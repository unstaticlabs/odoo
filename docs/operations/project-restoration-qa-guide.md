# Projects restoration: local QA guide

## Open the QA site

The local QA environment is already running at:

<http://127.0.0.1:8079/web/login?db=odoo_projects_qa_20260729&type=password>

Use the main restored account for normal testing:

```text
Login: valentin
Password: projects-qa
```

Use the restricted reviewer account for the permissions check:

```text
Login: prosper
Password: projects-reviewer
```

This is a disposable copy named `odoo_projects_qa_20260729`. It is separate
from the clean restoration proofs and from the normal shared Odoo database.
Cron is disabled, as are live electronic invoicing and e-reporting.

The data came from a private production backup. Keep this site on the local
machine, do not expose port 8079 publicly, and do not send invitations or
messages to real external addresses.

## What changed

The branch restores Projects and Tasks into standard Community Odoo instead of
building a separate project-management application. Most screens therefore
look like ordinary Odoo Projects.

The visible additions are:

- a **Planned Start** field beside the existing deadline;
- a warning when a task is planned to start before an unfinished dependency;
- restored task history, followers, activities and attachments;
- restored aliases, milestones, recurrences, subtasks and dependencies;
- **Projects > Configuration > Restoration Runs** for managers to inspect the
  import result and its reported issues.

The Enterprise Gantt screen is not included. Its meaningful date range is
available through Planned Start and Deadline in native task views.

## Suggested test tour

### 1. Confirm the project overview

1. Log in as `valentin`.
2. Open **Projects**.
3. Confirm the normal view immediately shows active projects.
4. Clear any personal filters if the count looks different.
5. Expect 16 active projects.
6. Add the **Archived** filter and expect one archived project.

Check that project names, managers, privacy, stages and update indicators look
recognisable. Open both an active project and the archived project.

### 2. Browse active and historical tasks

1. Open **Projects > Tasks**.
2. Switch between list and kanban views.
3. Confirm tasks use their restored stages and status badges.
4. Add the **Archived** filter and open an archived task.
5. Remove it again before continuing.

The restored perimeter contains 1,793 tasks: 1,742 active and 51 archived. It
also contains one task template. The QA database has 10 pre-existing native
tasks outside the restored perimeter, so an unfiltered all-task count can show
1,803 without indicating a duplicate import.

Status totals across active and archived tasks are:

| Status | Expected |
| --- | ---: |
| In progress | 1,282 |
| Changes requested | 30 |
| Approved | 102 |
| Waiting | 85 |
| Cancelled | 18 |
| Done | 276 |

Stages and statuses are different concepts. A task can remain in its familiar
project stage while its status says waiting, blocked, done or cancelled.

### 3. Check assignments, dates and planning

Open several tasks from different projects and check:

- project, company and customer;
- owner and assignees;
- priority;
- deadline;
- **Planned Start**, where present;
- milestone and recurrence settings.

Five tasks have a restored Planned Start. On a task with dependencies, compare
the Planned Start with the blockers' deadlines. If an unfinished blocker ends
after the planned start, the form should show an amber dependency warning.

### 4. Check subtasks and dependencies

Open a task that shows subtasks or blockers.

- Follow the parent/subtask links in both directions.
- Open a blocking task from the dependency list.
- Confirm waiting work still looks waiting and was not silently completed.
- Confirm completed and cancelled blockers retain their original status.

The copy contains 1,221 parent/subtask links and 227 dependency links.

### 5. Check milestones, recurrences and updates

Browse projects with milestones and recurring tasks.

- Open a milestone and check its deadline and reached state.
- Open a recurring task and check its repeat interval and end rule.
- Open the project updates/history area and read an older update.

There are 3 milestones, 14 recurrence configurations and 16 project updates.

### 6. Read chatter and open evidence

On several projects and tasks:

1. Scroll through older chatter.
2. Open replies in a conversation to check their order.
3. Check tracked field changes.
4. Open a scheduled activity.
5. Open or download an attachment.

The restoration contains 18,458 messages, 7,506 tracked changes, 658
activities, 2,051 followers and 38 attachments. The attachments total
15,433,661 bytes and were checksum-verified during restoration.

### 7. Check connected business records

Open a project with an analytic account or profitability information.

- Follow the analytic-account link.
- Check that linked expenses remain visible through the normal accounting or
  expense navigation available to your user.
- Return to the project using the breadcrumb or browser back button.

Two projects have analytic accounts and those accounts connect to 116 restored
expenses. The source contained no project sales links and no project-linked
Documents records, so their absence is expected.

### 8. Check privacy and company access

First, while logged in as `valentin`, confirm private/follower-only projects
open normally.

Then:

1. Log out.
2. Log in as `prosper` with `projects-reviewer`.
3. Open **Projects**.
4. Confirm follower-only private projects are absent.
5. Expect 11 projects to be visible to this restricted reviewer.
6. Try navigating back to a private project using browser history; access
   should still be refused or the record should remain hidden.

Log back in as `valentin` for the remaining checks.

The source has 2 employee-visible, 5 follower-only, 1 invited-user and 9
portal-visible projects. It has no external project collaborators, so this QA
tour does not ask you to create or invite a synthetic collaborator.

### 9. Make a safe workflow edit

This QA database is disposable, so make one recognisable change:

1. Open an active task.
2. Change its priority or status.
3. Add a short note beginning with `QA:`.
4. Save and refresh the page.
5. Confirm the edit remains and the task still opens normally.

Do not run the restoration importer from the user interface. Automated tests
already verify that repeating the same source snapshot does not overwrite
valid post-cutover task edits.

### 10. Inspect the restoration report

As `valentin`, open:

**Projects > Configuration > Restoration Runs**

Open the latest run and confirm:

- status is **Passed**;
- 17 projects and 1,793 tasks are reported;
- source and target material counts match;
- there is no unresolved error or warning;
- the one informational issue explains that 638 source activities had no
  assigned user and used the documented fallback assignment rule.

## Expected differences from Odoo Online

These are deliberate and should not be reported as defects:

- no Enterprise Gantt client;
- no empty Enterprise Documents folder shells;
- no replay of historical outgoing-email or notification queues;
- no project sales links, project Documents or external collaborators, because
  the source contained none;
- Enterprise-only property metadata is retained for audit but unsupported keys
  are not injected into Community Odoo.

## Managing the local server

Check that it is running:

```bash
docker ps --filter name=usl-projects-qa-20260729
```

Read recent logs:

```bash
docker logs --tail 100 usl-projects-qa-20260729
```

Stop it when finished:

```bash
docker stop usl-projects-qa-20260729
```

Start the same QA environment again:

```bash
docker start usl-projects-qa-20260729
```

## Reporting a problem

For each problem, record:

- the project and task;
- the login used;
- the URL;
- what you expected;
- what happened instead;
- whether the record was active or archived;
- a screenshot, if the problem is visual;
- whether refreshing the page changes the result.

Do not paste private chatter or attachments into a public issue.
