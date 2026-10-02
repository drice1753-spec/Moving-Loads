# Email Offerings — User Guide

`offerings` is a command-line tool that runs the truckload-offering mailbox for a
surplus building-materials business: it turns a deal into a customer email,
blasts it to your buyer list, reads the replies, answers delivered-price
questions with a real freight quote, hands firm offers to you, nudges buyers
who went quiet, and emails you a daily digest. It is built to be boring and
safe: it does nothing to Gmail until you say `--live`, it writes buyer-facing
replies as Gmail *drafts* until you tell it to send, and it never accepts an
offer on your behalf.

This guide is for the owner. For the module-by-module contract that the code
implements, see [`EMAIL_OFFERINGS_DESIGN.md`](EMAIL_OFFERINGS_DESIGN.md).

---

## 1. What it does

Today the workflow lives in one Gmail account and one person's head: a cost
sheet goes to the sales team, a cleaned-up version is BCC'd to a few hundred
buyers, the replies trickle in, and whoever is at the keyboard answers "what
is that delivered to 73127?" by looking up freight and typing a reply. The
system does the same eight things, in the same voice, with a paper trail.

| # | What you do today, by hand                                                                                                            | What `offerings` does                                                                                                                                                 |
|---|---------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1 | Write an **internal cost sheet** to the sales team (`DELETE RED ... FOB: Calhoun, GA ... COST FOB: $0.84/sf ... BRING BACK ALL FIRM OFFERS ... DELETE RED.`, subject ending `(AWR 10/1)`). | `offerings offering add --sheet` reads that email as a text file and turns it into an offering. `offerings internal ID` sends the cost sheet to the sales team for you. |
| 2 | **Blast** the customer version: To yourself, BCC ~300 buyers, subject `$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (New 10/1)` or `MAKE OFFERS: ... (New 10/1)`; re-send later as `... - 10/1 update`. | `offerings blast ID` schedules it; the next `offerings run` sends it in BCC groups of 50, To: you. `--kind update` produces the "- 10/1 update" re-send.                 |
| 3 | **Forward personally** to one buyer: `MARCUS>>$0.99/sf ...` with a one-line note.                                                      | `offerings blast ID --to buyer-one@example.com --kind personal --note "I can deliver this truckload at $0.99/sf."`                                                    |
| 4 | Answer **"how cheap delivered to 73127 on 2 truckloads?"** by looking up freight and replying.                                          | Classifies the reply, quotes freight from the FOB point to the ZIP with the Google Distance Matrix API, writes the reply in your style, and drafts or sends it per your policy. |
| 5 | Negotiate **firm offers**.                                                                                                             | Escalates every firm offer to you by email, labels the Gmail thread `Offerings/Offer`, and never answers it with a yes or a no.                                        |
| 6 | Wade through out-of-office replies, bounces, and "take me off your list".                                                              | Ignores auto-replies, marks bounced addresses, unsubscribes anyone who asks. None of them ever get a reply.                                                            |
| 7 | (Nobody) follows up with buyers who never answered.                                                                                     | After `follow_up_after_days` with no reply, drafts (or sends) one short "still available" nudge per buyer, at most `max_follow_ups` times.                            |
| 8 | (Nobody) has an overview of what went out and what came back.                                                                           | `offerings digest` emails you a summary of blasts, quotes, offers, unsubscribes and the drafts waiting for you in Gmail.                                               |

Units it understands: `/sf` (square foot), `/sy` (square yard), `/lf`,
`/ea`, `/plt` (pallet) and `/TL` (truckload). FOB points are free text
(`Calhoun, GA`, `Dalton, GA`, `Dallas, TX`, ...).

---

## 2. Quick start

Everything below runs in **dry-run mode**: no Gmail call is made, every
"sent" message lands as a `.eml` file in the `outbox/` directory.

### 2.1 Install

```bash
git clone <your repo url> Moving-Loads
cd Moving-Loads
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,llm]"          # llm = optional Claude classifier
offerings --help
```

Python 3.10 or newer is required.

### 2.2 Create a config and the database

```bash
cp examples/config.example.json config.json
```

Edit `config.json`: set `sender_email` to the Gmail address that will send
(`you@example.com` in the examples), `sender_name`, `company_name`,
`sales_team_email` and `signature`. Leave the secrets empty; they come from
environment variables later. Keep `policy.allowed_recipient_domains` as
`["example.com"]` for now so nothing can reach a real buyer by accident.

```bash
offerings --config config.json init
```

This creates `offerings.db` (SQLite) in the current directory. Every later
command takes the same `--config config.json` (or set
`OFFERINGS_CONFIG=config.json` once and drop the flag).

Add `config.json`, `offerings.db`, `outbox/` and any `client_secret.json`
to `.gitignore` if you keep this checkout in git.

### 2.3 Load a buyer list

Buyers live in a CSV with this header (column order is free, tags are
separated by `|`):

```
email,name,company,postal_code,city_state,tags
```

```bash
offerings --config config.json buyers import examples/buyers.example.csv
offerings --config config.json buyers list
```

Re-importing is safe: existing buyers keep their status, and blank cells
never overwrite a value you already have. One-off additions:

```bash
offerings --config config.json buyers add buyer-six@example.com --name "Jordan Lee" --company "Lee Flooring" --zip 37203
```

