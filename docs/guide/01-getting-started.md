# Getting started

Sign in to Clipper, learn the layout, set up your first account and brand, and see how a normal day goes.

## Sign in

1. Open [https://145-241-239-46.sslip.io](https://145-241-239-46.sslip.io).
2. Your browser asks for a username and password. For both, reach out to pjsrsns@gmail.com.
3. Clipper opens on the **Library**.

There is one shared username and password and no separate user accounts, so anyone who has them can do
everything, including publishing. Keep them to yourself.

Clipper works on a phone too. The Recover page is made for one, because Telegram alerts link straight to
it.

## The layout

Every page shares a sidebar on the left and a header along the top.

![Clipper's Library page with the sidebar on the left: page links, a red failed-post badge on Calendar, and the status footer](../images/overview.png)

| # | What it is |
|---|---|
| 1 | The pages: **Library**, **Calendar**, **Accounts** and **Customizations**. The Editor opens from the Library. |
| 2 | Failed posts badge. The red number counts posts that failed; click it to open the oldest one's Recover page. It only shows when something failed. |
| 3 | Status. The first thing standing between a post and Instagram: the api, the database, the worker, then the publishing switch. |
| 4 | Renders queued or running, and posts scheduled to go out (drafts don't count). |
| 5 | The page header: the page title, its tabs, and its main actions on the right. |

### The status footer

The footer checks the system every 10 seconds and shows the worst problem first. Hover it for a one-line
explanation.

| Status | What it means |
|---|---|
| **Publishing live** (green) | All is well. Scheduled posts go out to Instagram at their time. |
| **Publishing off** (amber) | Renders run, but nothing reaches Instagram: scheduled posts stay **Scheduled**. Expected on a review copy; on the live app, see [deploy.md](../deploy.md). |
| **Worker offline** (red) | Nothing renders or publishes until the worker is back. |
| **Database offline** (red) | Nothing renders or publishes until the database is back. |
| **API offline** (red) | The server isn't answering, so nothing on the page is current. |

Pages that show "Couldn't load …" during an outage reload by themselves once the server answers again.

## First-time setup

Do this once. Each step links to the page that explains it in full.

### 1. Connect an Instagram account

Clipper publishes through Zernio, so accounts are connected there, not in Clipper.

1. In Zernio, create a profile for the account: one profile per Instagram account, so each keeps its own
   queue and limits.
2. In that profile, connect Instagram. It must be a Business or Creator account.
3. In Clipper, go to **Accounts** and click **Sync accounts**. The account appears as a card.

**Connect account** on the same page shows these steps, with a link to Zernio. More in [Accounts](06-accounts.md).

### 2. Set when it posts

On the account's card:

1. Pick the **Timezone** the account posts in. Posting times are in this zone.
2. Set the **Times**: remove a time with its ×, add one with **Add**, or replace them all from **Presets**.
3. Set the **Daily cap** (most posts a day) and **Min gap** (fewest minutes between two posts).

A new account starts on Europe/London with the **Every hour, 07:00–23:00** preset, a daily cap of 10 and a
30-minute gap. Changes save as you make them.

### 3. Add a brand

A brand is an advertiser: its logo, link and caption.

1. Go to **Customizations** › **Brands** and click **New brand**.
2. Under **Logo**, click **Choose PNG** (or drop the file). It must be a PNG with a transparent background.
3. Fill in **Name** (required), **Link** and **Caption template**. In the template, `{link}` becomes the link
   and `{creator}` the clip creator's @handle.
4. Turn on **Default brand** if the Editor should pick this brand for clips you haven't rendered yet.
5. Click **Create**.

Leave **Auto-approve** off while you get used to Clipper: every post then waits as a draft until you approve
it. More in [Customizations](04-customizations.md).

### 4. Optional: saved captions and covers

Under **Customizations**, **Captions** holds reusable caption text and **Covers** holds cover images
(**Upload cover**). The default cover is preselected in the Editor; the default caption fills in when the
brand has no caption template.

### 5. Check the footer

The status footer should read **Publishing live**. If it reads **Publishing off**, nothing will reach
Instagram; see [The status footer](#the-status-footer).

## A day in Clipper

The usual routine, in the evening, to fill tomorrow's slots.

1. **Bring clips in.** In the **Library**, paste a link (and the creator's @handle, if you have it) and click
   **Import**. For many links at once, use **Import links** with a document or pasted text. For files, click
   **Upload** or drop them on the **Drop videos here** bar. A clip is ready to edit when its status reads
   **Ready**. See [Library](02-library.md).
2. **Brand and render.** Hover a clip and click **Open editor**. The brand is already picked (the one this
   clip was last rendered with, else the default brand). Check the logo, crop, cover and caption, then click
   **Render** (⌘↵). You can move on to the next clip while it renders. See [Editor](03-editor.md).
3. **Schedule.** Open the **Calendar**. Finished renders wait in **Ready to schedule** on the right. Select
   them and click **Auto-schedule** to fill the next free slots, or drag one onto a slot.
   See [Calendar](05-calendar.md).
4. **Approve.** A post arrives as a draft unless its brand has **Auto-approve** on, and a render with no
   logo always arrives as one. A draft never publishes. Look them over, then click **Approve** on each, or
   approve them all with **Approve _n_ drafts** in the header (it lists them first).
5. **Let it run.** Every minute Clipper sends out the posts that are due, so a Reel is usually live within
   two minutes of its slot. Published posts appear under **Library** › **Published**, each with a link to its
   Reel.
6. **Fix what failed.** If a post fails, the red badge appears on **Calendar** and Telegram sends an alert.
   Open the post's Recover page and apply the remedy it offers, or **Dismiss** the post. See
   [Publishing and recovery](07-publishing-and-recovery.md).

The same routine as a diagram: [Workflows](../workflows.md#the-nightly-routine).

## Good to know

> [!WARNING]
> A published Reel can't be deleted through Clipper or Zernio. Check the logo and caption before a post is
> approved; to remove a live Reel, delete it in Instagram itself.

> [!TIP]
> On the **Calendar**, the sidebar folds to icons when the window is 1400 px wide or less, to make room for
> the week.

[Guide](README.md) · Next: [Library](02-library.md) →
