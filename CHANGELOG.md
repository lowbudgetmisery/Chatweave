# Changelog

## 1.12.0

- The chat game can now be themed for **any streamer**. A theme file holds the streamer's name (`{streamer}` in lines), the crew name, ranks, targets, loot and lines as before, plus a new `[card]` section: title, subtitle, tags, prompts, difficulty labels, result stamps, `!stash` wording, dock button names, three colors, and an emblem (`"dwarf"`, `"none"`, or your own image).
- Added: `game_themes\pirate_raid.toml`, a complete second theme ("Pirate Raid", Deckhands, anchor emblem, teal colors), to copy from.
- `heist.toml` (Lumi's Gliese Heist) now uses `{streamer}` and lists every `[card]` setting. An older `heist.toml` without them keeps the same look.
- Theme mistakes are caught on start with the exact line (bad color, missing `{command}` in a prompt, unknown placeholder, missing emblem file...).

## 1.11.0

- Renamed to **Chatweave** everywhere you see it: the app is now `chatweave.exe`, the download is `chatweave_app_v....zip`, releases are titled Chatweave, and the log file is `logs\chatweave.log`. Your `config.toml`, YouTube login, `settings.json`, recordings and heist stashes carry over unchanged. (The code's internal name is still `chatmerge`, so `python -m chatmerge` and old recordings keep working.)
- Added: a demo GIF at the top of the README.
- Fixed: the heist card said "1 Gumis"; it now says "1 Gumi".

## 1.10.0

- Added: **BetterTTV and FrankerFaceZ emotes** (global and the channel's own, including BTTV shared emotes), alongside Twitch and 7TV. On a name clash, channel emotes win over global ones, then 7TV, BTTV, FFZ.
- Added: **zero-width emotes** stack on the emote before them (7TV zero-width emotes, BTTV `cvMask`/`cvHazmat`/`TopHat` and friends, visible FFZ modifier emotes). Works on Twitch's own emotes too.
- Added: **BTTV emote modifiers** `w!` `h!` `v!` `z!` `c!` `l!` `r!` `p!` `s!`, and **FFZ effect emotes** (`ffzW`, `ffzX`, `ffzY`, `ffzCursed`; supporter-only effects like `ffzHyper` and `ffzRainbow` only for the people FFZ lists). Same looks as the add-ons.
- All three lists reload every 15 minutes. If one add-on's site is down, the others keep working and the last good list stays.
- Demo mode shows a zero-width emote and the party/shake effects.

## 1.9.0

- New chat game, **Gliese Heist**, replacing Starcatch. Built around Lumi's space thief lore: heists her Gumis join with `!heist` (more crew, better odds; loot for everyone on success), quick `!steal` races, and `!stash` ranks from Gumi Recruit to Lumi's Favorite Gumi. Starts at random times or from the dock (**Start heist**, **Quick steal**). Still overlay-only: nothing is posted to chat.
- All the game's text lives in `heist.toml` (targets, loot, ranks, lines), checked line by line on start. `teasing = false` leaves out the teasing fail lines.
- `config.toml` `[game]` has new settings (`heist_command`, `steal_command`, `stash_command`, `event_min_minutes`, `event_max_minutes`, `heist_join_seconds`, `steal_seconds`, `teasing`, `content_file`). The old Starcatch settings are ignored. An old `data\starcatch.db` is left alone and not used.
- Shared Chat partner viewers can't play, and a subs-only overlay makes the game subs-only too.

## 1.8.2

- Fixed: Setup and the app build picked the newest installed Python, which can be too new for the pinned packages (found on GitHub's build machine with Python 3.15). They now prefer 3.12, then 3.13, 3.11, 3.14.
- Added: GitHub builds the Windows app (`chatmerge.exe`, no Python needed) for every update; tagged versions are published on the Releases page.

From a live test on a real Twitch channel and an unlisted YouTube stream:
- Confirmed live: YouTube finds your own stream by itself; Twitch deletes and slow mode on/off.
- Found: YouTube does not report single removed messages to outside apps (the removed message arrived; nothing followed). README now says so and points to the dock's Hide button.
- Changed: plain-language YouTube lookup errors, for example "the channel the app is logged in to can't live stream ... run YouTube Login.bat and pick the channel you stream on" instead of "HTTP 403 liveStreamingNotEnabled".

## 1.8.1

From a 39-minute live recording (Twitch + YouTube, 3,927 lines, replayed with zero errors):
- Added: Twitch announcements (`/announce`) show with their color; watch streaks show too. Both stay out of the events window. Bot filters apply to them.
- Added: a permanent test that replays real message shapes from that recording, with every name, id and text replaced.
- Changed: stopping with Ctrl+C closes the OBS and dock connections first, to avoid a burst of error text on Windows.
- Confirmed live: replies, first-time chatters, Super Chats in other currencies, YouTube gifts and gifted memberships, member milestones, and duplicate YouTube messages across reconnects being shown once.

## 1.8.0

Added
- Twitch Shared Chat (collabs): partner-channel lines get a "collab" tag, or can be hidden or shown plain (`shared_chat`). Partner subs and raids show as plain lines and stay out of the events window. Deletes that name the partner channel's message id remove the copy too.
- Twitch replies show a small "replying to @name: ..." line (`show_replies`).
- First-time Twitch chatters get a FIRST MESSAGE tag in the dock only.
- Dock shows chat modes as chips while they're on: Twitch sub-only, emote-only, followers-only, slow, unique-chat; YouTube members-only.
- Demo feed includes a reply, a first-time chatter and a collab line.

## 1.7.0

Added
- Known chat bots (Nightbot, Fossabot, Moobot, Wizebot, Sery_Bot, Botisimo) and `!commands` are hidden from the overlay by default. Tip bots (StreamElements, Streamlabs, SoundAlerts) are optional.
- Sub-only overlay: only subs, YouTube members, VIPs, mods and the streamer show on stream, plus all support events. The dock still shows everyone and dims the lines viewers can't see.
- Dock: **Hide user** button (both platforms, now and later), **Settings** panel that applies instantly and saves to `settings.json`, **Spawn a star** when the game is on.
- Starcatch: optional overlay-only catch game on a new `/game` browser source (`[game]` in config.toml, off by default).
- `Record Chat.bat` / `run --record` saves raw chat; `replay` plays it back through the same code for testing.
- Log file in `logs/chatmerge.log` (about 6 MB kept).
- App build: `build_exe.ps1` makes a folder with `chatmerge.exe` that needs no Python.

Changed
- `requirements.txt` now lists exact versions tested together. Run `Setup.bat` again after updating.
- The server refuses requests addressed to any other host name, and websocket connections from other websites.
- If recording can't write (disk full), recording stops and chat keeps running.
- The game does its saving in the background and can never hold up chat.
- The dock's Settings panel saves only what you changed in it, so it can't undo a Hide user from another dock.

Fixed
- A fresh install could pick a grpcio-tools whose generated YouTube code needs a newer protobuf than the one installed, so YouTube chat failed to load. The generated code is now rebuilt whenever those package versions change.

## 1.6.0

- Per-action overlay behavior for deletes, timeouts, bans and Twitch `/clear` (remove, placeholder or keep), fade or instant removal, blocked words and links, dock Hide and Clear overlay buttons.