### 2.4 Add an offering

**From a JSON file** (see `examples/offering.example.json` for every field):

```bash
offerings --config config.json offering add --file examples/offering.example.json
```

**From one of your existing internal cost-sheet emails.** Open the email in
Gmail, choose *Show original* or copy the text, and save it as a `.txt` file
whose first line is the subject:

```
Subject: $0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (AWR 10/1)

DELETE RED...FOB: Calhoun, GA...COST FOB: $0.84/sf...SELL: $0.99/sf...Suggested Sell Below $1.09/sf delivered...BRING BACK ALL FIRM OFFERS...DELETE RED.

5mm/12mil Silver Rustic Oak SPC Vinyl Click Flooring, 7 x 48 planks with attached IXPE pad.
...
```

```bash
offerings --config config.json offering add --sheet examples/internal_sheet.example.txt
# if the file has no "Subject:" first line:
offerings --config config.json offering add --sheet sheet.txt --subject '$0.99/sf Silver Rustic Oak SPC Vinyl Click Flooring (AWR 10/1)'
```

The parser reads `FOB:`, `COST FOB:`, `SELL:` / `Suggested Sell`,
`BRING BACK ALL FIRM OFFERS` / `MAKE OFFERS`, `approx 6 truckloads` and
`20,000 sf per truckload`, keeps the whole `DELETE RED ... DELETE RED` span
and the cost lines in the *internal* fields, and uses the rest of the body
as the customer-facing description. The `(AWR 10/1)` suffix is dropped from
the title.

Offerings start as `draft`. Add `--activate` to make them `active`
immediately, or leave that to `blast`, which activates a draft when you
schedule it.

```bash
offerings --config config.json offering list
offerings --config config.json --json offering list        # machine-readable
```

Note the id: the JSON example is `silver-rustic-oak-spc-2026-10-01`; a sheet
gets `<title-slug>-<date you added it>`, e.g.
`silver-rustic-oak-spc-vinyl-click-flooring-2026-10-01`.

### 2.5 Preview before anything is scheduled

```bash
ID=silver-rustic-oak-spc-2026-10-01
offerings --config config.json offering preview $ID                      # the blast as buyers see it
offerings --config config.json offering preview $ID --internal           # the DELETE RED cost sheet for the sales team
offerings --config config.json offering preview $ID --buyer buyer-one@example.com --note "I can deliver this truckload at $0.99/sf. Pretty great deal."
```

The blast preview must never show your cost, your "Suggested Sell" note or
the words `DELETE RED`. If it does, the description in your offering file
contains them outside a `DELETE RED ... DELETE RED` span: move them into
`internal_notes`.

### 2.6 Blast in dry run and read the outbox

```bash
offerings --config config.json blast $ID
offerings --config config.json run
# [DRY RUN] campaigns=1 sent=5 drafts=0 replies=0 quotes=0 offers=0 follow_ups=0 suppressed=0 expired=0 skipped=0 errors=0
ls outbox/
```

Each message the engine would have sent is a standard RFC 822 file,
`1-sent.eml`, `2-draft.eml`, ... Open them in any mail client, or list them:

```bash
python3 - <<'EOF'
import glob, email
from email import policy
for path in sorted(glob.glob("outbox/*.eml")):
    msg = email.message_from_file(open(path), policy=policy.default)
    print(f"{path}\n  Subject: {msg['Subject']}\n  To: {msg['To']}\n  Bcc: {msg['Bcc']}\n")
EOF
grep -il "delete red\|cost fob\|suggested sell" outbox/*.eml || echo "no internal data in the outbox"
```

With five example buyers and `bcc_chunk_size` 50 you get one file with
`To: you@example.com` and five `Bcc` addresses. To watch the chunking, run
once with `OFFERINGS_POLICY_BCC_CHUNK_SIZE=2 offerings ... run`.

Two things that make a dry run "do nothing" and are not bugs:

* **Quiet hours.** By default nothing buyer-facing goes out between 20:00 and
  07:00 in `timezone` (US/Eastern). If you try the quick start in the
  evening, the campaign stays `scheduled`. Disable for the test with
  `OFFERINGS_POLICY_QUIET_HOURS_START=none`.
* **Domain allow-list.** With `allowed_recipient_domains: ["example.com"]`,
  a buyer at any other domain is skipped and counted under `skipped`.

Dry run has an empty inbox, so `replies=0` is expected; reply handling is
exercised on the next rung of the rollout ladder (section 5).

```bash
offerings --config config.json status
```

shows counts of offerings, buyers, campaigns and the most recent actions.

---

## 3. Configuration

Settings come from the JSON file (`--config PATH` or `OFFERINGS_CONFIG`) and
from environment variables; **environment variables win**. Every
top-level field maps to `OFFERINGS_<FIELD>` in upper case, every policy
field to `OFFERINGS_POLICY_<FIELD>`. Booleans accept `true/false`, `1/0`,
`yes/no`, `on/off`. Lists are comma separated. Integer fields that may be
empty (`quiet_hours_*`) accept `none` or an empty string to mean "off".

`--live` on the command line forces `policy.live` on for that command;
`--db PATH` overrides `db_path`.

### 3.1 Settings

