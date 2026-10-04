# ADR (office-progress): the Ofis's İlerleme strip is read from the roadmap, never typed in

**Status:** proposed by the worker, 2026-10-04 (cycle d20261003). The lead numbers it.

## Context

The owner, 2026-10-03: "Ben ajanları görüyorum ama şu anda roadmap'e göre projenin ortalama yüzde
kaçı tamamlandı, yüzde kaçı kaldı göremiyorum." The Danışman counted it by hand that evening.

## Decision

1. `services/api/app/team/progress.py` parses two documents of the tree the process serves:
   `docs/ROADMAP.md` (the JARVIS table and "The order") and
   `docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md` (every table row with a numeric ID).
   `GET /v1/team/office` carries the result as the additive field `progress`
   (`{jarvis, order, v1, rule, as_of}`); `as_of` is `settings.release` (the exported release sha)
   or null. The root is `app.state.progress_root`, default the repository root.
2. Weights: JARVIS HAVE 1, PARTIAL 0.5, MISSING 0; the NEVER / HARDWARE row is excluded from the
   denominator. The order: done 1, partial 0.5, open 0. v1.0: IMPL `DONE` = done, PROOF `PR` =
   proven in reality. A row whose state cannot be read is `unknown`, named in `jarvis.unknown`,
   and counted as NOT done (an empty IMPL is `unknown` in `by_status`). The rule is sent as text.
3. Rounding: nearest whole percent, an exact half rounds DOWN (62.5 -> 62): progress is never
   rounded up from a tie.
4. A missing file or a missing section makes that part `null`; the page shows "okunamadı".
5. The page: `OfficeProgress.tsx` under the top bar, a native `<details>` (click opens, no
   state, works without JS), three thin `role="meter"` bars, the panel lists the JARVIS rows by
   state (Var / Yarım / Yok / Okunamadı / Hedef değil) and the order's next open step. Styled
   inline in the office palette (office.css was not in the card's area).
6. Deviation from the card: there is no `officeProgress.ts`. On a case-insensitive disk it and
   `OfficeProgress.tsx` are the same import (`./OfficeProgress` resolved to the .ts on Windows and
   the component was `undefined`); the model lives in `OfficeProgress.tsx` as `buildProgress`.

## Proposed ROADMAP marker (for the Proje Yöneticisi to apply at merge)

A step of "### The order" may begin with `**DONE**` or `**PARTIAL**` right after its number; no
marker means open. The parser reads the marker first; without one it falls back to the word DONE
in the step's own line (step 1 today). The seven lines, the rest of each line unchanged:

```
1. **DONE** **Memory** — DONE 2026-09-29: PR-1 in production (ADR-0200), PR-2 automated
2. **PARTIAL** **browser-use, anywhere** — the JARVIS that does anything on the web:
3. **Secretary** — Radicale (own calendar/contacts), a mail account, then the telephony
4. **The house** — Home Assistant as the `smart_home` provider; "salonun ışığını kapat".
5. **Everywhere** — reopened: the office PC is the second device; next: session→device
6. **Voice and character** — close the Turkish TTS gap, then give the persona its wit.
7. **Sight** — gesture stage 2 merged after the owner's trial; AR as a later surface.
```

With the markers the order reads (1 + 0.5) / 7 = 21 %; without them 1 / 7 = 14 % (what the page
shows until they are applied). The card's "Sıralı plan %25" was an illustration, not a count.

## Consequences / open

- **Production (the inspector's return of 2026-10-03):** the api image (context `services/api`)
  does not ship `docs/`. Decision: do NOT copy the documents into the image (a docs edit would then
  need an image rebuild, and the build context would have to widen to the repository); instead the
  root is `PAGENTOS_PROGRESS_ROOT` when set (`routes.py`, done in this change), and the production
  compose mounts exactly the two files, read-only, from the release checkout the stack is built
  from - so the strip reads the same release's documents. Proposed text for
  `infra/docker/docker-compose.prod.yml` (outside this card's area; the lead applies it), in the
  `api: &cloud-core` service - `api-blue` / `api-green` inherit both through `<<:`:

  ```yaml
      environment: &cloud-core-env
        # office-progress: where the Ofis's İlerleme strip reads the roadmap and the v1.0 list.
        PAGENTOS_PROGRESS_ROOT: /srv/pagentos/progress
      volumes:
        # office-progress: the two documents the strip counts, read-only, from the release checkout.
        - ../../docs/ROADMAP.md:/srv/pagentos/progress/docs/ROADMAP.md:ro
        - ../../docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md:/srv/pagentos/progress/docs/product/PERSONALAGENTOS_V1_FEATURE_MATRIX.md:ro
  ```

  Applied in this change (return 2, the compose file was added to the area);
  `test_the_production_api_mounts_both_documents_read_only_under_that_root` guards it. A file bind mount follows the checkout's inode: the release script must not replace the
  files by rename after the container starts, or the container keeps the old copy until restart
  (a blue/green release restarts the colour anyway).

- **Recovery bundle (the inspector's return 1, 2026-10-04):** this change edits
  `infra/docker/docker-compose.prod.yml`, so the release that ships it is marked
  `RECOVERY_BUNDLE_STALE` by `scripts/cloud/release-cloud-core-bluegreen.sh` (the compose-change
  check). Until the bundle is refreshed the recovery timer reconciles against the previous tree's
  inputs, and the NEXT release that changes compose again is refused with exit 83. Required step
  after this release: re-run `install-recovery-supervisor.sh <the full 40-hex release sha>` on the
  Cloud Core. The Danışman (Proje Yöneticisi) does it - it is within its authority; it is NOT an
  owner step and does not go into the owner's batch.

- The strip changes with the documents: a release that edits the table changes the number.
