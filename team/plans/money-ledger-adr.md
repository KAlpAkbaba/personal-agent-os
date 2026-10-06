# ADR (draft, money-ledger): JARVIS's own money ledger - bank notification mails, spends heard in a conversation, never the bank

Status: proposed (worker, cycle d20261006). The lead numbers it and moves it into docs/DECISIONS.md.

## Context

The owner (2026-10-05, Order step 5 "Knows his money"): JARVIS should know his balance and his
spends without ever touching the bank. Hard limits from the card: no money movement, no bank
password, no internet-banking scraping, and a test that no bank host is contacted.

## Decision

1. **The ledger is JARVIS's own rows** (`app/money`, migration `0071_money_ledger`, five tables:
   `money_entries`, `money_balances`, `money_bank_notices`, `money_questions`, `money_scans`).
   Amounts are integer **kuruş** (BIGINT): a float would make a bank mail's 0,30 differ from a
   spoken 0,30. No column holds a password, a card number or an account number.
2. **Balance and bank transactions come only from the bank's notification mails already in
   `mail_index`** (sender, subject, the first 200 characters of the body - what the mail poll
   indexes). `MailPoller(after_poll=...)` hands each successful poll to `ledger.ingest_mail`; a
   failing hook never fails the poll. A mail counts only when the sender's domain IS a known
   bank's domain or a subdomain of it (look-alikes refused), and its words state a movement in
   the past tense or a balance (advertising states neither). Per bank a small parser (domains,
   display name, own extra patterns - Enpara's "...TL'lik alışveriş") over shared Turkish
   patterns ("1.234,56 TL", "işyerinde" / "İşyeri:", "bakiye"; "limit" is never the balance).
   Each mail is read once (`money_bank_notices.ref` unique). The balance is kept per bank with
   the MAIL's time and said with it ("Yapı Kredi hesabınızda 2.500 TL (bugün 14:00 postasına
   göre)"); an older mail never overwrites a newer balance.
3. **A spend from a conversation** (`conversations/spend.py`, pure): a price asked, ONE amount
   said, and the OWNER's clear acceptance ("tamam alayım", "olur veriyorum", "alıyorum",
   "anlaştık") -> booked at once as TENTATIVE by `money/loop.py` (every 20 s; unique per
   conversation + line) and said in one notification line with "geri al". Not sure - no
   acceptance of his, two prices, an acceptance when no line is known to be the owner's, no
   amount - nothing is booked; a `money_questions` row is asked **90 s after the conversation
   ENDS** ("750 liralık bir harcama yaptınız mı?"), never while it goes on, never twice.
   Answers: "evet" (asked amount), "evet ama 750" (his amount), "hayır" (nothing). "Nakit"
   makes it cash.
4. **Never counted twice.** A bank card-spend mail first looks for a spend JARVIS holds with the
   same amount to the kuruş, not cancelled, not cash, not yet bank-confirmed, heard between 12 h
   before and 30 min after the mail; the closest wins and the mail's reference is written ONTO
   it (`bank_ref` unique in the database). Only when none matches is the mail a new row. Cash
   is never matched.
5. **Voice**: six intents/tools - `money.balance`, `money.spent` (queries, "bu ay markete ne
   harcadım"), `money.spend_yes`, `money.spend_no`, `money.undo`, `money.cash` (actions).
   A bare "evet" / "hayır" is money only while his question is open (process-local note in
   `money/pending.py`, 10 min) and nothing else waits for a yes; a bare "geri al" only right
   after a booking and nothing in focus. "Evet harcadım", "harcamayı geri al" need no state.
   Argument keys `amount` / `category` (no text/audio/token). Web: `/money`.

## Consequences

- Read-only by construction: `app/money`, `conversations/spend.py` and `tools_money.py` import
  no network client (AST test) and a full pass with socket connect/getaddrinfo patched to fail
  opens nothing (test).
- Only the first 200 characters of a bank mail are seen (the index's snippet): a bank that puts
  the amount further down is "unparsed" until the snippet grows (mail service is another card).
- Bank mail formats are the shapes Turkish banks use in common, not verified against each bank's
  live mails: the owner trial is the proof (below). A new format is a parser line + a test row.
- A spoofed mail from a bank's real domain would be believed (no DKIM check here); it can only
  write a ledger row the owner can see and take back - it moves nothing.
- Needs outside this card's area (ALAN_ISTEGI in the report): `security/step_up.py` tiers
  (balance/spent OPEN; yes/no/undo/cash SENSITIVE), `voice/capabilities.py` family name "money",
  the tool-set contract in `test_voice_realtime_sessions.py`, and `realtime_sessions/service.py`
  copying `money_amount_kurus` / `money_category` onto the turn record (local mode: without it
  "evet ama 750" books the asked amount and "markete nakit verdim" falls to the model's argument).
- Migration written on `0070_household_stock`; conversation-followups carries a 0071 in
  parallel - the merge re-chains one of them (integration step / Danışman).

## Owner trial (PROVEN_REAL, READY_FOR_OWNER)

With "evde dinle" on: ask a price, hear it, say "tamam alayım", pay by card. Expect within 20 s a
notification "750 TL harcamayı deftere geçici olarak yazdım"; when the bank's mail arrives and the
mail poll runs, `/money` shows the row "kesin" with the bank's name - one row, not two.