| Field                           | Environment variable                      | Default           | Meaning                                                                                                                                 |
|---------------------------------|-------------------------------------------|-------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| `sender_email`                  | `OFFERINGS_SENDER_EMAIL`                  | *(required)*      | The Gmail address that sends everything and is the `To:` on blasts. Mail *from* this address in the inbox is never treated as a reply.  |
| `sender_name`                   | `OFFERINGS_SENDER_NAME`                   | `""`              | Display name in `From:`; also used for the default signature (`-Alex`).                                                                 |
| `company_name`                  | `OFFERINGS_COMPANY_NAME`                  | `""`              | Company line under the signature.                                                                                                       |
| `reply_to`                      | `OFFERINGS_REPLY_TO`                      | `""`              | `Reply-To:` header. Empty means replies come back to `sender_email`, which is what the inbox reader expects.                            |
| `escalation_email`              | `OFFERINGS_ESCALATION_EMAIL`              | `sender_email`    | Where firm offers and digests are sent. Always sent for real in live mode, never drafted.                                                |
| `sales_team_email`              | `OFFERINGS_SALES_TEAM_EMAIL`              | `""`              | Recipient of `offerings internal ID` (the cost sheet). That command fails if this is empty.                                             |
| `signature`                     | `OFFERINGS_SIGNATURE`                     | `""`              | Full signature text. Empty builds `-{sender_name}` plus `company_name`.                                                                 |
| `google_maps_api_key`           | `OFFERINGS_GOOGLE_MAPS_API_KEY`           | `""`              | Distance Matrix API key for freight quotes. Empty: delivered-price requests cannot be quoted and become drafts for you plus an error entry. |
| `rate_per_mile`                 | `OFFERINGS_RATE_PER_MILE`                 | `4.5`             | Freight cost per road mile per truckload, in dollars.                                                                                   |
| `freight_margin_per_truckload`  | `OFFERINGS_FREIGHT_MARGIN_PER_TRUCKLOAD`  | `0.0`             | Flat dollars added to each truckload's freight before it is quoted.                                                                     |
| `anthropic_api_key`             | `OFFERINGS_ANTHROPIC_API_KEY`             | `""`              | Claude API key for the reply classifier.                                                                                                |
| `anthropic_model`               | `OFFERINGS_ANTHROPIC_MODEL`               | `claude-opus-5-5` | Model used by the Claude classifier.                                                                                                    |
| `classifier`                    | `OFFERINGS_CLASSIFIER`                    | `auto`            | `rules` (keyword rules only), `claude` (requires the key; fails otherwise), `auto` (Claude when the key is set and the `anthropic` package is installed, else rules). |
| `gmail_client_id`               | `OFFERINGS_GMAIL_CLIENT_ID`               | `""`              | OAuth client id (section 4.1).                                                                                                          |
| `gmail_client_secret`           | `OFFERINGS_GMAIL_CLIENT_SECRET`           | `""`              | OAuth client secret.                                                                                                                    |
| `gmail_refresh_token`           | `OFFERINGS_GMAIL_REFRESH_TOKEN`           | `""`              | Long-lived refresh token for `sender_email`. All three Gmail values are required for `--live`.                                          |
| `gmail_label_prefix`            | `OFFERINGS_GMAIL_LABEL_PREFIX`            | `Offerings`       | Threads get labels `<prefix>/Offer` and `<prefix>/Needs reply`.                                                                         |
| `db_path`                       | `OFFERINGS_DB_PATH`                       | `offerings.db`    | SQLite database file. `--db` overrides.                                                                                                 |
| `outbox_dir`                    | `OFFERINGS_OUTBOX_DIR`                    | `outbox`          | Where dry-run `.eml` files are written.                                                                                                 |
| `timezone`                      | `OFFERINGS_TIMEZONE`                      | `US/Eastern`      | Local time zone for quiet hours and the daily send counter.                                                                             |

### 3.2 Policy (`"policy": {...}` in the file)

