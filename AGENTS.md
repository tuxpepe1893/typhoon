# Agent instructions

These rules apply to every AI coding agent working in this repository (Claude Code, Codex, Cursor, and others).

## Commits

- Always add a `Co-Authored-By` trailer that names the model you are running as, and uses the email linked to your tool's GitHub account, so GitHub credits the commit to that account. For example:

  ```text
  Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
  ```

  Other tools use their own GitHub-linked identity in the same format.

## Bugs and issues

- When you discover a bug or issue, file it on GitHub Issues right away with `gh issue create`. You don't need to ask first. Search the open issues first (`gh issue list --search`) and comment on an existing one instead of filing a duplicate.
- Include what happened, what you expected, the steps to reproduce, and the affected files. If you fix it in the same session, link the issue from the commit or PR (for example, `Fixes #12`).
- Redact secrets before filing: no MAC or IP addresses, cloud credentials or session tokens, or HomeKit pairing codes.

## Documentation

- Don't create new user-facing docs (READMEs, guides, `docs/` pages, or other Markdown files) without the user's explicit approval. Updating existing docs to match a code change is fine.
