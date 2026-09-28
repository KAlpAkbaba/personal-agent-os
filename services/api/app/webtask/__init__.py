"""The browser task loop (ADR-0207): a goal, carried out on the web, in the owner's Chrome.

``goal -> OBSERVE -> PLAN -> GATE -> ACT -> VERIFY -> next round | done | ask_owner``.

The pieces, each a module that can be read and tested by itself:

* ``types``     - the vocabulary: an observation, a step, an expectation, a round.
* ``risk``      - what acting on an element WOULD BE, from the element, by the contract's
                  rule and the shared markers file. Never from the model.
* ``sites``     - the site a page belongs to, and the deny-list no task acts on.
* ``planner``   - the ONE place a model may be asked anything: one step, flat.
* ``gate``      - allow, ask the owner, hand over, refuse. Pure.
* ``verify``    - whether the step's expectation holds in a NEW observation.
* ``loop``      - one round, over ports. Budgets and loop detection live here.
* ``service``   - the row is the truth; the loop's state is written after every round.
* ``workflow`` / ``activities`` - one durable activity per round (Temporal).

Nothing in this package is reachable by the owner yet: the intent and the shell are PR-D,
the real planner models and the owner's Chrome are PR-C.
"""
