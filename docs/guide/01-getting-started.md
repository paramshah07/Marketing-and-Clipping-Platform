# Getting started

Create your account, connect your Zernio key, Instagram accounts and (if you like) a Telegram bot, learn the layout,
and see how a normal day goes.

## Sign up

Clipper has room for a limited number of users (15, the operator included). Each user has their own library,
brands, accounts, posts and bots, and nobody else sees them.

![The Create your account page: a username field, a password field, 2 of 3 spots left, and a Create account button](../images/sign-up.png)

1. Open [https://145-241-239-46.sslip.io/signup](https://145-241-239-46.sslip.io/signup), or **Sign up** under the
   sign-in form.
2. Pick a **Username**: 3 to 32 characters, lower-case letters, digits, dot, dash or underscore. Anyone can see that a
   name is taken, so don't use anything private.
3. Pick a **Password** of at least 8 characters.
4. Click **Create account**. You are signed in and land on **Set up Clipper**.

The page shows how many spots are left. When it says **Signups are full.**, ask the operator. There is no email and
no reset link: if you forget your password, the operator sets a new one for you.

## Sign in

![The Sign in page: a username field, a password field and a Sign in button](../images/sign-in.png)

1. Open [https://145-241-239-46.sslip.io](https://145-241-239-46.sslip.io). Any page asks you to sign in first, and
   takes you back to it afterwards.
2. Enter your **Username** and **Password** and click **Sign in**.

You stay signed in on that browser for 30 days, renewed as you use it. **Log out** is on the **Settings** page. After
10 wrong passwords within 15 minutes, sign-in for that username stops working from your network for up to 15 minutes.

> [!NOTE]
> The operator signs in as `clipper`. Everything from before Clipper had accounts (clips, brands, accounts, posts, the
> Zernio key and the three Telegram bots) belongs to that account, and works as it did.

Clipper works on a phone too. The Recover page is made for one, because Telegram alerts link straight to it.

## Set up Clipper

After signing up you land on **Set up Clipper**: three steps, each a card that shows its state live. Uploads,
renders and brands work without them, so **Do this later** takes you to the Library; the **Setup _n_/3** badge in the
sidebar brings you back. The same cards stay on the **Settings** page.

![Set up Clipper for a new user: the Zernio API key card with its three steps and a Verify button, the Instagram accounts card waiting for the key, and the optional Telegram bots card with the Add a bot steps](../images/setup.png)

### 1. Zernio API key

Clipper publishes through [Zernio](https://zernio.com), with your own Zernio account. Zernio's first 2 Instagram
accounts are free.

1. Sign up or log in at [zernio.com](https://zernio.com).
2. Open **API keys** and click **Create API key**. Keep the defaults: scope **Full** (all profiles), permission
   **Read-write**, no expiry.
3. Copy the key (it starts with `sk_`, and Zernio shows it once), paste it into the card and click **Verify**.

Clipper checks the key with Zernio, stores it encrypted, and never shows it again. The card then reads **Connected as**
your Zernio name and email, with the key's last four characters and when it was last checked. If Zernio refuses it,
the card says why in red, with the error code:

| Message | What to do |
|---|---|
| Zernio refused the key (`ZERNIO_KEY_INVALID`) | Copy it again from Zernio's **API keys** page, or make a new one |
| Already connected to another Clipper user (`ZERNIO_USER_CLAIMED`) | One Zernio account belongs to one Clipper user. Use your own Zernio account |
| Another Zernio account's (`ZERNIO_ACCOUNT_CHANGED`) | Your Instagram accounts came from the Zernio account you connected first: use a key from that one |
| A post is publishing (`KEY_IN_USE`) | Wait a minute and try again |
| Won't list your accounts with this key | The key has limited access: make a Full, Read-write one |

Once connected: **Re-check** asks Zernio again (and fetches your Instagram accounts again), **Replace key** takes a
new key, and **Remove** deletes it (your scheduled posts then stop going out until you add one again).

### 2. Instagram accounts

Accounts are connected in Zernio, not in Clipper: a Business or Creator account, one Zernio profile per account.

1. Click **Connect in Zernio**, create a profile, and connect the Instagram account in it.
2. Back in Clipper, click **Re-check**. Your accounts appear with a **Connected** chip.

The card warns when Zernio has no accounts for you yet, when an account is already connected to another Clipper user
(it isn't added for you), or when an account is beyond your Zernio plan's limit (Zernio won't post to it until you
upgrade or remove one). More in [Accounts](06-accounts.md).

### 3. Telegram bots (optional)

A Telegram bot of your own sends you failure alerts and runs Clipper from the chat. Add as many as you like; each
answers only the chat you pair it with. The steps, from @BotFather to the test message, are in
[Telegram bot › Add a bot](08-telegram-bot.md#add-a-bot).

## Settings

**Settings** (your username at the bottom of the sidebar) has the same three cards, plus **Account**: your username,
your storage, **Change password** and **Log out**.

![The Settings page: the Zernio API key, Instagram accounts and Telegram bots cards, then Account with the username, storage used of the quota and Log out](../images/settings.png)

- **Storage** is your clips and renders against your limit (5 GB unless the operator changed it). When it is full,
  uploads, imports and renders stop until you delete some clips or renders.
- **Change password** needs your current one, and signs out your other browsers.

## The layout

Every page shares a sidebar on the left and a header along the top.

![Clipper's Library page with the sidebar on the left: page links, a red failed-post badge on Calendar, and the status footer](../images/overview.png)

| # | What it is |
|---|---|
| 1 | The pages: **Library**, **Calendar**, **Accounts** and **Customizations**. The Editor opens from the Library. |
| 2 | Failed posts badge. The red number counts posts that failed; click it to open the oldest one's Recover page. It only shows when something failed. |
| 3 | Status. The first thing standing between a post and Instagram: the api, the database, the workers, then publishing (the server's switch, or your Zernio key). |
| 4 | Renders queued or running, and posts scheduled to go out (drafts don't count). |
| 5 | The page header: the page title, its tabs, and its main actions on the right. |

Under the status, the footer's last row is your username, which opens **Settings**, with a **Setup _n_/3** badge that
opens **Set up Clipper** until your Zernio key and an Instagram account are in place. (The screenshot above predates
that row.)

![The sidebar footer: No Zernio key in amber, 0 rendering and 0 scheduled, and the username alice.e2e with a Setup 0/3 badge](../images/footer-no-key.png)

### The status footer

The footer checks the system every 10 seconds and shows the worst problem first. Hover it for a one-line
explanation.

| Status | What it means |
|---|---|
| **Publishing live** (green) | All is well. Your scheduled posts go out to Instagram at their time. |
| **No Zernio key** (amber) | You have no Zernio key yet: your scheduled posts stay **Scheduled**. Click it to open **Settings**. |
| **Zernio key refused** (red) | Zernio refused your key, so your publishing is paused. Click it, then **Re-check** or **Replace key**. |
| **Publishing off** (amber) | The server's publishing switch is off: renders run, but nothing reaches Instagram, for anyone. Expected on a review copy; on the live app, tell the operator. |
| **Worker offline** (red) | Nothing renders or downloads until the worker is back; ready posts still publish. When both workers are down: nothing renders or publishes. |
| **Publisher offline** (red) | Nothing publishes and no alerts go out until the publisher is back. Renders run. |
| **Database offline** (red) | Nothing renders or publishes until the database is back. |
| **API offline** (red) | The server isn't answering, so nothing on the page is current. |

Pages that show "Couldn't load …" during an outage reload by themselves once the server answers again.

## First-time setup, after Set up Clipper

Do this once, after your Zernio key and an Instagram account are connected ([Set up Clipper](#set-up-clipper)).
Each step links to the page that explains it in full.

### 1. Set when it posts

On the account's card, on the **Accounts** page:

1. Pick the **Timezone** the account posts in. Posting times are in this zone.
2. Set the **Times**: remove a time with its ×, add one with **Add**, or replace them all from **Presets**.
3. Set the **Daily cap** (most posts a day) and **Min gap** (fewest minutes between two posts).

A new account starts on Europe/London with the **Every hour, 07:00–23:00** preset, a daily cap of 10 and a
30-minute gap. Changes save as you make them.

### 2. Add a brand

A brand is an advertiser: its logo, link and caption.

1. Go to **Customizations** › **Brands** and click **New brand**.
2. Under **Logo**, click **Choose PNG** (or drop the file). It must be a PNG with a transparent background.
3. Fill in **Name** (required), **Link** and **Caption template**. In the template, `{link}` becomes the link
   and `{creator}` the clip creator's @handle.
4. Turn on **Default brand** if the Editor should pick this brand for clips you haven't rendered yet.
5. Click **Create**.

Leave **Auto-approve** off while you get used to Clipper: every post then waits as a draft until you approve
it. More in [Customizations](04-customizations.md).

### 3. Optional: saved captions and covers

Under **Customizations**, **Captions** holds reusable caption text and **Covers** holds cover images
(**Upload cover**). The default cover is preselected in the Editor; the default caption fills in when the
brand has no caption template.

### 4. Check the footer

The status footer should read **Publishing live**. If it reads anything else, nothing of yours will reach
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
6. **Fix what failed.** If a post fails, the red badge appears on **Calendar**, and your Telegram bots with alerts
   on send an alert.
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
