# Discord setup

The project needs a community server. Creating one is a human action: a Discord
account must own the guild, and only an account (not a bot token) can create one.
This file records the intended structure so that whoever creates it does it once,
correctly.

## What to create

1. In the Discord desktop or web app, create a server from a template ("Gaming
   Community" or blank), name it **KuberWeaver**.
2. Enable **Community** in Server Settings, then set:
   - Rules channel: `#rules`
   - Community updates: `#announcements`
   - Keep the default verification level; raise it if spam appears.
3. Create these channels (text):
   - `#announcements` (announcement type, read-only for members)
   - `#general`
   - `#help`
   - `#showcase` (user deployments and screenshots)
   - `#plugins` (brick and extension discussion)
   - `#infra` (network, hypervisor and target talk)
4. Optional voice channel `#voice`.
5. Roles:
   - `Maintainer` (the author)
   - `Contributor` (self-assignable through a reaction role, once the bot exists)
   - `@everyone`: read history, no mention of `@everyone`.

## Rules to paste in `#rules`

Keep them short and enforceable:

- Be respectful. No harassment, no hate speech, no spam.
- Keep it technical. Paste logs with secrets redacted, not screenshots of a
  terminal full of tokens.
- Do not ask maintainers to debug your personal infrastructure.
- No self-promotion in `#general`; use `#showcase`.
- Security reports go through the repository's private advisory, never in chat.

## Invite link

Generate a permanent invite from Server Settings, then add it to:

- `README.md` (badge or plain link under Community)
- `SUPPORT.md` (first entry of "Where to ask")
- `site/index.html` (footer)

Until the server exists, neither the README nor the site advertises one: they do
not carry a community link at all, and no invite is invented here.

## Bot (optional, later)

A bot adds reaction roles, issue notifications and release announcements. It
needs its own application in the Discord Developer Portal, a token stored as a
secret, and explicit scopes (`bot`, `messages.read`). Until then, moderation and
notifications are manual. Never store a bot token in the repository.
