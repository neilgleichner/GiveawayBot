# Security operating notes

## Before deployment

- Set a unique PostgreSQL password in both `POSTGRES_PASSWORD` and the password portion of `DATABASE_URL`.
- Generate `WEBHOOK_SECRET` with at least 32 random bytes, even though the initial deployment uses long polling.
- Keep `.env` readable only by the deployment account (`chmod 600 .env`).
- Ensure the Linux firewall does not expose PostgreSQL (5432) or Redis (6379). This Compose file does not publish either port.
- Add the Bot as an administrator to the channel and linked discussion group. Verify its privacy setting permits regular discussion messages.

## Data handling

- Exports are generated in memory and delivered only to an allowlisted administrator's private chat; they are never written to a public directory.
- Audit records retain administrative actions, but logs must not include Bot tokens, database URLs, passwords, or complete participant exports.
- Review retention requirements before deciding how long to retain `message_attempts` and participant data.

## Incident response

If the Bot token, database password, or VPS is suspected to be exposed: revoke/rotate the Bot token through BotFather, rotate database credentials, invalidate the old deployment secret, inspect audit logs, and restore only from verified encrypted backups if necessary.