| Field                        | Environment variable                            | Default | Meaning                                                                                                                                                                   |
|------------------------------|-------------------------------------------------|---------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `live`                       | `OFFERINGS_POLICY_LIVE`                         | `false` | `false`: nothing touches Gmail, mail goes to `outbox_dir`. `true`: real Gmail. Requires the three Gmail credentials.                                                      |
| `auto_reply_mode`            | `OFFERINGS_POLICY_AUTO_REPLY_MODE`              | `draft` | Buyer-facing automatic replies (quotes, "interested" answers, ZIP requests, offer acknowledgements): `draft` creates Gmail drafts for you to send, `send` sends them, `off` only logs. |
| `auto_send_follow_ups`       | `OFFERINGS_POLICY_AUTO_SEND_FOLLOW_UPS`         | `false` | `false`: follow-ups are drafted. `true`: sent without review.                                                                                                             |
| `acknowledge_offers`         | `OFFERINGS_POLICY_ACKNOWLEDGE_OFFERS`           | `false` | Send (or draft, per `auto_reply_mode`) a short "got your offer, will confirm shortly" to a buyer who makes a firm offer. The offer itself is still only escalated to you.   |
| `max_sends_per_run`          | `OFFERINGS_POLICY_MAX_SENDS_PER_RUN`            | `200`   | Maximum recipient addresses per `run` pass. A blast to 300 buyers in chunks of 50 counts 300.                                                                              |
| `max_sends_per_day`          | `OFFERINGS_POLICY_MAX_SENDS_PER_DAY`            | `1500`  | Maximum recipient addresses per local calendar day. Keep it under your Gmail account's own daily limit.                                                                    |
| `bcc_chunk_size`             | `OFFERINGS_POLICY_BCC_CHUNK_SIZE`               | `50`    | Buyers per BCC message on a blast or update.                                                                                                                               |
| `follow_up_after_days`       | `OFFERINGS_POLICY_FOLLOW_UP_AFTER_DAYS`         | `3`     | Days after the first contact before a silent buyer gets a follow-up.                                                                                                      |
| `max_follow_ups`             | `OFFERINGS_POLICY_MAX_FOLLOW_UPS`               | `1`     | Follow-ups per buyer per offering. `0` disables follow-ups.                                                                                                               |
| `quiet_hours_start`          | `OFFERINGS_POLICY_QUIET_HOURS_START`            | `20`    | Local hour (0-23, inclusive) after which no buyer-facing mail goes out. `none` disables quiet hours.                                                                       |
| `quiet_hours_end`            | `OFFERINGS_POLICY_QUIET_HOURS_END`              | `7`     | Local hour (exclusive) when sending resumes. The default window 20-7 wraps midnight.                                                                                      |
| `allowed_recipient_domains`  | `OFFERINGS_POLICY_ALLOWED_RECIPIENT_DOMAINS`    | *(empty = any)* | Comma-separated list. When set, only buyers at these domains can receive anything. Use your own domain while testing live.                                         |
| `unsubscribe_footer`         | `OFFERINGS_POLICY_UNSUBSCRIBE_FOOTER`           | `true`  | Append "Reply with REMOVE to stop receiving these offerings." to buyer-facing mail.                                                                                        |
| `inbox_lookback_days`        | `OFFERINGS_POLICY_INBOX_LOOKBACK_DAYS`          | `7`     | How far back the very first inbox poll looks. Later polls continue from where the previous one stopped.                                                                   |
| `offering_ttl_days`          | `OFFERINGS_POLICY_OFFERING_TTL_DAYS`            | `30`    | Offerings without an explicit `expires_at` expire this many days after `created_at`. Expired offerings get no more blasts or follow-ups.                                   |

A minimal environment-only setup, no file at all:

```bash
export OFFERINGS_SENDER_EMAIL=you@example.com
export OFFERINGS_SENDER_NAME="Alex Rivera"
export OFFERINGS_COMPANY_NAME="Alpinewest Resources"
export OFFERINGS_SALES_TEAM_EMAIL=sales-team@example.com
offerings init
```

---

## 4. Credentials

None of these belong in the repository or in a config file you commit. Put
them in an environment file with `chmod 600` (section 7 shows the layout)
and let the environment override the empty strings in `config.json`.

### 4.1 Gmail: OAuth client and refresh token

The tool talks to the Gmail API as *you*, using an OAuth "Desktop app"
client and a refresh token for the sending account. Gmail needs the
`https://www.googleapis.com/auth/gmail.modify` scope: it sends, creates
drafts, reads the inbox and adds labels, and nothing else.

1. **Create a Google Cloud project.** Go to the Google Cloud Console
   (`console.cloud.google.com`), sign in as the account that will send, and
   create a project, e.g. `offerings-mailer`.
2. **Enable the Gmail API.** *APIs & Services → Library*, search "Gmail
   API", *Enable*.
3. **Configure the OAuth consent screen** (*APIs & Services → OAuth consent
   screen*, called *Google Auth Platform* in newer consoles):
   * *User type*: **Internal** if the sending address is in a Google
     Workspace domain you administer (no verification, no token expiry).
     Otherwise **External**.
   * App name and support email: anything, e.g. `Offerings mailer`.
   * *Scopes*: add `.../auth/gmail.modify`.
   * *Test users* (External only): add the sending address.
4. **Create the OAuth client.** *APIs & Services → Credentials → Create
   credentials → OAuth client ID*, application type **Desktop app**. Download
   the JSON and save it as `client_secret.json` **outside** the repo. It
   contains `client_id` and `client_secret`.
5. **Obtain a refresh token.** On a machine with a browser:

   ```bash
   pip install google-auth-oauthlib
   python3 - <<'EOF'
   from google_auth_oauthlib.flow import InstalledAppFlow

   SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
   flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
   creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")
   print("OFFERINGS_GMAIL_CLIENT_ID=" + creds.client_id)
   print("OFFERINGS_GMAIL_CLIENT_SECRET=" + creds.client_secret)
   print("OFFERINGS_GMAIL_REFRESH_TOKEN=" + creds.refresh_token)
   EOF
   ```

   A browser window opens. Sign in as the **sending** account. For an
   External app in testing, Google shows "Google hasn't verified this app";
   click *Continue*. Tick the Gmail permission. The three lines printed are
   your credentials; copy them into the environment file on the server.

   *Alternative without Python:* the OAuth 2.0 Playground
   (`developers.google.com/oauthplayground`). Click the gear, tick *Use your
   own OAuth credentials*, paste the client id and secret, enter the
   `gmail.modify` scope in step 1, authorize, then *Exchange authorization
   code for tokens* in step 2 and copy the refresh token. The playground
   needs `https://developers.google.com/oauthplayground` as an authorized
   redirect URI, which only a **Web application** client type allows, so
   create a second client of that type for it.
