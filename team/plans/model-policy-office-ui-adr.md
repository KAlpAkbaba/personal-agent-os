# ADR text for the lead (task model-policy-office-ui) - the model policy on the Ofis page

The page's third of the contract of ADR-0214 addendum 7 (the API's: addendum 14). The contract is unchanged.

**What the page does now.**
* *The seat's panel* has a model selector for the seat's ROLE (native `<select>`, Fable 5.1 / Opus 5.5 /
  Sonnet 5.5, the setting's model selected). Worker seats share one role and the panel says so with the number of
  worker seats sent ("Dört çalışan aynı modeli kullanır" with four, "Üç ..." with three). The owner's seat, a seat
  the page does not know and an answer without `models` (an older API) have none. "Bir sonraki koşudan itibaren
  geçerli." stands under it.
* *Choosing* sends `PUT /v1/team/queue/models` with the whole setting, the role changed. Optimistic: the choice is
  drawn at once; a refusal draws the previous setting again and shows the server's `detail.message` (role=alert).
* *The weaker-than rule in the page*: the inspector's options weaker than the worker's model and the worker's options
  stronger than the inspector's are `disabled`, with "Denetleyici işçiden zayıf modelde koşamaz"; `chooseModel`
  refuses such a choice before any PUT. The server stays the authority (`inspector_weaker_than_worker`).
* *The top bar*: "Fable: %NN" / "Tüm modeller: %NN"; a null `used_pct` is "bilinmiyor", never "%0" (a real 0 is
  "%0"); `limited` is "limitte, <local HH:MM>" (just "limitte" with no reset time); "yedek model: açık/kapalı" is a
  button with `aria-pressed` = the setting's `fallback`, which PUTs the whole setting flipped; the newest `lowered`
  entry is one line "model düşürüldü: <from> → <to>, <task title or id>".
* *A dead cycle* (`cycle.running` false): the same numbers and line, followed by "(<dd.MM HH:mm> itibarıyla)" from
  the status' `updated_at` in the muted colour - never drawn as the present (the inspector's finding 5 on
  model-policy-api).
* *A seat with `running_model`*: "şu an: <model> (düşürüldü)" on the seat (and in its aria-label) and in the panel.

**Decisions made here, each reversible.**
1. *The selector shows the SETTING's model of the role, not the seat's `model`*: after a choice the seat's `model`
   lags until the next poll; the setting is what the owner changed.
2. *The poll does not overwrite a choice in flight*: the page keeps its chosen setting until the PUT has answered
   and a poll brings a setting at least as new (`updated_at`, Z strings compare in order).
3. *The worker-seat sentence counts the seats sent* rather than saying "Üç" always: the cycle runs four or more
   worker seats; with three the card's literal text is what is shown.
4. *The fallback toggle reflects the setting's `fallback`*, not `cycle.limits.fallback` (what a running cycle is
   using): the toggle writes the setting, so it shows the setting.

**Evidence.** PROVEN_AUTOMATED: `apps/web/tests/office/models.test.tsx` (21 cases; RED 20/20 before the code), the
existing office tests green, the whole web suite 126 files / 2186 tests, tsc and oxlint clean. Mutations RED: the
weaker-than rule removed (3 cases), a null `used_pct` read as 0 (1), a dead cycle drawn as the present (1).
NOT_RUN: PROVEN_REAL (the owner changes a role's model on the page and sees the next run on it) - needs the release.

**Rollback.** Revert the commit: the page goes back to the old top bar and panel; the API's keys are ignored.
