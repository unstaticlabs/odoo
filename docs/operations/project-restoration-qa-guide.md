# Projects restoration: QA guide

## Prepare a disposable QA site

Run this tour only on a disposable database that has completed Projects
restoration, validation and finalization. The environment owner must provide:

- a local-only URL;
- a restored project manager account;
- a restricted internal reviewer account;
- the exact disposable database and container names.

Share temporary passwords through an appropriate local or secret channel.
Never commit them to the repository or reuse production credentials.
Set `QA_DATABASE` and `QA_CONTAINER` in the review shell to the exact
branch-specific resource names before using the commands below.

The QA copy must be separate from clean restoration proofs, canonical
development databases and preserved source databases. Run it through the
normal product add-ons path with cron disabled and both live electronic
invoicing guards set to zero. Bind its HTTP port to localhost only.

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
