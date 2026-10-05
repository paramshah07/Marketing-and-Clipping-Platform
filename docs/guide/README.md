# Clipper guide

How to use Clipper, one page per part of the app. Read them in order the first time; after that, jump to the
page you need.

Use the live app, [https://145-241-239-46.sslip.io](https://145-241-239-46.sslip.io), for your real work. The dev site,
[https://dev.145-241-239-46.sslip.io](https://dev.145-241-239-46.sslip.io), runs the next version: it publishes for real
too, and its data is replaced every 5 days ([Which site to use](01-getting-started.md#which-site-to-use)).

1. [Getting started](01-getting-started.md): which site to use, sign up, sign in and out, Set up Clipper, find your way around, set up an account and a brand, and what a day in Clipper looks like.
2. [Library](02-library.md): bring clips in by upload, link or document, follow their status, clean up, and find what was published.
3. [Editor](03-editor.md): place the logo, crop, pick a filter, a song, a cover and a caption, render, and act on the finished renders.
4. [Customizations](04-customizations.md): brands, saved captions, saved covers and songs, and which one the Editor picks for you.
5. [Calendar](05-calendar.md): the week board, scheduling by hand or with **Auto-schedule**, approving drafts, editing a post.
6. [Accounts](06-accounts.md): your Instagram accounts' time zone, posting slots, daily cap and minimum gap; syncing, reconnecting, disabling.
7. [Publishing and recovery](07-publishing-and-recovery.md): what happens at slot time, how to fix a post that failed, and what to do when Zernio refuses your key.
8. [Telegram bot](08-telegram-bot.md): run Clipper from a Telegram chat, and get alerts there.
9. [Settings and your account](09-settings.md): get a Zernio API key, connect Instagram through Zernio, make and pair a Telegram bot, storage, password and **Log out**.
10. [Troubleshooting and FAQ](10-troubleshooting.md): can't sign in, signups full, key refused, no Instagram accounts, a bot that doesn't answer, storage full, refused links, a video already posted, slow renders, the dev site.

For flows that cross pages (the first day, the nightly routine, a night from your phone, a failed post, cleaning up,
adding an account or a bot, a new Zernio key), see [Workflows](../workflows.md).

## Reading the screenshots

- Numbered blue circles mark the parts of the screen. The table under each screenshot explains each number.
- Labels from the app are in **bold**, exactly as they appear on screen.
- Most screenshots come from a review copy of production's data, signed in as the operator (`clipper`), where
  publishing is switched off. That is why the status footer reads **Publishing off** and **Post now** is greyed out.
  On the live app the footer reads **Publishing live**.
- A few pages also have a short recording (an animated GIF, no sound) of a task that is easier to see moving, such as
  trying filters in the Editor or **Auto-schedule** on the Calendar.
- The saved captions, covers and songs in the screenshots were made for them: the covers are clip frames and the songs
  are plain tones.
- The sign-up, sign-in, Set up Clipper and Settings screenshots, and the refused key and token, come from the dev site,
  with a throwaway user (`docs-shots`) and a made-up key and bot token. The Settings page with a refused key comes
  from a throwaway test stack (its user 1, `clipper`, with a made-up key).
- The date and time fields of the **Schedule…** popover follow your computer's region settings, so a time
  may read 13:00 on your screen where a screenshot shows 01:00 PM. The post drawer's time is always typed
  as 24-hour `HH:MM`.

[Documentation index](../README.md) · Next: [Getting started](01-getting-started.md) →
