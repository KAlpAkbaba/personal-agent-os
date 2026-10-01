# ADR (unnumbered): PR-C's binding limits close at the gate (ADR-0207, PR-C 2/2)

Status: proposed by worker `webtask-prc-gate-bindings`; the lead numbers it at merge.

## Context

ADR-0207 lists, under "Recorded for PR-C, and binding on it", what may not stay open once
a model plans a browser task's steps. `webtask-model-planner` wires that model. This
closes the items that live in the Cloud Core's gate (`app/webtask/gate.py`,
`app/webtask/risk.py`). The retention of the last observation and the ceiling ON THE WIRE
for `fill` / `select_option` / `set_checked` are not here (see "Not here").

## Decisions

1. **A write is classified from its element.** `fill`, `select_option` and `set_checked`
   are judged by the element's name and `submits`, with the marker file, as a click is:
   HIGH_IMPACT name -> HIGH_IMPACT, `submits` or an external-communication name ->
   EXTERNAL_COMMUNICATION, otherwise REVERSIBLE_WRITE. The worker's `risk_hint` is never
   lowered. `classify_element` is unchanged: it is the contract's section 4 and the
   worker's rule, where a field is REVERSIBLE_WRITE because pressing it only focuses it.
   `Decision.risk_ceiling` carries the class for these actions.
2. **A click on a checkbox, radio or switch is `set_checked` by another name** and is
   classified the same way. Not on the card; found on the way: without it a planner
   reaches the wired checkbox through `click` and the rule above closes nothing.
3. **Payment is the same boundary for every action that names an element.** A payment
   marker in the name -> `ask_owner(payment)`, for click, fill, select_option and
   set_checked; a grant does not change it.
   *Where this departs from the card:* the card's examples ('Satın al' select, 'Abone ol'
   checkbox) are asked to give a HIGH_IMPACT read-back, and both names are PAYMENT
   markers. A read-back can be confirmed; ADR-0207 decision 4 says a payment is not
   performed "with a confirmation either". So they are handed over (risk HIGH_IMPACT,
   kind `payment`), and the confirmable HIGH_IMPACT read-back is shown on a non-payment
   name ('Hesabı sil'). The stricter reading; reversible by narrowing the check.
4. **An unnamed control that submits or sits in a form is EXTERNAL_COMMUNICATION**, and
   the read-back says "adsız bir düğme ... Sayfa bu düğmeye ad vermemiş". Unnamed = no
   letter and no digit after folding (an icon glyph such as "×" is not a name). Exempt:
   a plain link (`role=link` with an href - it navigates), text entry (`fill`, and a
   click that only focuses a text field: typing sends nothing and the control that sends
   is gated when pressed). An unnamed control outside any form that submits nothing
   stays REVERSIBLE_WRITE (a menu, a close box).
5. **The site-name rule reads site position only.** The label of the registrable domain
   (>= 4 letters) must be followed, on folded text, by: a locative / ablative / dative
   suffix with an optional apostrophe (`da de ta te`, the same with `ki`, `dan den tan
   ten`, `ya ye`); the one-letter dative `a` / `e` ONLY after an apostrophe ("Google'a";
   "dünya" is not "düny'a"); or the word `sitesi…` / `sayfası…`. Added to the card's
   list, as the same three cases: `tan/ten`, `-ki`, and `'a/'e` - without them
   "Facebook'tan", "YouTube'daki" and "Google'a git" would have stopped working.
6. **The grant is bound to what the control is wired to.** The read-back facts now hold
   `submits`, `in_form` and `href_host`; `facts_still_hold` compares them. The same name
   and role rewired between the read-back and the word -> the grant is spent and the
   owner hears a new read-back that begins "Onayınızdan sonra sayfa değişti; yeniden
   soruyorum." A read-back stored before these facts existed opens only a control where
   they are absent now too.
7. **The read-back says what will be done.** `fill` / `select_option` / `set_checked`
   now reach it, so it says "bir alana yazacağım" / "bir listeden seçim yapacağım" /
   "bir kutunun işaretini değiştireceğim" instead of "bir düğmeye basacağım". The value
   is never said, as it is never logged.
8. **The non-password sensitive field** needed no code: the gate already hands over on
   the worker's `sensitive` mark for all three writes. It now has its test, which runs
   the worker's own `is_sensitive` from its source on a `type=text` field named
   'Kart numarası'.

## Known limits, written down

* A bare name is not site position: "Trendyol aç" / "YouTube aç" name no site any more.
  The planner's navigation is refused (`url_not_from_owner_or_page`) and the task fails
  after three such rounds. If that phrasing matters, it needs its own rule (it cannot be
  "any word before a verb": "şu haberi aç").
* An ordinary noun in the locative is still site position: "listede" and "Adana'da"
  allow `liste.com` and `adana.com`. Navigation only; the destination policy, the
  deny-list and the per-element gate still apply.
* A control that was read back as submitting and is rewired into one the rules call
  FREE is clicked without the comparison: a free step never reaches the grant.
* The second read-back for a link whose target moved does not name the new host.
* A text field with a marker in its name ("Yanıtla", "Paylaş") is read back before it is
  typed into. Over-asking, by the card's rule (name-based, as click).
* `loop._rebind` says "sayfa değişmiş" when two unnamed controls cannot be told apart;
  the outcome (no click) is right, the sentence is not exact. `loop.py` is outside this
  task.

## Not here (for the lead at merge)

* **Next card: "contract v1.8: the ceiling on fill/select/set_checked"** - the worker
  still performs these three without a ceiling; the Decision already carries it.
* The retention rule for the last observation in `web_tasks.state_json` (PR-C list).

## Evidence

`tests/unit/test_webtask_gate.py`, `tests/unit/test_webtask_acceptance.py`:
PROVEN_AUTOMATED. Seven mutations RED, each restored from a backup copy with sha256
equal before and after.
