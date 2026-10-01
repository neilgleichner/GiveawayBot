# AsanPanel Giveaway Bot

Secure Telegram bot for weekly giveaways. Participants are identified only by Telegram User ID and can register once per giveaway after a valid operator message and a live channel-membership check.

## Production prerequisites

1. Create the bot with BotFather. Add it as an administrator to `@AsanPanel_News` and to its linked discussion supergroup. It must receive ordinary group messages (administrator status meets this requirement).
2. Copy `.env.example` to `.env`, set a real Bot token, database password, and a random `WEBHOOK_SECRET`. Do not commit this file.
3. Start the private stack: `docker compose up -d --build`.
4. Open a private chat with the bot as the configured administrator. In the exact discussion thread for a channel post, run `/new_giveaway` and then `/start_giveaway <id>` as a reply to any message in that same thread.

The container deliberately uses long polling: no inbound HTTP port is exposed. It is a good default for a single VPS. If switching to webhooks later, place the application behind Nginx, HTTPS, Telegram webhook secret verification, and an IP/firewall policy.

## Admin commands (private chat only)

`/new_giveaway [title]`, `/start_giveaway <id>`, `/close_giveaway <id>` (یا `/stop_giveaway`)، `/reset_giveaway <id>`, `/status <id>`, `/stats <id>`, `/export <id> ids|eligible|participants`, `/recheck_membership <id>`, `/remove_eligibility <giveaway_id> <user_id> <reason>`, `/draw <id>`, `/history`, `/operators`, `/operator_add <name>`, `/operator_rename <operator_id> <name>`, `/operator_alias_add <operator_id> <alias>`, `/operator_alias_remove <alias_id>`, `/operator_disable <operator_id>`.

`/new_giveaway` must be sent in the designated comment group and in the intended discussion thread. It binds that thread permanently to the giveaway.

## Security notes

- PostgreSQL and Redis are private Docker-network services; no host port is published.
- Never paste an export into a group. The bot sends exports only to the requesting allowed admin in private chat.
- Back up PostgreSQL using encrypted, off-host storage and test restore regularly.
- Review Docker image and dependency updates before production use.