6. **Make the token permanent (External apps).** While the consent screen's
   publishing status is *Testing*, refresh tokens expire after **7 days**. On
   the consent screen (or *Audience*), click **Publish app**. Google will keep
   showing the unverified-app warning at consent time; that is fine for an app
   only you use. If the token stops working with `invalid_grant`, repeat
   step 5.

Check the setup without sending anything to buyers:

```bash
offerings --config config.json --live status
```

A credentials problem shows up here as `Gmail 401 ...` or
`Live mode requires OFFERINGS_GMAIL_CLIENT_ID, ...`.

### 4.2 Google Maps Distance Matrix API key

Freight quotes are `road miles × rate_per_mile` (+ `freight_margin_per_truckload`)
per truckload, with road miles from the Distance Matrix API.

1. In the same Cloud project, *APIs & Services → Library*, search
   "Distance Matrix API", *Enable*. The Maps Platform requires a **billing
   account** on the project; usage at this volume (one lookup per quote) is
   small, and Google credits a monthly free amount.
2. *Credentials → Create credentials → API key*. Then *Edit key →
   API restrictions → Restrict key → Distance Matrix API* so the key cannot
   be used for anything else.
3. `export OFFERINGS_GOOGLE_MAPS_API_KEY=...`

Test it (this one command calls Google even in dry run):

```bash
offerings --config config.json quote $ID --zip 73127 --truckloads 2
offerings --config config.json quote $ID --dest "Oklahoma City, OK"
```

### 4.3 Anthropic (Claude) API key, optional

Without a key the classifier uses deterministic keyword rules, which handle
unsubscribes, bounces, out-of-office, "2 truckloads to 73127" and "$0.85/sf
firm" style replies well. With a key, free-form replies ("could you do
better if we took the whole lot and picked up?") are understood far more
reliably; the rules still run first for unsubscribe/bounce/auto-reply and
remain the fallback for any API problem.

1. Sign in to the Claude Developer Platform console (`platform.claude.com`),
   open *API Keys*, create a key.
2. `export OFFERINGS_ANTHROPIC_API_KEY=...`
3. Make sure the optional dependency is installed: `pip install -e ".[llm]"`.

With `classifier: auto` (the default) Claude is used as soon as the key is
present; `classifier: rules` ignores the key; `classifier: claude` refuses
to start without it. The model defaults to `claude-opus-5-5`
(`OFFERINGS_ANTHROPIC_MODEL`). Each reply costs one short request; the email
body is passed to the model as quoted *data*, and whatever instructions a
buyer (or spammer) writes in an email are never followed (section 8).

---

## 5. Rollout ladder

Climb one rung at a time. Each rung changes exactly one setting.

| Rung | Setting                                                                                  | What goes out for real                                                     | What to check before the next rung                                                                                                                                                                                                                                                                                       |
|------|------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 0    | **Dry run** (`live: false`, the default)                                                 | Nothing. Everything lands in `outbox/`.                                    | Open every `.eml`: subject format (`$0.99/sf ... (New 10/1)` / `MAKE OFFERS: ...`), `To:` is you, BCC groups are the size you expect, signature and footer look right, `grep -il "delete red\|cost fob\|suggested sell" outbox/*.eml` finds nothing. `offerings status` shows the actions you expect.                                      |
| 1    | **Live, drafts** (`--live` or `OFFERINGS_POLICY_LIVE=true`; `auto_reply_mode: draft` is the default) | Blasts, updates and personal forwards. Firm-offer escalations and digests to you. **All buyer-facing replies and follow-ups are Gmail drafts.** | Do this first with `allowed_recipient_domains` set to **your own** domain and a buyer list of your own addresses: confirm the mail arrives, threads are labelled `Offerings/...`, drafts appear in Gmail *Drafts*. Then clear the allow-list (`OFFERINGS_POLICY_ALLOWED_RECIPIENT_DOMAINS=` or `[]`) and blast to real buyers. Review *Drafts* daily; the quotes in them are the ones you would send next rung. Compare a few against your own freight lookups and adjust `rate_per_mile` / `freight_margin_per_truckload`. |
| 2    | **Send quotes** (`auto_reply_mode: send`)                                                | Delivered-price quotes, "interested" answers and ZIP requests are sent without review. Questions, unknown replies and failed quotes are **still drafted** for you. | Read the daily digest (`offerings digest --send`). Spot-check sent quotes in *Sent*. Watch `errors=` in the run summary. Confirm no reply ever went to an unsubscribed or declined buyer (`offerings buyers list --status unsubscribed`).                                                                                                 |
| 3    | **Auto follow-ups** (`auto_send_follow_ups: true`)                                       | One nudge per silent buyer after `follow_up_after_days`, at most `max_follow_ups`. | Check the digest's `follow_ups` count against expectations, that buyers who replied or declined never get one, and that volume stays under `max_sends_per_day`. Optionally turn on `acknowledge_offers`.                                                                                                                       |

Going back down is one setting too: remove `--live` (or set
`OFFERINGS_POLICY_LIVE=false` when live is driven by the environment) and
everything is a dry run again.

---

## 6. How replies are routed

