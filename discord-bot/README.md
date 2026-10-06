# Community Bot — Python, hosted 24/7 for free

A single Discord bot covering info commands, moderation, welcome + auto-role,
verification, tickets, giveaways, a support queue, server logging and a full
security suite. Text commands, SQLite storage, no external database needed.

Verified on Python 3.12 with discord.py 2.4 (see [Verification](#verification)).

---

## Table of contents

1. [What you get](#what-you-get)
2. [Project layout](#project-layout)
3. [Step 1 — Create the Discord application](#step-1--create-the-discord-application)
4. [Step 2 — Run it on your PC](#step-2--run-it-on-your-pc)
5. [Step 3 — Host it 24/7 for free](#step-3--host-it-247-for-free)
6. [Step 4 — Set up each feature in your server](#step-4--set-up-each-feature-in-your-server)
7. [Command reference](#command-reference)
8. [Verification](#verification)
9. [Troubleshooting](#troubleshooting)

---

## What you get

| Area | Features |
|---|---|
| **Info** | `ping`, `uptime`, `membercount`, `serverinfo`, `userinfo`, `botinfo`, embed `help` |
| **Moderation** | `kick`, `ban`, `unban`, `mute`/`unmute` (timeouts), `warn`, `warnings`, `clearwarnings`, `purge`, `slowmode`, `lock`, `unlock`, `addrole`, `removerole`, `roles` |
| **Welcome** | welcome channel + custom text, welcome DM, leave message, auto-role on join, `{user}`-style placeholders |
| **Verification** | button panel with maths captcha (5 attempts then a 30-minute timeout), one-click mode, manual grant/revoke |
| **Tickets** | panel button, private channel per user, auto numbering, add/remove members, rename, close with transcript |
| **Giveaways** | timed giveaways, 🎉 reaction entries, rerolls, manual end, survives restarts |
| **Queue** | staff add/remove/list/next, reorder, ping, DM, optional dedicated channel + staff role |
| **Logs** | deletes, edits, joins, leaves, bans, unbans, channel + role changes |
| **Security** | anti-spam, anti-raid, link blocking, mass-mention blocking, word filter, lockdown mode, global blacklist, `panic` |

---

## Project layout

```
discord-bot/
├── bot.py                 # entry point, cog loading, error handling, /health endpoint
├── config.py              # reads .env
├── db.py                  # async SQLite layer (all persistent data)
├── utils.py               # embeds, duration parsing, transcripts, helpers
├── cogs/
│   ├── info.py            # ping / uptime / counts / server info
│   ├── moderation.py      # kick, ban, mute, warns, purge, slowmode, lock
│   ├── welcome.py         # welcome, leave, auto-role
│   ├── verification.py    # captcha / button verification
│   ├── tickets.py         # ticket panel + transcripts
│   ├── giveaways.py       # giveaways with restart-safe entries
│   ├── queue.py           # staff support queue
│   ├── logs.py            # server logs
│   └── security.py        # anti-spam, anti-raid, filters, blacklist
├── tests/smoke_test.py    # offline test suite (no Discord connection needed)
├── requirements.txt
├── Dockerfile             # used by every host below
├── fly.toml               # Fly.io config (paid)
├── render.yaml            # Render blueprint (free but sleeps)
├── .env.example
└── run.bat                # Windows launcher
```

---

## Step 1 — Create the Discord application

1. Go to <https://discord.com/developers/applications> → **New Application** → name it.
2. Open **Bot** → **Reset Token** → copy it. **Never share this token.**
3. Still on the Bot page, turn on both privileged intents:
   - **SERVER MEMBERS INTENT** → on
   - **MESSAGE CONTENT INTENT** → on
   
   Without these, welcome messages, join logs, security checks and most
   moderation commands will not work.
4. **General Information** → copy your **Application ID**.
5. Invite the bot with these scopes and permissions (no Administrator):

   `https://discord.com/oauth2/authorize?client_id=YOUR_APPLICATION_ID&permissions=1102061890687&scope=bot`

   The number above grants exactly: view/send messages, embed links, attach
   files, read message history, add reactions, manage channels, manage roles,
   manage messages, manage nicknames, kick, ban, moderate members, use
   application commands.

---

## Step 2 — Run it on your PC

Python 3.12 is already installed on this machine at
`C:\Users\giosa\AppData\Local\Programs\Python\Python312\python.exe`.

Open PowerShell in the `discord-bot` folder:

```powershell
cd C:\Users\giosa\Pictures\discord-bot

# 1. install dependencies (once)
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m pip install -r requirements.txt

# 2. create your config
copy .env.example .env
notepad .env          # paste DISCORD_TOKEN, set OWNER_IDS to your own user ID
```

Or just double-click **`run.bat`** — it creates `.env` for you and starts the bot.

Confirm it works:

```
[your bot] Logged in as CommunityBot#1234
[your bot] Connected to 1 guild(s)
```

Type `!help` in your server.

> Leave the terminal open to keep the bot online. Step 3 moves it to the cloud.

---

## Step 3 — Host it 24/7 for free

### Which host? (checked against live pricing pages)

| Host | Free? | Always on? | Verdict |
|---|---|---|---|
| **Northflank** | Yes — free Sandbox tier, 2 services | **Yes**, "always-on compute – no sleeping" | **Use this** |
| Render | Yes (512 MB) | No — sleeps after 15 min idle | Works only with an UptimeRobot ping every 14 min |
| Fly.io | No free tier for new accounts (card required) | Yes | ~$2/month |
| Railway | No — Hobby needs payment | Yes | Not free |
| Koyeb | Free tier removed | Yes | Not free |
| Oracle Cloud | Always Free VM | Yes | Real free VPS, painful to get a machine |

### Deploy to Northflank (recommended)

1. Push this folder to a **GitHub repository** (the `.env` file is git-ignored,
   so your token stays out of git).
2. Sign up at <https://northflank.com> → choose the free **Sandbox** tier.
3. **Create a service** → connect your GitHub repo → build type **Dockerfile**.
4. Environment variables:

   | Key | Value |
   |---|---|
   | `DISCORD_TOKEN` | your bot token (mark as secret) |
   | `OWNER_IDS` | your Discord user ID |
   | `PREFIX` | `!` |
   | `PORT` | `8080` |
   | `DB_PATH` | `/data/bot.db` |

5. Runtime settings:
   - **Port:** `8080`
   - **Health check path:** `/health`
   - **Instance:** the free sandbox size (256 MB is plenty)
   - **Command:** leave empty — the `Dockerfile` already runs `python bot.py`
6. (Recommended) add a **persistent volume** mounted at `/data` so the SQLite
   file survives redeploys.
7. Click **Deploy** and watch the logs — you should see `Logged in as ...`.
8. Copy the bot's public hostname into the Discord Developer Portal if you want
   the *Public Bot* toggle (optional).

Your bot now runs continuously without your PC being on.

### Deploy to Render (free, but it sleeps)

1. Push to GitHub, then in Render choose **New → Blueprint** (the repo already
   contains `render.yaml`).
2. Add `DISCORD_TOKEN` and `OWNER_IDS` when prompted.
3. Create a free **UptimeRobot** monitor on
   `https://<your-service>.onrender.com/health` every 14 minutes — a Discord bot
   produces no inbound HTTP traffic, so without this Render shuts it down.

### Deploy to Fly.io (paid, ~$2/month)

```bash
fly launch --no-deploy          # picks up the existing Dockerfile
fly volumes create bot_data --size 1
fly secrets set DISCORD_TOKEN=... OWNER_IDS=...
fly deploy
fly machine start
```

---

## Step 4 — Set up each feature in your server

Run these once per server, as an admin.

**Logs** — pick a private channel first:
```
!logs set #mod-logs
!logs test
```

**Welcome + auto-role** (create the roles below the bot's highest role):
```
!setwelcome #welcome
!setwelcome message Welcome {user} to **{server}**! You are member **#{membercount}**.
!setwelcome dm Welcome to {server}! Read the rules in #rules.
!autorole add @Member
!setleave {username} just left — we are now {membercount} members.
!testwelcome
```
Placeholders: `{user}` `{username}` `{tag}` `{id}` `{server}` `{membercount}`

**Verification** (captcha):
```
!verification setup #verify @Member captcha
!verification panel
```
Members press the button, answer a maths question, and get `@Member`.
Wrong answers are counted; 5 failures = 30-minute timeout.
Use `button` instead of `captcha` for click-only verification.
Manage with `!verification grant @user`, `!verification revoke @user`, `!verification off`.

**Tickets**:
```
!ticketsetup #ticket-category #ticket-logs @Support
!ticketpanel #support
```
Then staff use `!ticket close` (saves a transcript), `!ticket add @user`,
`!ticket remove @user`, `!ticket rename new name`. `!tickets` lists open ones.

**Queue**:
```
!qsetup #queue @Support
!qadd @user needs help with billing
!qlist
!qnext
!qdone @user
```
`!qnext` calls the person at the front (public ping + DM), `!qdone` removes
them once you're finished. Also `!qmove @user 1`, `!qping @user`, `!qclear`.

**Giveaways**:
```
!giveaway #general 10m 1 Nitro 3 months
!giveaway 30s 1 Test prize          (runs in the current channel)
!gwlist
!gwend 1
!gwreroll 1
```

**Security**:
```
!security status
!security antispam on 6 8 timeout    # >6 messages in 8s = timeout
!security antiraid on 8 15           # >8 joins in 15s = raid alert
!security linkblock on
!security linkblock allow #rules
!security mentionblock on
!security filter add badword otherword
!security lockdown on                 # only staff can talk
!security lockdown off
!security blacklist add @user raider
!security panic 10                   # kick everyone who joined in the last 10 min
```

---

## Command reference

<details>
<summary>All 48 commands (click to expand)</summary>

**Info** — `ping`, `uptime`, `membercount`, `serverinfo`, `userinfo`, `botinfo`, `help`

**Moderation** — `kick`, `ban`, `unban`, `mute`, `unmute`, `warn`, `warnings`,
`clearwarnings`, `purge`, `slowmode`, `lock`, `unlock`, `addrole`, `removerole`, `roles`

**Welcome** — `setwelcome`, `setwelcome message`, `setwelcome dm`,
`setwelcome off`, `setleave`, `autorole add|remove|show`, `testwelcome`

**Verification** — `verification setup`, `verification panel`,
`verification grant`, `verification revoke`, `verification off`

**Tickets** — `ticketsetup`, `ticketpanel`, `ticket close`, `ticket add`,
`ticket remove`, `ticket rename`, `tickets`

**Giveaways** — `giveaway`, `gwend`, `gwreroll`, `gwlist`, `gwjoin`, `gwleave`

**Queue** — `queue`, `qadd`, `qlist`, `qnext`, `qdone`, `qmove`, `qping`, `qclear`, `qsetup`

**Logs** — `logs set`, `logs off`, `logs test`

**Security** — `security status`, `security antispam`, `security antiraid`,
`security linkblock`, `security mentionblock`, `security filter`,
`security lockdown`, `security blacklist`, `security panic`

</details>

---

## Verification

Everything below runs offline, without a Discord connection:

```powershell
python tests\smoke_test.py
```

```
PASS: 82
All checks passed.
```

It verifies that every cog loads, all 48 commands are registered, the captcha
generator can't hang and always produces the correct answer, the link regex
behaves, and the whole SQLite layer (queue ordering, tickets, giveaway entries,
raid tracking, blacklist, config defaults) works.

`python -m compileall .` checks syntax across the project.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Improper token has been passed` | `DISCORD_TOKEN` is wrong or has whitespace. Reset the token in the Developer Portal if it was ever exposed. |
| Nothing happens when I type `!ping` | The bot lacks the **Message Content Intent**. Turn it on and restart the bot. |
| Welcome / join / leave logs missing | Turn on the **Server Members Intent**. |
| `I'm missing the permission(s)` | Move the bot's role above the roles it must manage, or re-invite it with the link in Step 1. |
| `commands.BotMissingRole` / role errors | Drag the bot's role to the top of the member list. |
| Bot goes offline after ~15 min on Render | Expected on the free tier — use Northflank, or add the UptimeRobot ping. |
| Giveaways lost after redeploy | Attach a persistent volume at `/data`, or accept that redeploys wipe `/data`. |
| DMs never arrive | Users with DMs closed can't be messaged. The bot always reports when a DM fails. |
| Captcha does nothing | The role must be below the bot's highest role. |
| Bot works locally but not on the host | The host needs `PORT`/`/health` wired up — see the health endpoint in `bot.py`. |

---

## Security notes

- `.env` is git-ignored and `.dockerignore` excludes it — your token is only
  ever passed as an environment variable on the host.
- Set the token as a **secret** on the host, not a plain variable.
- Nothing here needs the **Administrator** permission.
- The bot never allows `@everyone`/`@here`/role pings from its own messages;
  user pings are allowed on purpose (giveaway winners, `!qnext`).