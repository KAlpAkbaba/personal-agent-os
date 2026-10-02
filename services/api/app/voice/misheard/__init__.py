"""The misheard notebook ("Yanlış anlaşılanlar defteri"): the sentence the recogniser WROTE
when the system did not understand it - text only, 30 days, the owner's alone.

``models`` is the table, ``service`` the one writer and the ways a row leaves, ``routes`` the
owner's four calls under ``/v1/voice/misheard``. Nothing here listens to the relay: a sentence
reaches ``service.record`` only where a caller hands it one.
"""
