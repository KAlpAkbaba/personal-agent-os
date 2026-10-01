# ADR (unnumbered): Store/UWP windows are known by the app's own names

Context: owner trial 2026-09-30 (MAIL): `app.launch calc` succeeded, the foreground window was
`applicationframehost.exe` titled "Hesap Makinesi", and `open_application` failed
`postcondition_failed` (only `systemsettings.exe` was in `UWP_HOSTED_IMAGES`, B39 req 123).

Decision: when the foreground window's image is `applicationframehost.exe` and the launched
image is not in `UWP_HOSTED_IMAGES`, the postcondition passes iff the title equals (casefolded)
the `name_tr` or an alias of the allowlisted application whose image is the launched image -
read from `operator-allowlists.json` through `allowlists.APPLICATIONS`, no second list.
Settings keeps its exact tuple; classic apps still need their own image; an unrelated frame-host
title fails.

Consequence: a Store app not in the allowlist cannot be launched anyway (app.launch allowlist).
A frame window titled with the app's name but belonging to a different document of the same app
also passes - same as the exact-image path.
