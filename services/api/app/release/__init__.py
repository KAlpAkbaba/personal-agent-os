"""M18 owner-authorised release execution path (ADR-0055 §5, M18 spec §5).

This package is deliberately OUTSIDE ``app.evolution``: root policy
``deployment_authority`` (``app/evolution/authority.py``) says deployment
authority "lives outside the engine and is reachable only from a verified
owner session." ``app.evolution`` proposes a candidate and, through
``EvolutionService``, records the owner's authorisation; everything in THIS
package is what actually executes a release once that authorisation exists —
preflight, deploy, post-deployment verification and automatic rollback.

Nothing here mutates a real production system. Every effectful step goes
through the :class:`~app.release.backend.DeploymentBackend` seam so the whole
path is exercisable against fakes; wiring a real backend (Hetzner over
Tailscale SSH, mirroring ``scripts/cloud/release-cloud-core.ps1``) is later,
owner-gated work.
"""
