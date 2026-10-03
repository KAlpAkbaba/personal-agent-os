"""The watch: a public page read in the cloud, compared with the last reading, told once.

A watch is a standing subscription of its own (not a routine action): one clock
(``every_hours`` / ``next_due_at``) for one decision. ``compare`` is the pure half (hash,
tr-TR number, conditions, the edge), ``extract`` the one-field model fallback, ``reader`` the
cloud reading, ``service`` the store, ``runner`` the loops and ``routes`` ``/v1/watches``.
Only a hash and one value are ever stored - never page text.
"""