Every message in the inbox that is newer than the last poll, not from you,
and belongs to a known offering (same Gmail thread as a blast, or a subject
that matches an offering title after stripping `Re:`/`Fwd:`) is classified
once and acted on once. Unsubscribe, bounce and auto-reply checks run before
anything else, by rules, never by the model.

| The buyer wrote...                                                       | Intent                    | What happens                                                                                                                                                                                                                                                                                      |
|--------------------------------------------------------------------------|---------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| "Unsubscribe", "remove me", "take me off your list", "REMOVE", "STOP"    | `unsubscribe`             | Buyer status → `unsubscribed`. Nothing is sent, now or ever. Logged as `buyer_unsubscribed`.                                                                                                                                                                                                       |
| A bounce from `mailer-daemon` / `postmaster`                            | `bounce`                  | If a known buyer address appears in the bounce, that buyer → `bounced`. Otherwise ignored. Never answered.                                                                                                                                                                                         |
| "Automatic reply: out of the office until..."                            | `out_of_office`           | Ignored. Not counted as a reply, so the buyer still gets a follow-up later.                                                                                                                                                                                                                       |
| "We'll take 2 loads at $0.85/sf delivered to Dallas"                     | `firm_offer`              | **Escalated to `escalation_email`** with who, price, quantity, destination, a summary and the quoted original. Thread labelled `Offerings/Offer`. Never accepted or declined automatically. If `acknowledge_offers` is on, the buyer gets a short "got it, will confirm shortly" (draft or send). |
| "How cheap can you get on 2 truckloads delivered to 73127?"              | `delivered_price_request` | Destination = ZIP or city in the email, else the buyer's ZIP on file. Freight is quoted and a reply in your voice ("freight is $2,900 from Calhoun, GA to Oklahoma City, OK (644 mi) ... delivered ≈ $1.14/sf on 2 truckloads ... make a firm offer delivered to your location") is drafted or sent per `auto_reply_mode`. No destination → a reply asking for ZIP and truckload count. Quote fails (no Maps key, no route) → a draft for you plus an `error` entry, even in `send` mode. The learned ZIP is saved on the buyer. |
| "Interested, send details"                                               | `interested`              | Reply with the offering details and a request for ZIP and truckload count (draft or send).                                                                                                                                                                                                         |
| "Is this first quality? Can you send photos?" / anything else            | `question` / `unknown`    | **Draft for you** with the original quoted, thread labelled `Offerings/Needs reply`. Never sent automatically, even in `send` mode.                                                                                                                                                                 |
| "Not for us, thanks"                                                     | `not_interested`          | Buyer tagged `declined:<offering id>`; no follow-ups for this offering. Logged as `buyer_declined`.                                                                                                                                                                                                 |

Every automatic reply goes to the thread the buyer wrote in (`Re:` subject,
proper `In-Reply-To`), is counted against the send caps, and is skipped
(and logged) if the buyer is unsubscribed, bounced, paused, or outside
`allowed_recipient_domains`.

---

## 7. Running autonomously

One pass is `offerings run`: expire old offerings → send scheduled campaigns
→ read and route new replies → send due follow-ups. A pass never raises on a
single bad item; errors are collected into the summary line and the audit
log. Run it every 15 minutes.

### 7.1 Environment file

`/home/you/offerings/offerings.env`, mode `600`, `KEY=value` lines (no
`export`, so systemd can read it too):

```
OFFERINGS_GMAIL_CLIENT_ID=...
OFFERINGS_GMAIL_CLIENT_SECRET=...
OFFERINGS_GMAIL_REFRESH_TOKEN=...
OFFERINGS_GOOGLE_MAPS_API_KEY=...
OFFERINGS_ANTHROPIC_API_KEY=...
```

### 7.2 cron

```cron
# m   h    dom mon dow  command
*/15  *    *   *   *    cd /home/you/offerings && set -a && . ./offerings.env && set +a && flock -n .run.lock .venv/bin/offerings --config config.json --live run >> run.log 2>&1
45    17   *   *   1-5  cd /home/you/offerings && set -a && . ./offerings.env && set +a && .venv/bin/offerings --config config.json --live digest --since-hours 24 --send >> run.log 2>&1
```

`flock -n` skips a pass if the previous one is still running, so two passes
never share the database. The second line mails you the digest on weekday
afternoons; `--send` without `--live` would only write it to the outbox.

### 7.3 systemd timer

`/etc/systemd/system/offerings.service`:

```ini
[Unit]
Description=Email offerings: one run_once pass
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=offerings
WorkingDirectory=/opt/offerings
EnvironmentFile=/opt/offerings/offerings.env
ExecStart=/opt/offerings/.venv/bin/offerings --config /opt/offerings/config.json --live run
```

`/etc/systemd/system/offerings.timer`:

```ini
[Unit]
Description=Run email offerings every 15 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=15min
RandomizedDelaySec=60
Persistent=true

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now offerings.timer
systemctl list-timers offerings.timer
journalctl -u offerings.service -n 50
```

A oneshot service cannot overlap itself, so no lock is needed. Add a second
service/timer pair for the digest, or keep the digest in cron.

Alternatively run a single long-lived process,
`offerings --config config.json --live run --loop --interval 900`, as a
simple service with `Restart=on-failure`. Ctrl-C (SIGINT) stops the loop
cleanly after the current pass.

