# gitlab-automation

Mirror JSON documents into GitLab: epics, user stories, issues, milestones and labels.

The project turns a folder of plain JSON files into GitLab objects. Every object is
described declaratively, every command is idempotent, and every run can be
previewed with `--dry-run` before anything is written.

---

## Contents

- [Why](#why)
- [Requirements](#requirements)
- [Setup](#setup)
- [Project layout](#project-layout)
- [Quick start](#quick-start)
- [Commands](#commands)
- [JSON documents](#json-documents)
- [Payload reference](#payload-reference)
- [Idempotency](#idempotency)
- [Dry run](#dry-run)
- [Exit codes](#exit-codes)
- [Tests](#tests)
- [Troubleshooting](#troubleshooting)
- [Security](#security)

---

## Why

- **One source of truth.** The JSON files describe *what* should exist; the CLI
  figures out *how* to get there.
- **Portable.** The same documents drive GitLab.com, a self-managed instance or a
  test project. Only `GITLAB_URL` and the project aliases change.
- **Safe.** Nothing is written until you drop `--dry-run`, and re-running never
  duplicates objects.

## Requirements

- Python 3.11 or newer (developed against 3.13).
- `requests` and `python-dotenv`.

```bash
pip install -r requirements.txt
```

The CLI runs straight from a clone, no install required:

```bash
python main.py --help
```

Installing it is optional and adds a `gitlab-automation` command:

```bash
pip install -e .
gitlab-automation --help
```

## Setup

### 1. Create a `.env` file

```bash
cp .env.example .env
```

`.env` is git-ignored and is the only place credentials live. Fill it in:

```dotenv
GITLAB_URL=https://gitlab.com
GITLAB_TOKEN=glpat-xxxxxxxxxxxx
GITLAB_GROUP_PATH=your-group/your-subgroup
GITLAB_PROJECTS=repo=your-group/subgroup/repo,api=your-group/subgroup/api
```

`GITLAB_PROJECTS` is an **alias to full path** map, separated by commas. Aliases keep
the JSON short and portable; the full path is what the GitLab API needs.

Comments are optional: `.env.example` documents every key, but `.env` may be just the
assignments you actually need.

### 2. Verify the environment

```bash
python main.py doctor
```

`doctor` checks the token, the group and every configured project, and prints what it
found. It never writes anything.

### 3. Validate the JSON documents

```bash
python main.py validate
```

`validate` runs completely offline and needs no token. It reports missing fields,
duplicate titles, malformed dates, unknown project aliases and bad label colours.

## Project layout

```text
gitlab-automation/            # project root
├── main.py                  # thin entry point: adds src/ to sys.path and runs the CLI
├── pyproject.toml           # packaging; the src layout is declared here
├── requirements.txt
├── .env.example             # documented template for .env
├── .env                     # your real token and paths (git-ignored)
├── data/                    # the JSON documents that get mirrored
│   ├── epics.json
│   ├── user-stories.json
│   ├── milestones.json
│   ├── labels.json
│   └── examples/            # reference payloads, not loaded by default
│       ├── issues.example.json
│       ├── user-stories.minimal.json
│       ├── milestones.example.json
│       ├── labels.example.json
│       ├── epic-updates.example.json
│       └── issue-updates.example.json
├── src/
│   └── automation/   # the package
│       ├── cli.py           # argparse wiring and command dispatch
│       ├── config.py        # .env loading and defaults
│       ├── client.py        # GitLab REST client
│       ├── schema.py        # JSON loading, validation and merging
│       ├── markdown.py      # description rendering
│       ├── issue_builder.py # preflight checks and API payloads
│       ├── runner.py        # shared orchestration helpers
│       ├── logger.py        # coloured logging
│       ├── doctor.py        # environment check command
│       └── commands/        # one module per subcommand
└── tests/                   # offline unit tests
```

Only `data/*.json` is read. Anything in `data/examples/` is a template you can copy.

The examples tell one story: they are the work items for building this very toolkit,
so you can read them top to bottom and immediately understand every field.

## Quick start

After `pip install -e .` the `gitlab-automation` script runs the whole workflow:

```bash
gitlab-automation doctor                          # 1. verify credentials and projects
gitlab-automation validate                        # 2. check the JSON offline
gitlab-automation labels create                   # 3. create the labels the payloads use
gitlab-automation milestones create               # 4. create the schedule windows
gitlab-automation epics create --skip-unsupported # 5. create the epics, tolerating Free groups
gitlab-automation stories create --dry-run        # 6. preview the user stories
gitlab-automation stories create                  # 7. create them for real
```

From a clone without installing, `python main.py` takes exactly the same options:

```bash
python main.py doctor
python main.py stories create --no-assign-epics
```

The order matters: milestones must exist before the issues that reference them, and
epics too when the group supports them. Labels are referenced by name only, so they
can be created in any order, but creating them first avoids warnings.

On a Free/Starter group, step 5 tolerates the epic licence limit thanks to
`--skip-unsupported`; drop the flag to make the command fail instead. `stories create`
still creates the items without epics and warns once. See
[Groups without epics](#groups-without-epics).

Options you will reach for most often (full list under [Command options](#command-options)):

| Option | Command | Effect |
| --- | --- | --- |
| `--skip-unsupported` | `epics create\|list\|update` | Treat the Free/Starter epic `403` as a warning and exit `0`. |
| `--no-assign-epics` | `stories create`, `issues create` | Do not call the premium epic endpoint; create the items without an epic. |
| `--create-missing-labels` | `stories create`, `issues create` | Create the referenced labels that do not exist yet. |
| `--create-missing-milestones` | `stories create`, `issues create` | Create the referenced milestones, deriving their window from the items. |
| `--file PATH` | creation and update commands | Read the document from an explicit path. |

Every command accepts `--help`, e.g. `python main.py stories create --help` or
`gitlab-automation epics create --help`.

## Commands

Every command accepts `--help`.

| Command | Purpose |
| --- | --- |
| `doctor` | Check token, group and every project. |
| `validate` | Validate the JSON documents offline. |
| `epics create \| list \| update` | Group-level epics. |
| `stories create \| list` | User stories: issues enriched with acceptance criteria. |
| `issues create \| list \| update` | Plain issues. |
| `milestones create \| list` | Group or project milestones. |
| `labels create \| list` | Project or group labels. |

`stories` is also available as `user-stories`.

### Global options

| Option | Effect |
| --- | --- |
| `--dry-run` | Report every action without writing. |
| `--data-dir PATH` | Use another folder instead of `./data`. |
| `--log-level LEVEL` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `--no-color` | Disable ANSI colours. |
| `--version` | Print the version. |

Global options may go **before or after** the subcommand, so both of these work:

```bash
python main.py --dry-run stories create
python main.py stories create --dry-run
```

### Command options

Creation and update commands add their own flags on top of the global ones:

| Option | Command | Effect |
| --- | --- | --- |
| `--skip-unsupported` | `epics create \| list \| update` | On a Free/Starter group, warn and exit `0` instead of failing on the licence `403`. |
| `--no-assign-epics` | `stories create`, `issues create` | Skip the epic lookup, so the Premium-only endpoint is never called. |
| `--create-missing-labels` | `stories create`, `issues create` | Create the labels a payload references but that do not exist yet. |
| `--create-missing-milestones` | `stories create`, `issues create` | Create the referenced milestones, deriving their window from the items. |
| `--file PATH` | creation and update commands | Read the document from an explicit path instead of the default file. |

### Filters

Creation and update commands accept `--project`, `--title`, `--limit` and `--dry-run`
to process part of a payload:

```bash
python main.py stories create --project repo
python main.py stories create --limit 3
python main.py issues update --issue "Replace milestone titles with numeric IDs"
```

`--title` matches items that *create* something; `--issue` and `--epic` match items
that *reference* an existing object.

### Auto-creating dependencies

```bash
python main.py stories create --create-missing-labels --create-missing-milestones
```

`--create-missing-labels` creates any label a payload references but that does not
exist yet. `--create-missing-milestones` does the same for milestones, deriving each
milestone window from the `start_date` and `due_date` of the stories that reference
it. Both are opt-in: without them, an unknown label is an error and an unknown
milestone is a warning.

### Groups without epics

Epics are a **Premium/Ultimate** feature. On Free and Starter the endpoint exists but
refuses the request with `403`, which is a licence limit rather than a permissions
problem. The CLI makes this explicit instead of hiding it behind a raw `403`:

- `stories create` / `issues create` **degrade gracefully**. If a payload declares an
  `epic` and the group cannot host epics, the item is still created and a warning is
  printed. Nothing is lost: the epic field is simply ignored.
- `--no-assign-epics` skips the epic lookup entirely, so the gated endpoint is never
  called. Use it to silence the warning on a Free group:

  ```bash
  python main.py stories create --no-assign-epics
  ```

- `epics create` / `list` / `update` **fail by default**, because creating epics is
  the whole point of those commands. When a group cannot host them, pass
  `--skip-unsupported` to turn the limitation into a warning and exit `0`:

  ```bash
  python main.py epics create --skip-unsupported
  ```

  That keeps a scripted `epics create && stories create` pipeline green instead of
  letting the first step abort the run.

`epics` commands never reach the network in `--dry-run`, so a Free group can still
preview an epic payload offline.

### Closing work

Updates are separate documents so that a repeatable "create" run never reopens
finished work. No update document ships in `data/`, because the examples in
`data/examples/` are not auto-discovered: pass one explicitly, or copy it to
`data/issue-updates.json` and drop the `--file` flag.

```bash
python main.py epics update --file data/examples/epic-updates.example.json
python main.py issues update --file data/examples/issue-updates.example.json
```

## JSON documents

A document has an optional `type`, an optional `defaults` block and exactly one
collection list:

```json
{
  "type": "issues",
  "defaults": { "labels": ["status::backlog"] },
  "issues": [
    { "project": "repo", "title": "Fix a bug", "description": "Details." }
  ]
}
```

The collection key is inferred from the filename, and `type` is cross-checked against
it. Both `issues` and `tickets` are accepted, `user_stories` and `stories` likewise.

| File | `type` | Collection key |
| --- | --- | --- |
| `epics.json` | `epics` | `epics` |
| `user-stories.json` | `user_stories` | `user_stories` (or `stories`) |
| `issues.json` | `issues` | `issues` (or `tickets`) |
| `milestones.json` | `milestones` | `milestones` |
| `labels.json` | `labels` | `labels` |
| `epic-updates.json` | `epic_updates` | `epic_updates` |
| `issue-updates.json` | `issue_updates` | `issue_updates` |

A file whose name does not match any of these is ignored. An empty collection
(`"issues": []`) is valid and means "nothing to do"; a missing collection key is an
error.

### `defaults`

`defaults` supplies values for items that do not declare them. An item always wins.

```json
{
  "defaults": {
    "labels": ["status::backlog", "type::user-story"],
    "milestone": "Automation Phase 1 - Toolkit MVP",
    "start_date": "2026-03-02",
    "due_date": "2026-03-27"
  }
}
```

`labels` is the one exception: `defaults`, the item and any CLI override are
concatenated and de-duplicated, because labels are additive by nature.

## Payload reference

### Issues and user stories

| Field | Type | Notes |
| --- | --- | --- |
| `project` | string | Alias from `GITLAB_PROJECTS`. Required. |
| `title` | string | Required. Doubles as the idempotency key. |
| `description` | string | Markdown body. |
| `labels` | string[] | Merged with `defaults`. |
| `milestone` | string | Title of an existing milestone. |
| `start_date`, `due_date` | date | `YYYY-MM-DD`. `due_date` must not precede `start_date`. |
| `assignees` | string[] | Usernames. Unknown names are reported and ignored. |
| `epic` | string | Title of an existing epic; attached after creation. |
| `issue_type` | string | `issue`, `task` or `incident`. Defaults to `issue`. |
| `weight` | int | Premium feature. |
| `confidential` | bool | Create the issue as confidential. |
| `state` | string | `opened` or `closed`. |
| `notes` | string[] | Extra Markdown appended to the description. |

User stories additionally accept:

| Field | Type | Notes |
| --- | --- | --- |
| `acceptance_criteria` | string[] | Rendered as a checklist. |
| `definition_of_done` | string[] | Rendered as a checklist. |

The description is rendered as:

```markdown
# <title>

<description>

## Acceptance Criteria

- [ ] ...

## Definition of Done

- [ ] ...

## Metadata

| Field | Value |
| --- | --- |
| Project | repo |
| Epic | Automation 1 - GitLab Toolkit |
| Milestone | Automation Week 2 - Core Commands |
| Schedule | 2026-03-09 -> 2026-03-20 |
```

Every item is also appended with a small traceability footer naming the source file
and item index.

### Epics

Same core fields, plus `objectives` (string[]) which is rendered as a checklist. The
`project` field is not used: epics are group-scoped.

Epics require a **Premium/Ultimate** group. On Free and Starter the API answers `403`;
see [Groups without epics](#groups-without-epics) for the fallbacks.

### Milestones

| Field | Type | Notes |
| --- | --- | --- |
| `title` | string | Required. Idempotency key. |
| `description` | string | Markdown body. |
| `scope` | string | `group` (default) or `project`. |
| `project` | string | Alias, required when `scope` is `project`. |
| `start_date`, `due_date` | date | `YYYY-MM-DD`. |
| `state` | string | `opened` or `closed`. |

### Labels

| Field | Type | Notes |
| --- | --- | --- |
| `name` | string | Required. |
| `scope` | string | `project` (default) or `group`. |
| `project` | string | Alias, or `all` for every alias in `GITLAB_PROJECTS`. |
| `color` | string | Hex, e.g. `#1F75CB`. |
| `description` | string | Short label description. |

Creating labels with `project: "all"` expands to one call per configured alias.

## Idempotency

The **title is the identity** of an object. Before creating, the CLI lists what
already exists in the target scope and skips exact matches, reporting them as
`skipped`. Re-running a creation command is therefore safe and only creates what is
missing.

To change an existing object, use an update document (`epics update`,
`issues update`) or the GitLab UI. Creation commands never overwrite.

## Dry run

`--dry-run` performs every read and every validation but performs no writes. It still
reports what *would* be created:

```bash
python main.py --dry-run stories create
```

Auto-creation flags are ignored in dry-run mode, so you see the warnings that would
otherwise be silently resolved.

## Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Success. |
| `1` | Completed with errors or skipped items. |
| `2` | Configuration problem, such as a missing token. |
| `3` | GitLab API error that could not be handled. |
| `130` | Interrupted. |

## Tests

The test suite is fully offline and needs neither a token nor a network:

```bash
python -m unittest discover -s tests
```

## Troubleshooting

**`GITLAB_TOKEN is not set.`**
Copy `.env.example` to `.env` and fill it in. The token needs `api` scope, and
`read_api` for read-only commands.

**`unknown alias 'api'`**
The alias is not in `GITLAB_PROJECTS`. Add it as `alias=group/subgroup/project`.

**`milestone 'X' not found in project 'Y'`**
Run `milestones create`, or use `--create-missing-milestones`. The items are still
created, without the milestone.

**`unknown assignee(s) ignored`**
The username does not match a member of the target project. Check with
`python main.py doctor`.

**`GitLab epics are a Premium/Ultimate feature and the group answered 403.`**
The group is on Free or Starter, where the epics API is refused by licence. The same
token reaches `group info`, labels and milestones fine, so this is not a permissions
problem. Options: run `stories create` (it degrades and creates the items without
epics), add `--no-assign-epics`, use `epics create --skip-unsupported` in scripted
runs, or move the payload to a Premium/Ultimate group.

**A group label is rejected.**
`scope: "group"` requires the `Owner` role on the group. Project labels need
`Maintainer`.

**A permission error appears only when writing.**
The token may lack `api` scope, or the target may require an approval. `doctor`
reports the scopes the token actually has.

## Security

- `.env` holds the token and is git-ignored. Never commit it.
- Tokens only ever appear in the `PRIVATE-TOKEN` header; they are never logged.
- The first version of this project hard-coded a personal access token directly in
  two ad-hoc scripts, and that token was committed. Those scripts have been deleted.
  **The token must be considered compromised: revoke it in GitLab under
  Preferences > Access Tokens and issue a new one.**