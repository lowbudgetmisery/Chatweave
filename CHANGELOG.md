# Changelog

## 1.8.2

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