### 7.4 Quiet hours and daily caps

* Between `quiet_hours_start` and `quiet_hours_end` (default 20:00-07:00 in
  `timezone`) no buyer-facing mail is sent. Passes still run: they read the
  inbox, record replies, mark unsubscribes, and still escalate firm offers
  to you. Campaigns simply stay `scheduled` and go out on the first pass
  after 07:00.
* Each pass sends at most `max_sends_per_run` recipient addresses, each
  local day at most `max_sends_per_day`. A 300-buyer blast with the default
  200 per run goes out over two passes (30 minutes). When the daily cap is
  reached, the remainder waits for tomorrow; nothing is dropped.
* Keep `max_sends_per_day` below what Google allows your account per day, or
  Gmail will reject sends (`Gmail 429`) and the pass logs errors.

---

## 8. Safety model

These are the rules the code is tested against, in plain language.

1. **Nothing reaches Gmail unless you say so.** With `live: false` (the
   default) the engine uses a dry-run mailer that writes files. Live mode
   refuses to start without all three Gmail credentials.
2. **Your cost never leaks.** `cost_price`, the "Suggested Sell" note,
   `internal_notes` and the words `DELETE RED` are never rendered into a
   blast, a personal forward, a follow-up, a quote or any reply. Only the
   internal sheet (`offerings internal`, `preview --internal`) contains them,
   and it goes only to `sales_team_email`.
3. **Firm offers are never accepted.** The only thing a buyer can receive in
   response to an offer is an optional "got it, will confirm shortly". The
   offer itself goes to you.
4. **Suppressed buyers get nothing.** Unsubscribed, bounced and paused buyers
   are removed from every blast, follow-up and reply before it is built, and
   the unsubscribe check runs before any other decision about an email.
5. **Each inbound email is handled once.** Processed message ids are stored;
   a crash, a re-run or a wider `inbox_lookback_days` never answers the same
   email twice.
6. **Auto-replies and bounces never get a reply.** Those checks are plain
   rules and run before the language model sees anything.
7. **Caps apply to every send**, including automatic replies and follow-ups:
   per run, per day, quiet hours, and the domain allow-list.
8. **Email content is data, not instructions.** When Claude classifies a
   reply, the email is wrapped in `<email>` tags, the model is told to ignore
   any instructions inside it, and its answer must be one of the known
   intents; anything else falls back to the rules. A buyer cannot talk the
   system into sending, pricing or unsubscribing anyone.
9. **No secrets in the repo.** Credentials come from the environment or a
   config file outside git; `offerings --json status` and logs show them
   redacted.
10. **Your own mail is skipped.** Anything from `sender_email` in the inbox
    (your manual replies, the blast copies addressed to you) is never
    treated as a buyer reply.

Everything the engine does is written to an audit log (`offerings status`,
`offerings digest`), including the things it decided *not* to do and why.

---

## 9. Troubleshooting

| Symptom                                                                                                 | Cause and fix                                                                                                                                                                                                                                      |
|---------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `OFFERINGS_SENDER_EMAIL (or sender_email in the config file) is required.`                             | No config was found. Pass `--config config.json`, set `OFFERINGS_CONFIG`, or export `OFFERINGS_SENDER_EMAIL`.                                                                                                                                      |
| `Live mode requires OFFERINGS_GMAIL_CLIENT_ID, OFFERINGS_GMAIL_CLIENT_SECRET and OFFERINGS_GMAIL_REFRESH_TOKEN.` | You passed `--live` without the three Gmail values in the environment (cron and systemd do not read your shell profile; use the env file in section 7).                                                                                   |
| `Gmail 401: ...` or `invalid_grant`                                                                     | The refresh token is expired or revoked. Most often the OAuth app is still in *Testing* (tokens die after 7 days): publish the app and run the token snippet again (section 4.1). Also happens after a Google password reset or a revoked app permission. |
| `Gmail 403: ... insufficient ... scope`                                                                 | The token was issued without `gmail.modify`. Re-run the snippet with that scope and `prompt="consent"`.                                                                                                                                             |
| `Gmail 429: ...` or `rateLimitExceeded`                                                                  | Google's sending limits. Lower `max_sends_per_day` / `max_sends_per_run`; the pass will resume where it stopped.                                                                                                                                    |
| Quotes are always drafted, never sent, even with `auto_reply_mode: send`; errors mention `API key`     | `google_maps_api_key` is empty. Set `OFFERINGS_GOOGLE_MAPS_API_KEY`.                                                                                                                                                                               |
| `API error: REQUEST_DENIED`                                                                             | The Maps key is not allowed to call the Distance Matrix API, or billing is not enabled on the project. Check *API restrictions* on the key and *Billing*.                                                                                           |
| `No route found: ZERO_RESULTS` / `NOT_FOUND`                                                            | The destination could not be routed (typo in the ZIP, a PO box, overseas). The reply is drafted for you to finish by hand.                                                                                                                         |
| `classifier must be one of ('rules', 'claude', 'auto')` / Claude classifier refuses to start            | Fix `classifier`, or set `OFFERINGS_ANTHROPIC_API_KEY` and `pip install -e ".[llm]"`. `auto` falls back to rules silently when either is missing.                                                                                                   |
| `run` prints `sent=0` and the campaign stays `scheduled`                                                | Quiet hours (default 20:00-07:00 local), daily cap reached, or every recipient was filtered out by `allowed_recipient_domains` (look at `skipped=`). Check `offerings status`.                                                                      |
| `Outbound message needs at least one recipient.`                                                        | All recipients of a campaign were unsubscribed/bounced/paused or outside the domain allow-list.                                                                                                                                                     |
| `offerings internal ID` fails with a `sales_team_email` error                                            | Set `sales_team_email` / `OFFERINGS_SALES_TEAM_EMAIL`.                                                                                                                                                                                             |
| `buyers import` reports rejected lines                                                                   | Those rows have no valid `email` (or the header lacks an `email` column). The rejected lines are printed; fix and re-import, the rest were imported.                                                                                               |
| `Could not determine an offering title` / `No FOB location found` / `No price found` on `--sheet`        | The sheet needs a subject (first line `Subject: ...` or `--subject`), an `FOB: City, ST` marker and a price (`$0.99/sf` in the subject, or `COST FOB:` / `SELL:` in the body).                                                                        |
| A blast preview shows your cost or `DELETE RED`                                                          | The text is in `description` outside a `DELETE RED ... DELETE RED` span. Move it to `internal_notes` or wrap it.                                                                                                                                   |
| An offering silently became `expired`                                                                    | `expires_at` passed, or `created_at` is older than `offering_ttl_days`. Set an explicit `expires_at` in the JSON before re-adding, or raise `offering_ttl_days`; setting the status back to `active` alone will expire it again on the next pass.    |
| `database is locked`                                                                                     | Two passes overlapped. Use `flock -n` in cron or the systemd timer (section 7).                                                                                                                                                                     |
| Replies are not being picked up                                                                          | The reply must be in a thread the system sent or carry the offering title in the subject, and must not be from `sender_email`. The first poll only looks back `inbox_lookback_days`. Check that the Gmail account in the token is the same `sender_email`. |

