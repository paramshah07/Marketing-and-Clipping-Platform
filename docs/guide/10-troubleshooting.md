# Troubleshooting and FAQ

The common problems, what causes them, and the fix. Messages are quoted as Clipper shows them. When something here
says "ask the operator", that is whoever runs your Clipper and gave you its address.

- [Signing in](#signing-in)
- [Zernio and Instagram](#zernio-and-instagram)
- [Telegram bots](#telegram-bots)
- [Clips and renders](#clips-and-renders)
- [The dev site](#the-dev-site)

## Signing in

### I can't sign in

- **Wrong username or password.** Usernames are lower case (capitals are ignored when you type them). Check you are on
  the right site: an account made on the dev site doesn't exist on the live app, and a live-app account reaches the
  dev site only with its next reset ([Which site to use](01-getting-started.md#which-site-to-use)).
- **Too many failed sign-ins: try again in 15 minutes.** 10 wrong passwords for that username from your network within
  15 minutes. Wait, then try once more carefully.
- **This account is disabled.** The operator disabled it. Ask them.
- **You forgot your password.** There is no reset link. Ask the operator for a new one, then change it in
  **Settings** ([Forgotten password](01-getting-started.md#forgotten-password)).
- **The server refused a request from this address: it expects another one (APP_BASE_URL).** The change didn't come
  from Clipper's own address. Open Clipper at its address ([Which site to use](01-getting-started.md#which-site-to-use))
  and try again; if it keeps happening, tell the operator.

### Clipper signed me out

Sessions last up to 30 days: using Clipper once fewer than 15 are left renews one to 30, so it ends 15 to 30 days after you last used it. You are also signed out when you change your password in another
browser, when the operator sets a new password for you, and, on the dev site, at every reset. Sign in again: Clipper
takes you back to the page you were on.

### Signups are full

A site takes 15 enabled accounts, the operator's included, and the sign-up page then says **Signups are full.** Ask the
operator: they can disable an account nobody uses, which frees its spot, or raise the limit. The live app and the dev
site count separately; on the dev site, the copies of live-app accounts count too.

## Zernio and Instagram

### Zernio refused my key

- **When you click Verify** ("Zernio refused the key: copy it again from Zernio's API keys page"): the key was copied
  incompletely, was revoked, or expired. Copy it again from [zernio.com/dashboard/api-keys](https://zernio.com/dashboard/api-keys),
  or create a new one there. Zernio shows a key only once, so if you no longer have it, make a new one.
- **Later** (the card reads **Refused** and the footer **Zernio key refused**): someone revoked the key in Zernio, it
  expired, it lacks the publishing permission, or Zernio reports a failed payment. The card's red line says which.
  Fix it in Zernio and click **Re-check**, or **Replace key** with a new one made with Zernio's defaults. Each reason
  is in [What the card tells you](09-settings.md#what-the-card-tells-you).
- **"Zernio won't list your accounts with this key: create one with full access (Full, Read-write)"**: the key has some
  of Zernio's permission groups switched off (it starts with `zrk_`). Make a new key with Zernio's defaults and
  **Replace key**.
- **"This Zernio account is already connected to another Clipper user"**: one Zernio account belongs to one Clipper
  user on a site. Use your own Zernio account. (On the dev site, if your live-app account uses this Zernio account,
  sign in to the dev site with your live-app account instead of a separate one.)
- **"This key is another Zernio account's; …"**: your Instagram accounts came from another Zernio account. Use a key
  from that one.

### No Instagram accounts found

The **Instagram accounts** card reads **None found**, or the Accounts page is empty.

1. Check the account is connected in Zernio, in a profile of the Zernio account your key belongs to.
2. Check it is an Instagram **Business or Creator** account. Personal accounts can't post through Zernio.
3. Check your key's scope is **Full** (all profiles): a key limited to some profiles doesn't see the others.
4. Click **Re-check** on the card. If it now says "@name is connected to another Clipper user", someone else on this
   site added that account first. If it says "beyond your Zernio plan's account limit", upgrade the plan or remove an
   account in Zernio, then **Re-check**.
5. Connected two Instagram accounts to one Zernio profile? The second replaced the first. Connect each one in its own
   profile ([Instagram accounts](09-settings.md#what-instagram-needs)).

### My posts stay Scheduled past their time

Look at the status footer ([The status footer](01-getting-started.md#the-status-footer)):

| Footer | Fix |
|---|---|
| **No Zernio key** | Add your key in **Settings**. |
| **Zernio key refused** | Fix the key in **Settings** (above). |
| **Publishing off** | The server's switch is off: tell the operator. |
| **Publisher offline** or **Worker offline** | Tell the operator. |
| **Publishing live** | The post may be a draft (dashed, **Draft**): drafts never publish, **Approve** it. Or its account is **Disconnected**: reconnect it in Zernio. |

Once it is fixed, posts less than 30 minutes late go out, and later ones move to their next free slots (your bots say
so).

### An account says Disconnected

Its Instagram login stopped working in Zernio. Reconnect it in Zernio, in the same profile, then click **Sync
accounts** on the Accounts page (or **Re-check** in **Settings**). Its failed posts move to its next free slots.
See [Reconnect a disconnected account](06-accounts.md#reconnect-a-disconnected-account).

## Telegram bots

### The bot says Token rejected

Telegram refused the bot's token: it was revoked or replaced in @BotFather. Send @BotFather `/token`, pick the bot,
and paste the new token under **Add another bot** in **Settings**. The bot keeps its chat
([A revoked or new token](09-settings.md#a-revoked-or-new-token)).

When you first add a bot, **Telegram refused the token: copy it again from @BotFather** means the token was copied
incompletely or is an old one: copy it again, or get a fresh one with `/token`.

### The bot doesn't answer

| In Settings | Cause | Fix |
|---|---|---|
| **Waiting for Start** | Not paired with a chat yet. | **Pair**, then **Open @yourbot and tap Start** and tap **Start** in Telegram. |
| **Not responding** | Clipper's bot service is down, or another program uses the same token (another app, or the same bot on the other Clipper site). | Make sure the bot runs only here. If you can't tell, send @BotFather `/token` for the bot and paste the new token here: the old one stops working everywhere else. If it lasts, tell the operator. |
| **Running** | You wrote from another chat: a bot answers only its paired chat, without a reply to anyone else, groups included. | Use the paired chat, or **Re-pair** from the new one. |

If you sent messages while the bot was down, it ignored them on purpose, so an old tap never acts hours later. It says
"Back online. … ignored: send them again." when it returns.

### I don't get alerts

1. Check at least one bot has **Alerts** on and reads **Running**.
2. Click **Test**. **Telegram didn't take the test message: …** gives Telegram's reason, for example the chat blocked
   the bot: unblock it in Telegram (or **Re-pair**).
3. Some alerts are sent once only: a failed post once, an account problem at most once per account every 6 hours
   ([Alerts](07-publishing-and-recovery.md#alerts)).

### The pairing code expired

A code works for 15 minutes. Click **Get a new code** (or **Pair** again) and tap **Start** within that time.

### "Another app receives this bot's messages (a webhook)"

The bot is already used by another app. Clipper won't take it over, because that would break the other app. Make a new
bot for Clipper with @BotFather `/newbot`.

## Clips and renders

### My storage is full

"your storage is full (… of … GB): delete clips or renders to make room": uploads, imports and renders stop until you
make room. The quickest wins: **Free up space** on **Library** › **Published** deletes the MP4s of Reels already on
Instagram, and ticking clips in the [Library](02-library.md#find-and-remove-clips) deletes many at once with their
renders. Renders you no longer need go from the [Editor](03-editor.md) (a render that a post still uses can't go, unless
the post is cancelled). Step by step: [Cleaning up your library](../workflows.md#cleaning-up-your-library).
**Settings** › **Account** shows how much you use ([Storage](09-settings.md#storage)). For a bigger limit, ask the
operator.

### A clip I deleted is still there

A clip stays while one of its renders has a post that isn't cancelled: a draft, scheduled or failed post keeps it until
you cancel or dismiss that post, and a published one keeps it for good (it is how Clipper knows never to post the video
twice). A clip still uploading, downloading or probing, or with a render still rendering, stays too. After a bulk
delete, the clips that stayed remain ticked, and a note says why.

"the server is almost out of disk space: try again later" (`DISK_FULL`) is the server, not you: tell the operator.

### My link import is refused

- **"…: only a link to one video on YouTube, Instagram, TikTok, X, Facebook can be imported"**: the link isn't a single
  video on those sites (a profile, a playlist, a search, a shortened link from another service, or another site).
  Open the video itself and copy its address. Only the operator's account imports from other sites.
- **Failed** with **Needs login cookies** (`LOGIN_REQUIRED`): the site wants a logged-in visitor, or is rate-limiting
  the server. **Retry** later, or download the video yourself and **Upload** it.
- **Already in your Library**: you have that video already, perhaps from another link to it (a tracking query, or
  `youtu.be` against `youtube.com`). The message names the clip: open it instead.
- Other causes, and whether a retry helps: [Clip statuses](02-library.md#clip-statuses).

### Clipper asks "Post it there again?"

That video already went to the account, or is queued there: as another render of the same clip, or as another clip of
the same link. Clipper never posts a video to an account twice unless you say so, so **Auto-schedule** leaves such a
render unplaced (with the reason), and placing it by hand asks first. Click **Cancel** to place nothing, or **OK** to
post it again. The Telegram bot asks the same way ([Calendar](05-calendar.md#place-one-render-on-a-slot)).

### A render card says MP4 deleted

**Free up space** deleted that render's video file, because every post of it is on Instagram already. It can't be
posted again; **Re-render for…** (Library › **Published**) or a new render in the Editor makes a fresh one.

### A render failed with "Its song was deleted"

The song was deleted from **Customizations** › **Music** before the render ran (`MUSIC_MISSING`), or before a
**Re-render for…** or **Re-render and retry** of a render that had it. Renders already finished keep the song. Pick
another song (or **None**) in the Editor and render again.

### Where is Instagram's music library?

Switched off. Picking a track from Instagram's own library (its music and trending sounds) only works for accounts
connected to Zernio with Facebook Login, so Clipper doesn't offer it. Upload your own songs to **Customizations** ›
**Music** and pick one in the Editor instead: it is mixed into the render ([Add music](03-editor.md#add-music)).

### Instagram muted my Reel's sound

The song mixed into the render is part of your video, so Instagram checks it like any other: it may mute or block a Reel
with copyrighted music. Use songs you have the rights to ([Add music](03-editor.md#add-music)).

### Renders are slow on long clips

A render re-encodes every frame at 1080x1920, so it takes longer the longer the clip is: a clip of several minutes takes
minutes. Your renders and downloads also run one at a time, and take turns with other users' (the live app runs two at
once in all; the dev site one, at a lower priority than the live app's). Until its turn, a render reads **Queued**;
while it runs, **Rendering** with the time it has taken so far.

You don't need to wait on the page: move on to the next clip, and the card updates by itself. If a render sits at
**Queued** for a long time while the footer shows **Worker offline**, tell the operator.

## The dev site

### I scheduled something on the dev site by mistake

The dev site publishes for real. Before its time, open the post on the **Calendar** and click **Cancel post**. Once it
is live, delete the Reel in Instagram: it can't be deleted through Clipper or Zernio.

### My clips, brands or posts on the dev site are gone

The dev site is reset to a fresh copy of the live app's data about every 5 days. Everything you made there goes:
clips, renders, posts, files, brands, saved captions, covers and songs, and your Instagram accounts' settings. Your dev-site
account stays, with its password, Zernio key and bots ([The dev site](01-getting-started.md#the-dev-site)). Click
**Re-check** on **Settings** to bring your Instagram accounts back, then set their posting slots and add your brands
again. Do real work on the live app.

### My dev-site username changed

The live app took that username since, so the reset renamed your dev-site account `yourname.dev`. Sign in with the new
name and your old password.

[Guide](README.md) · ← Previous: [Settings and your account](09-settings.md) · Next: [Workflows](../workflows.md) →
