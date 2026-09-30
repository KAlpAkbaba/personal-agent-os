# verify-device-service.ps1 on MAIL — 2026-09-30, after the reinstall from main `aa35fcf3`

Run by the owner (elevated install with `-DisplayPower -Operator`, then the unelevated verifier);
the output pasted to the lead verbatim. Every checked criterion PROVEN_REAL. Kept here as the
evidence behind the QUALIFICATION rows that cite it.

| Id | Criterion | Status | Evidence (abridged) |
|---|---|---|---|
| 2.1 | Service installed and running as LocalSystem | PROVEN_REAL | state=Running account=LocalSystem start=Auto pid=44364 |
| 1.11 | Service process is in Session 0 | PROVEN_REAL | session=0 |
| 2.2 | Companion runs in the owner's interactive session | PROVEN_REAL | session=1 user=MAIL\alpak, `…\agent\companion\PagentOS.SessionCompanion.exe` |
| 2.2b | audit dir writable by owner | PROVEN_REAL | the owner SID holds Modify on `C:\ProgramData\PagentOS\companion\audit`, inherited by the trail; the directory is owned by Administrators (ADR-0211) |
| 1.1 | Pipe DACL names the owner account explicitly | PROVEN_REAL | effective SDDL read from the live pipe handle, audited 2026-09-30T20:03:01Z |
| 1.4-1.7 | Companion admitted on kernel-sourced identity | PROVEN_REAL | owner SID, session=1, pid=11904 |
| 1.17 | Only SYSTEM and Administrators can write to the install tree | PROVEN_REAL | 201 objects checked; no other principal holds write; no empty DACLs |
| 1.7b | The pinned companion binary is admin-protected and readable by SYSTEM | PROVEN_REAL | owner=Administrators, 3 ACEs |
| 5.2 | No inbound listener owned by the agent | PROVEN_REAL | pids 44364, 11904: no listening TCP socket |
| 6b.1 | Browser worker starts as the owner from the installed tree | PROVEN_REAL | self-check exit 0 as MAIL\alpak; worker 0.5.0, chrome 154.0.8037.58 available, 30 capabilities |
| 6b.2 | Advertised capability manifest agrees with the worker | PROVEN_REAL | 30 `browser.*` operations plus `browser.chrome` (`browser.observe` among them, contract v1.6/1.7); device-service 0.6.0, manifest 8baba0648e3a; 106 capabilities advertised in all |
| 6b.3 | The worker executes the installed release from the installed venv | PROVEN_REAL | package digest e2ab31edd74a == installed source; site-packages copy byte-identical |
| 6b.4 | The companion's live worker is that release | PROVEN_REAL | pid 38860 started 2026-09-30T20:03:02Z, worker 0.5.0 from the installed venv (the DateTime overflow of the old 6b.4 is gone - ADR-0211) |

Server side, read by the lead afterwards: the device row `MAIL` (`3f60fdb5-5022-48cf-bb3c-d7192466b701`)
re-registered with 106 capabilities, `software_version 0.6.0`; one new broker session after the
install; production `aa35fcf3`, health `ok`.

What this does NOT prove: any voice or browser behaviour. The owner's real-device trials
(the four sentences of `team/reports/bootstrap-2026-09-30.md`) are still his to run.
