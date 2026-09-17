# VomeSync monetisation

What is built, what was promised, and the order to add Stripe without turning the public directory into a slot machine.

British English. No marketing voice. Do not promise a paid feature the code does not yet enforce.

## What exists today

| Piece | Where | Status |
|---|---|---|
| Free cap: 5 private + 10 public (15 total) | Server `checkFreeTierLimits` | Enforced on create and when publicize actually changes |
| Premium cap: 50 switches, 25 public | Same, after `owner_tier:*` | Enforced |

| How you become premium | Stripe Checkout, promo code, or admin grant | **Sold.** Hosted subscription Checkout + webhook. HA options flow **Upgrade to premium** |
| “Pay to subscribe to more than a few” | HA `FREE_TIER_MAX_SUBSCRIPTIONS = 10` | Options flow **and** `subscribe_to_switch`. Skipped when `POST /v2/owner/tier` says premium. The server still does not count watchers per install. |
| Paid promotion | Stripe Checkout + `promotedUntil` | **Sold.** Hosted Checkout, Promoted badge, directory sort |
| Directory ranking | `GET /public-switches` | Promoted listings first, then the rest. Organic catalogue is not sold. |
| Stripe | Same live account as vome.io | Hosting plans on vome.io; VomeSync promotion + premium on sync.vome.io. Separate webhook URL. **VAT:** Stripe Tax already registered; Checkout sends `automatic_tax`. |
| Manage billing | Stripe Customer Portal | Website **Manage billing** + HA **More → Manage billing**. Needs a paid Checkout customer, not a promo grant. |
| Owner “shop” | Public `link` field | A URL on the card. No payment, no webhook, no commission |

Catalogue and public holidays stay free to watch. Directory views are not charged.

The public directory is one Ed25519 seed. That owner is exempt from create/publicize caps (`CATALOGUE_OWNER_ID`). Paying customers stay on 50 switches / 25 public. Watching a catalogue UID is free and does not use these limits.

## Three different products

Do not mash them into one Checkout button. They have different merchants of record, fraud shapes, and Connect needs.

### 1. VomeSync premium (subscriber pays us)

Lift **create / publicize / subscribe** caps.

- **Checkout Sessions** (`mode: subscription`, €9 / month including VAT unless `STRIPE_PRICE_PREMIUM` is set). Metadata: `kind=vomesync_premium`, `ownerId`. Also on `subscription_data.metadata`.
- Website: `POST /v2/switch/:uid/premium` with a metadata access key.
- Home Assistant: signed `POST /v2/owner/premium` from **Upgrade to premium** in the options menu.
- Webhook `checkout.session.completed` → `setOwnerTier(ownerId, 'premium')`. `customer.subscription.deleted` / `updated` (canceled, unpaid, incomplete_expired) clears it unless a time-limited promo remains. `past_due` keeps premium for Smart Retries.
- HA options flow calls `POST /v2/owner/tier` and skips the 10-watch cap when `tier === 'premium'`. The subscribe service enforces the same cap.
- Server still cannot honestly cap “subscriptions” until HA registers a watcher list. Until then, premium is “we trust the client on watches + we raise the create/publicize caps which we do enforce.”
- **VAT:** same Stripe account as vome.io already has Tax (Sweden small seller, inclusive, SaaS personal `txcd_10103000`). Checkout must send `automatic_tax` + `tax_id_collection` or it silently collects 0. Dashboard Prices need `tax_behavior=inclusive` and Products that tax code.
- **Cancel / change:** Stripe Customer Portal (`POST /v2/switch/:uid/billing-portal` or signed `POST /v2/owner/billing-portal`). Promo grants have no `stripeCustomerId`.

No Connect. We are the merchant.

### 2. Paid promotion (owner pays us)

Featured placement on sync.vome.io. This is advertising, not a marketplace.

- One-time Checkout Session (`mode: payment`). Metadata: `kind=vomesync_promote`, `uid`, `ownerId`.
- Redis: `promotedUntil` (ms) on the switch hash. `GET /public-switches` sorts promoted first. Cards get a **Promoted** badge. Staff catalogue is not sold; it stays in the organic list unless an owner pays.
- Do not sell “ON” or fake user counts. Do not hide unpromoted switches.
- Refuse promotion of Test-category / empty-name listings (same debris rules as `purge-debris`).
- Webhook: `POST /api/stripe/webhook` with a raw body, *before* `express.json`.
- Default Price: €5 / 7 days (`STRIPE_PROMOTE_AMOUNT=500`) unless `STRIPE_PRICE_PROMOTE` is set.

No Connect. We are the merchant.

### 3. Owner earnings (visitor pays, owner is paid)

“Pay per click / toggle / view, we take commission, or they DIY.”

**Views:** do not charge the public to look at a card. It fights the directory, is easy to inflate, and is a poor ad metric on a 25-row page.

**Toggles / copy-UID / subscribe:** only makes sense for a *commercial* switch (tip jar, radio request, shop-window lamp). Community event switches (bridges, elections, holidays) must stay free to watch and free to automate — a paid toggle would break every subscriber’s automation.

Split this into two rails:

#### 3a. DIY hooks (no money through us)

Ship first if anyone actually asks to monetise a switch.

- Keep using `link` for a Stripe Payment Link / Ko-fi / whatever they already have.
- Optional `commerce.webhookUrl` + HMAC secret: we POST `{ uid, event: 'toggle'|'view'|'subscribe', ts, sig }` so they can invoice in their own stack (HA automation, n8n, their Stripe).
- Optional card copy: `commerce.note` (“£1 via the link if you want to buy a song”). Never imply we processed the payment.
- Optional `commerce.requireAccessKeyToToggle`: public listing is watch-only; toggle needs a key they sold themselves.

We take **no** commission because we touch **no** funds. That is the honest DIY path.

#### 3b. Commission (money through us)

Only after 3a has real users. This is a **marketplace**: we collect, we keep a platform fee, we pay the owner.

Stripe setup (Accounts v2, not legacy Express/Custom types):

- Connected accounts with Express dashboard, platform-owned pricing and negative-balance liability, **destination charges**.
- Checkout on sync.vome.io (“tip this switch” / “unlock toggle for 24h”).
- `application_fee_amount` = our commission **plus** enough to cover Stripe processing, otherwise destination charges eat the margin ([stripe.com/pricing](https://stripe.com/pricing)).
- Embedded `account_onboarding` + `notification_banner` so owners can get paid without a full Stripe dashboard build.
- KYC: a hobbyist flashing a porch light will often fail or abandon onboarding. That is why DIY comes first.

Do not use Connect for (1) or (2). Mixing SaaS subscriptions with destination charges on the same session is a mess; keep products on separate Checkout Sessions.

## Recommended order

1. **Paid promotion** — Checkout + `promotedUntil` + sort + badge. **Done.**
2. **Paid premium** — Checkout + `owner_tier` + HA **Upgrade to premium** + Customer Portal. **Done.** VAT is collected on Checkout via Stripe Tax on the existing vome.io registration.
3. **DIY commerce fields** — webhooks + copy. Zero payouts, zero KYC.
4. **Connect commission** — only if DIY owners say they want us to collect for them.

Crypto (Base/Solana) stays out until there is a reason. Promo codes remain for comps and the catalogue owner.

## What we will not sell

- Pay-per-view of the public directory.
- Promoting or gating the staff catalogue.
- Charging to subscribe to Tower Bridge / elections / holidays.
- A “boost user count” metric. User counts stay from authenticated use.
