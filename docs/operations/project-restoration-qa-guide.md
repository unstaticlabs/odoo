# Projects restoration: QA guide

## Prepare the local product target

Run this tour on canonical `odoo_dev` only after complete reconstruction,
validation and finalization. It is the single disposable developer/QA product
database. The environment owner must provide:

- a local-only URL;
- a restored project manager account;
- a restricted internal reviewer account;
- confirmation that the canonical product services are healthy.

Use `make login-link USER=<login>` for a local one-time login URL; never commit
credentials. Run through the normal product add-ons path with both live
electronic-invoicing guards set to zero. Create a separate database only for an
explicit, automatically cleaned proof that cannot safely use `odoo_dev`.

The data came from a private production backup. Keep this site on the local
machine, do not expose its port publicly, and do not send invitations or
messages to real external addresses.

## What changed

The branch restores Projects and Tasks into standard Community Odoo instead of
building a separate project-management application. Most screens therefore
look like ordinary Odoo Projects.

The visible additions are:

- a **Planned Start** field beside the existing deadline;
- a warning when a task is planned to start before an unfinished dependency;
- restored task history, followers, activities and attachments;
- restored aliases, milestones, recurrences, subtasks and dependencies.

The Enterprise Gantt screen is not included. Its meaningful date range is
available through Planned Start and Deadline in native task views.

Import runs, source identifiers and reconstruction reports are deliberately
absent from the product interface. They are migration evidence, not ongoing
project-management features.

## Suggested test tour

### 1. Confirm the project overview

1. Log in with the restored project manager account.
2. Open **Projects**.
3. Confirm the normal view immediately shows active projects.
4. Clear any personal filters if the count looks different.
5. Expect 17 active projects.
6. Add the **Archived** filter and expect one archived project.

Check that project names, managers, privacy, stages and update indicators look
recognisable. Open both an active project and the archived project.

### 2. Browse active and historical tasks

1. Open **Projects > Tasks**.
2. Switch between list and kanban views.
3. Confirm tasks use their restored stages and status badges.
4. Add the **Archived** filter and open an archived task.
5. Remove it again before continuing.

The restored perimeter contains 2,052 tasks: 2,000 active and 52 archived. It
also contains one task template. Finalization suppresses Odoo welcome tasks and
future recurrence conveniences while reconstructing the frozen source, so the
unfiltered product total must remain exactly 2,052. Any target-only task is a
blocking migration error, not an expected convenience.

Status totals across active and archived tasks are:

| Status | Expected |
| --- | ---: |
| In progress | 1,492 |
| Changes requested | 27 |
| Approved | 101 |
| Waiting | 80 |
| Cancelled | 22 |
| Done | 330 |

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

The copy contains 1,247 parent/subtask links and 258 dependency links.

### 5. Check milestones, recurrences and updates

Browse projects with milestones and recurring tasks.

- Open a milestone and check its deadline and reached state.
- Open a recurring task and check its repeat interval and end rule.
- Open the project updates/history area and read an older update.

There are 3 milestones, 16 recurrence configurations and 16 project updates.

### 6. Read chatter and open evidence

On several projects and tasks:

1. Scroll through older chatter.
2. Open replies in a conversation to check their order.
3. Check tracked field changes.
4. Open a scheduled activity.
5. Open or download an attachment.

The restoration contains 22,273 messages, 8,807 tracked changes, 894
activities, 2,368 followers and 45 attachments. The attachments total
19,320,931 bytes and are checksum-verified during restoration.

### 7. Check connected business records

Open a project with an analytic account or profitability information.

- Follow the analytic-account link.
- Check that linked expenses remain visible through the normal accounting or
  expense navigation available to your user.
- Return to the project using the breadcrumb or browser back button.

Two projects have analytic accounts and those accounts connect to 170 restored
expenses. The source contains no project sales links. It contains one
project-linked Documents record; the Documents archive must retain its stable
document identity and link it to both the restored task and project.

### 8. Check privacy and company access

First, while logged in with the restored project manager account, confirm
private/follower-only projects open normally.

Then:

1. Log out.
2. Log in with the designated restricted internal reviewer account.
3. Open **Projects**.
4. Confirm follower-only private projects are absent.
5. Expect 11 projects to be visible to this restricted reviewer.
6. Try navigating back to a private project using browser history; access
   should still be refused or the record should remain hidden.

Log back in with the restored project manager account for the remaining
checks.

The source has 2 employee-visible, 5 follower-only, 1 invited-user and 10
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

### 10. Confirm the clean product boundary

As the restored project manager, confirm that ordinary Projects navigation
contains no **Restoration Runs**, import reports, source IDs or reconstruction
fields.

From the repository, run:

```bash
PROJECT_TARGET_DATABASE="$QA_DATABASE" \
  scripts/project-restore product-validate
```

It must report that `usl_project_restore` is uninstalled, with zero migration
models, model metadata, fields, field metadata, project views and XML IDs. The
operational `usl_project` module and Planned Start field must remain installed.

## Expected differences from Odoo Online

These are deliberate and should not be reported as defects:

- no Enterprise Gantt client;
- no empty Enterprise Documents folder shells;
- no replay of historical outgoing-email or notification queues;
- no project sales links, project Documents or external collaborators, because
  the source contained none;
- unsupported Enterprise-only property keys are excluded from Community Odoo
  and reported only in the external migration evidence.

## Managing the disposable server

Check that it is running:

```bash
docker ps --filter name="$QA_CONTAINER"
```

Read recent logs:

```bash
docker logs --tail 100 "$QA_CONTAINER"
```

Stop it when finished:

```bash
docker stop "$QA_CONTAINER"
```

After evidence is recorded and review is complete, remove the branch-specific
container and disposable database. Do not remove shared Docker networks,
volumes, source services or canonical databases.

```bash
docker rm "$QA_CONTAINER"
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
