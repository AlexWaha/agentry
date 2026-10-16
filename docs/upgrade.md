# Upgrade, pin and roll back

The plugin installs from GitHub into a cache directory, and your session runs from that copy. Pushing to `main` should therefore change nothing for you. A release is a version bump in `plugin.json`, a changelog entry and a `vX.Y.Z` tag, and an update is something you run on purpose.

Everything below was run against Claude Code 2.1.299. Where a command has not been measured, the section says so.
## Table of contents

- [Which version am I on](#which-version-am-i-on)
- [Pin a version per project](#pin-a-version-per-project)
- [Upgrade](#upgrade)
- [Roll back](#roll-back)
- [Decision flow](#decision-flow)
- [For maintainers: releasing](#for-maintainers-releasing)

## Which version am I on

```bash
claude plugin list
```

The `Version:` line under `agentry@agentry` is what this project runs. Releases follow semantic versioning and are tagged `vX.Y.Z`. Each is listed on the repository's Releases page with its notes, and in [CHANGELOG.md](../CHANGELOG.md).

## Pin a version per project

A local-scope install is recorded per project, so it should stay on the installed version until you run `claude plugin update`.

To pin a project to a release, put the tag or branch after `#` when you add the marketplace:

```bash
claude plugin marketplace add AlexWaha/agentry#v0.2.0 --scope local
claude plugin install agentry@agentry --scope local
```

Claude Code passes the ref to `git clone --branch`, so a tag or a branch should work and a commit SHA does not. A SHA fails with "Remote branch <sha> not found". The ref is stored in the marketplace entry, for example `"ref": "v0.2.0"`. A version suffix such as `agentry@agentry@0.2.0` is silently ignored, so do not use it.

Running `marketplace add` again for the existing `agentry` marketplace prints "already on disk" and changes nothing. There is no re-clone and the ref stays. To move a pinned project to another tag, remove and re-add the marketplace, as in [Roll back](#roll-back).

Status: measured on 2.1.299 with the branch form (`#main`) only. The tag form, and a pinned project staying put across a real version bump, were not measured at release time; no release tag existed when this was measured.

## Upgrade

```bash
claude plugin marketplace update agentry
claude plugin update agentry@agentry --scope local
```

Then restart Claude Code. Read the [changelog](../CHANGELOG.md) first, and run both commands from the project root so the update applies to that project.

Status: on 2.1.299, `marketplace update` keeps the ref pin, and `plugin update` at the same version prints "already at the latest version". An upgrade across a real version bump from GitHub was not measured at release time. To change tags, remove and re-add as in [Roll back](#roll-back).

## Roll back

If an upgrade breaks a gate, remove the plugin and the marketplace from the project, then add the marketplace again pinned to the previous release:

```bash
claude plugin uninstall agentry@agentry --scope local
claude plugin marketplace remove agentry --scope local
claude plugin marketplace add AlexWaha/agentry#v<previous> --scope local
claude plugin install agentry@agentry --scope local
```

Restart Claude Code. Release tags from `v0.2.0` onward are listed under the repository's Releases page.

> **Warning.** `marketplace remove` cascades. It uninstalls every plugin installed from that marketplace, at every scope, not only the local one.

Status: all four commands exit 0, measured on 2.1.299 with a branch ref (the tag form was not measured at release time, see Pin).

## Decision flow

<p align="center"><a href="assets/upgrade-rollback.jpg"><img src="assets/upgrade-rollback.jpg" alt="Upgrade and rollback flow. Start by running claude plugin list to see which version you run. If you want a newer one, run claude plugin marketplace update agentry, then claude plugin update agentry@agentry --scope local, then restart Claude Code. If not, ask whether an upgrade broke a gate. If it did, uninstall the plugin, remove the marketplace (this cascades to every scope), add the marketplace again at the previous tag, then install the plugin and restart. If no gate broke, there is nothing to do." width="100%"></a></p>

The commands are in [Which version am I on](#which-version-am-i-on), [Upgrade](#upgrade) and [Roll back](#roll-back).

## For maintainers: releasing

`main` is protected, so a release starts on a task branch.

1. Bump `version` in `plugin/.claude-plugin/plugin.json` and add the `CHANGELOG.md` entry on the task branch.
2. Merge into `main` through the gated local merge.
3. Tag the merge commit `vX.Y.Z`.
4. Push `main` and push the tag. Each push needs its own recorded approval.
5. Publish the GitHub release with the notes from `docs/release/`.
6. On each machine that runs the plugin: `claude plugin marketplace update agentry`, then `claude plugin update agentry@agentry --scope local`, then restart.
