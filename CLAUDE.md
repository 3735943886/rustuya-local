# Notes for Claude Code

- Commit messages: do not add a `Claude-Session:` line (the session URL). The `Co-Authored-By:` trailer is fine.
- Releases: bump the version in `pyproject.toml` and `custom_components/rustuya/manifest.json`, and move the
  `## [Unreleased]` notes in `CHANGELOG.md` to a `## [X.Y.Z] — YYYY-MM-DD` section, in the same "Release X.Y.Z: ..."
  commit. Then tag `vX.Y.Z` and push; the release workflow uses that section as the GitHub Release notes and refuses a
  version without one (`tests/unit/test_release_notes.py` catches it before the tag).
