"""Voice subsystem (M4): provider independence half.

STT, realtime dialogue and long-form TTS are separate subsystems (constitution
Voice rule). This package holds the provider Protocols + deterministic fakes +
real adapter skeletons, the benchmark harness, the capability-aware fall-back
router, speaker verification, voice preferences and the barge-in state machine.
"""
