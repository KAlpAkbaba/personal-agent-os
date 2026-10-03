# ADR draft (pack-question-button): the Turkish pack question gets two buttons on /core

Status: accepted (worker, cycle d20261003). The lead numbers it.
Builds on ADR-0249 ("Not done here", plan D3) and ADR-0173.

## Context

`LocalVoiceMode` asks `packQuestion` (PACK_QUESTION_TR) when the owner has set
`pagentos.core.localStt` to `acik` / `olc` and Chrome answers `downloadable`, and
`answerPackQuestion(yes)` must be called from a click: Chrome's `install()` consumes the
click's transient user activation. Nothing on /core drew the question or called the answer,
so the path was unreachable.

## Decision

1. `LocalModeViewProps` gains an OPTIONAL `onAnswerPack?: (yes: boolean) => void`; every
   earlier caller and test renders unchanged.
2. With the local switch on and `snapshot.packQuestion !== null`, `LocalModeBlock` draws the
   snapshot's own sentence (the view types no sentence) in `data-local-pack-question`, and -
   only when the handler is given - two `core-chip` buttons, `data-local-pack-answer="yes"`
   ("Evet, indir") and `"no"` ("Hayır"). With no handler the sentence is shown and no button:
   never a button that does nothing. Start/stop and the listening indicator do not move.
3. The synchronous-click rule: each `onClick` calls `onAnswerPack(true|false)` directly - no
   promise, no timer, no state update before the call - and `VoiceControl` passes
   `(yes) => answerPackQuestion(yes)`, which calls `LocalVoiceMode.answerPackQuestion`, which
   calls `install()` in the same stack.
4. The building of the `local` props moves into one exported pure function,
   `buildLocalViewProps`, in `VoiceControl.tsx`, so the wiring is testable without a DOM.

## How the test holds the rule

`tests/uistate/voice-control-pack-question.test.tsx` builds a real `LocalVoiceMode` from the
fakes (`acik`, FakeOnDevice `downloadable`), gives its snapshot to the real view through
`buildLocalViewProps`, walks the element tree, calls the yes button's `onClick`, and asserts
`installCalls == [{langs:["tr-TR"], processLocally:true}]` on the next line with no await or
tick. Mutations proven RED: the yes wrapped in `void Promise.resolve().then(...)`; both buttons
passing `true`; the builder not passing `answerPackQuestion`.

## Not done / not proven

No real Chrome has run this: whether Chrome accepts the activation from this click and how
large the pack is stay NOT_RUN. No setting is turned on; the default `kapali` never asks.
Nothing is downloaded except by the owner's own click on "Evet, indir".
