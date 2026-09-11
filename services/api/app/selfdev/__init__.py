"""Self-development: a defect in, a verified candidate on its own branch out - then a stop.

See ``app.selfdev.engine`` for the whole path and ADR-0124 for why it is shaped this way.
Runs on the development machine that holds the git repository, never inside a production
container (the constitution: self-improvement is never "the model edits production source
and restarts").
"""