The run summary line (`[LIVE] campaigns=... errors=N`) and `offerings status`
are the first places to look; `offerings digest --since-hours 24` lists
every action with its reason.

---

## 10. FAQ

**Why BCC groups of 50?** Gmail counts every address on a message against
its per-message recipient limits and treats very large BCC lists as a spam
signal, which hurts delivery for the whole account. Fifty keeps each message
well inside those limits, keeps a failed chunk small enough to resend, and
matches roughly what you did by hand (300 buyers → 6 messages). Change it
with `bcc_chunk_size`. Your own address is the `To:` on every chunk, so you
get the same copy you used to.

**Why are replies drafts by default?** Because a wrong delivered price or
an odd sentence costs more than a 30-second review. Drafts appear in Gmail
*Drafts* on the buyer's thread; you read, fix if needed, and press send.
When a week of drafts needs no edits, move to `auto_reply_mode: send`.
Questions and anything the classifier is unsure about stay drafts forever.

**How do I stop everything, right now?** Any one of these:

* Set `OFFERINGS_POLICY_LIVE=false` in the environment file and make sure
  the cron/systemd command does not pass `--live` (`--live` on the command
  line forces live on). Every pass becomes a dry run that writes to
  `outbox/`.
* `sudo systemctl stop offerings.timer` (or comment out the cron line).
* Pause one deal: `offerings --config config.json offering set-status ID paused`.
  Its scheduled campaigns are cancelled and follow-ups stop; replies that
  still come in are read, and firm offers are still escalated to you.
  `set-status ID active` resumes it (re-run `blast` if a campaign was
  cancelled).
* Pause one buyer: `offerings buyers unsubscribe buyer-one@example.com`
  (permanent, same as if they asked) or set them `paused`.

**How do I re-send an offering as a "10/1 update"?**
`offerings --config config.json blast $ID --kind update` produces the
`... - 10/1 update` subject to every active buyer. Buyers who declined or
unsubscribed are excluded automatically.

**How do I forward a deal to one buyer with a personal note?**
`offerings --config config.json blast $ID --to buyer-one@example.com --kind personal --note "I can deliver this truckload at $0.99/sf. Pretty great deal."`
The subject becomes `MARCUS>>$0.99/sf 5mm/12mil Silver Rustic Oak SPC Vinyl Click Flooring`
and the note goes first, before the description.

**What does `--now` do on `blast`?** It dispatches the campaign in the same
command instead of waiting for the next `run`. The usual caps and quiet
hours still apply.

**Will it ever accept an offer?** No. A firm offer is forwarded to
`escalation_email` and labelled in Gmail. You reply to the buyer yourself.

**What is `(AWR 10/1)` versus `(New 10/1)`?** `AWR` marks the internal cost
sheet to the sales team (`offerings internal`); `New 10/1` marks the
customer blast; `- 10/1 update` marks a re-send. The date is the day the
message goes out, written without zero padding.

**Can I see what it would do before trusting it?** Yes: that is dry run.
Every message is a readable `.eml` in `outbox/`, and `offerings status`
shows every action, including skipped recipients and why.

**Where is the data?** One SQLite file (`db_path`, default `offerings.db`)
holding offerings, buyers, campaigns, contacts, replies, processed message
ids and the audit log. Back it up like any other file; copy it to move the
system to another machine.
