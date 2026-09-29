# Telegram bot

A Clipper bot lets you do from a Telegram chat what you do in the web app: import, render, schedule, approve and recover posts, and it sends your failure alerts.

It is your own bot, made with Telegram's @BotFather, and it works through the same API as the web app, as you: every rule and confirmation is the same, and it only ever sees your clips, renders, accounts and posts. This page shows how to use it (adding one is in [Settings](09-settings.md#telegram-bots)); [docs/telegram-bot.md](../telegram-bot.md) is the design reference. There are no screenshots of the chat: the examples are chat transcripts, with buttons in `[brackets]`.

## Add a bot

Make a bot with @BotFather, paste its token in **Settings** › **Telegram bots**, and pair it with your chat: the full
walkthrough, what each bot status means (**Running**, **Waiting for Start**, **Token rejected**, **Not responding**),
the **Alerts** switch, **Test**, **Re-pair**, a revoked token and **Remove** are in
[Settings › Telegram bots](09-settings.md#telegram-bots).

In short: send @BotFather `/newbot`, paste the token and click **Verify**, click **Open @yourbot and tap Start**, tap
**Start** in Telegram, and the bot answers **Paired. This chat runs your Clipper now.** Each bot answers only its chat
and ignores everyone else without a reply, including group chats. Use a different bot on the live app and on the dev
site.

## Before you start

- Open your private chat with your bot and send `/help`. Tap `/` in the chat for the command menu.
- Failure alerts go to every bot of yours with **Alerts** on.
- **Open in Clipper** buttons open the web app, which asks you to sign in first if you aren't.

## Commands

| Command | What it shows |
|---|---|
| `/status` | Whether the database, workers and your publishing are up (and why not), with your rendering, scheduled and failed counts |
| `/clips [text]` | The library, 10 per page; add text to search (also `/library`) |
| `/renders` | Recent renders |
| `/ready` | Renders ready to schedule, to select and auto-schedule (also `/queue`) |
| `/calendar` | One account's week, day by day (also `/week`) |
| `/drafts` | Drafts waiting for approval, with **Approve all** |
| `/failed` | Failed posts, oldest first |
| `/published [text]` | Published posts, with day range, account and brand filters |
| `/brands` | Brands, their logos and caption templates |
| `/accounts` | Instagram accounts and their posting slots |
| `/help` | What the bot does (also `/start`) |
| `/cancel` | Drops a question the bot is waiting on |

Lists end each line with a tappable id: `/c12` opens clip 12, `/r34` render 34, `/p56` post 56, `/b4` brand 4, `/a1` account 1.

## Import videos

Send the bot a video, a link, several links, or a document full of links (docx, xlsx, pptx, odt, txt, csv, md, rtf or html):

```text
You:  https://www.tiktok.com/@creator/video/7684… @creator
Bot:  Import this TikTok by @creator?
      tiktok.com/@creator/video/7684…
      [Import] [Cancel]
```

- A video file can be up to 20 MB. An `@name` in its caption, or next to a link, sets the creator. Upload bigger files in the web app, or send their link.
- A link already in the library gets its clip card instead.
- For many links the bot sums up first (`Found 14 videos (TikTok 6 · Instagram 8): 3 already in the library, 2 repeats. Import 11?`), then sends one message when every clip has finished.

## Render a clip

A clip's card has **Render…**, **Renders (_n_)**, **Creator** and **Watch**, plus **Retry** and **Remove** when they apply. **Render…** opens the render editor, one message that updates as you tap:

```text
Render · clip 12 · tiktok.com/@creator/video/7684…
00:21 · 1920x1080 · audio

Brand: Kite VPN
Logo: Top right · 22% of the width · opacity 100% (brand default)
Crop: centre, the source fills the 9:16 frame
Caption: 79/2200 characters · 2/30 hashtags
[Brand: Kite VPN]
[↖] [↑] [↗]
[←] [·] [→]
[↙] [↓] [↘]
[−] [22%] [+] [Opacity 100%]
[Crop: centre] [Caption]
[Render] [Close]
```

The brand starts as the one this clip was last rendered with; otherwise you pick one. The arrows snap the logo to a corner, edge or centre; − and + change its size by 2%, and **Opacity** steps through 100, 75, 50 and 25%. Once you move the logo, **Save as default** makes that placement the brand's default. The caption comes from the brand's template, and **Template** puts it back after you edit it. **Render** queues it, and the render's card arrives when it is done. The editor stays open for another variant.

## Schedule a render

A ready render's card has **Watch** (sends the MP4, up to 50 MB), **Schedule…**, **Post now** and **Delete**. **Schedule…** suggests the next free slot:

```text
Schedule render #196 · Flux Energy · tiktok.com/@the_tomato… · 00:16
Account: @afro.yahu (Europe/London)
When: Thu 1 Oct 13:00 BST (in 3d), the next free slot
Caption: …
[Schedule for Thu 1 Oct 13:00]
[Other time…] [Post now]
[Caption] [Close]
```

**Other time…** shows the next 8 days, then that day's posting slots (taken ones marked) and **Type a time…**. Typed times are in the account's zone: `18:30`, `6:30pm`, `tomorrow 9am`, `fri 13:00`, `2026-10-02 09:00` or `now`. A time that breaks the minimum gap or the daily cap gets a **Heads-up** line, as on the Calendar.

A draft result offers **Approve** straight away. **Post now** asks once, then reports **Live on Instagram** with the link when the Reel is up.

## Post cards

Every post opens as a card: its status, account, time, brand, clip and caption. The buttons depend on the status:

| Status | Buttons |
|---|---|
| Draft | **Approve** · **Move…** · **Caption** · **Post now** · **Cancel post** |
| Scheduled | **Move…** · **Caption** · **Post now** · **Cancel post** |
| Publishing | **Refresh** |
| Published | **View on Instagram** · **Re-render for…** |
| Failed or dead letter | The remedy · **Details** · **Dismiss** |

**Move…** works like **Other time…** above. **Re-render for…** renders the same clip and crop again for the brand you pick, with that brand's default logo placement and caption.

## Your week

`/calendar` shows one account's week from today, in its zone. Each day lists its posts, then its free slots:

```text
@afro.yahu · Mon 28 Sep to Sun 4 Oct · Europe/London
Today 2/3 · min gap 60 min · Zernio quota 2/100

Mon 28 Sep · today
09:00 Published · Acme · tiktok.com/@creator/vid… /p228
13:00 Published · Kite VPN · tiktok.com/@creator/v… /p229
free 19:00

Tue 29 Sep
09:00 Scheduled · Northwind Coffee · tiktok.com/@sc… /p231
…
[◀] [This week] [▶]
[Drafts (2)] [Ready to schedule]
```

`/ready` lists the renders waiting for a time. Tap each one to tick it (or **Select all**), then **Auto-schedule _n_ → @account**. The reply lists where each went, which ones are drafts to approve, and why any were not placed. `/drafts` has **Approve all _n_**, which lists them before it approves.

## Failed posts and alerts

When a post fails, each of your bots with **Alerts** on sends an alert:

```text
Bot:  @afro.yahu post 234 failed: Instagram rejected the video or caption (format, length or policy).
      [Open post]
      [Open in Clipper]
```

**Open post** sends the post card, with the same single remedy as the web app's [Recover page](07-publishing-and-recovery.md#the-recover-page):

```text
Publishing failed · post 234
@afro.yahu · Sun 27 Sep 19:00 BST (yesterday)
Northwind Coffee · tiktok.com/@nimpliq/video/7684… · 00:10 /r24
Instagram rejected the video. Instagram rejected the video or caption (format, length or policy).
Zernio said: Media processing failed: …
[Re-render and retry]
[Details] [Dismiss]
[Open in Clipper]
```

For a disconnected account the remedy is two buttons: **Reconnect in Zernio**, then **I've reconnected: check now**. After a remedy, and after **Post now**, the bot follows the post for an hour and tells you when it is live, fails again, or moves to another slot. An account-disconnected alert (from any account sync) has **Reconnect in Zernio** and **Sync accounts** buttons.

## Accounts and brands

- **Account card** (`/accounts`, then `/a1`): **Slots** (the same presets as the web app, or **Type times…** like `09:00 13:00 19:00`), **Timezone**, **Daily cap**, **Min gap**, **Calendar**, **Disable** or **Enable**, and **Sync accounts**. A disconnected account adds **Reconnect in Zernio**.
- **Brand card** (`/brands`, then `/b4`): **Name**, **Template**, **Link**, **Logo**, **Placement** (the default logo placement), **Auto-approve on/off** and **Archive**. `/brands` also has **New brand** and **Show archived**.

> [!TIP]
> Send a logo as a **file** (paperclip, then File), not as a photo. Telegram turns photos into JPEGs, which loses the transparency.

## Good to know

| Limit | Value |
|---|---|
| Videos the bot can receive | 20 MB each |
| Videos the bot can send (**Watch**) | 50 MB each |
| Time to answer a question the bot asked | 10 minutes |

- When the bot asks for text, reply with it; `-` clears an optional field, and `/cancel` or any command drops the question.
- Alerts and **Open in Clipper** buttons link to the site the bot belongs to: a dev-site bot opens the dev site.
- Anything you send while the bot is down is ignored, so an old **Post now** tap never publishes hours later. The bot says so when it is back: send it again.
- A confirm button acts once; a second tap only answers **Already done.** Open forms and lists expire when the bot restarts (**This has expired: run the command again**); buttons on cards keep working.
- While publishing is off, **Post now** answers **Publishing is off … nothing can post now**.
- The web app alone has free drag for the logo and crop, a live preview before rendering, and the saved captions and covers from [Customizations](04-customizations.md).

← Previous: [Publishing and recovery](07-publishing-and-recovery.md) · [Guide](README.md) · Next: [Settings and your account](09-settings.md) →
