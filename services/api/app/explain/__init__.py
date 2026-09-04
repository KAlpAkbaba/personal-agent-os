"""Self Explanation Engine (M16, docs/M16_ACTIVITY_LEDGER_SPEC.md §2).

Answers questions about what the system itself did — "Son yaptıklarını anlat", "Ne
başarısız oldu?", "Araştırma motoru ne durumda?", "Kanıtı ne?" — from durable evidence
first, then words. Every statement in a briefing is typed: a ``known_fact`` points at
the evidence it rests on, an ``inference`` says it is derived, an ``uncertainty`` says the
evidence is missing. Nothing here is answered from model memory, and nothing here can
start, deploy or change anything: the engine is read-only over the ledger.
"""
