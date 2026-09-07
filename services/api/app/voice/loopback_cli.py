"""``python -m app.voice.loopback_cli``: the TTS -> STT loopback proxy run, as a process.

    --cases <json>        a corpus report (``{"results": [...]}``, tests/voice_corpus) or a
                          plain list of ``{case_id, expected_speech|speech, category}``
    --provider openai|fake
    --out <json>          the evidence file (app.voice.loopback.LoopbackReport.to_dict)
    --max-cases N         the budget (default 40; real providers cost money)
    --per-category N      at most N cases from one category (default: balanced by budget)
    --seed N              the sampler's seed (default app.voice.loopback.DEFAULT_SEED)

The OpenAI key is read from the environment (``PAGENTOS_VOICE_OPENAI_API_KEY``, the same
variable the Cloud Core's settings use) and is never printed, logged or written. Exit
codes: 0 ran (whatever the verdicts - the report carries them), 2 bad input, 3 no key
for a real provider, 4 the run itself failed before a report could be written.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from app.voice import loopback
from app.voice.providers import (
    FakeSTTProvider,
    FakeTTSProvider,
    OpenAISTTProvider,
    OpenAITTSProvider,
)

PROVIDER_OPENAI = "openai"
PROVIDER_FAKE = "fake"
KEY_ENV = "PAGENTOS_VOICE_OPENAI_API_KEY"

EXIT_OK = 0
EXIT_BAD_INPUT = 2
EXIT_NO_KEY = 3
EXIT_FAILED = 4


def load_cases(path: Path) -> list[loopback.LoopbackCase]:
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("results"), list):
        return loopback.cases_from_corpus_results(raw["results"])
    if isinstance(raw, dict) and isinstance(raw.get("cases"), list):
        raw = raw["cases"]
    if not isinstance(raw, list):
        raise ValueError("cases file must be a corpus report or a list of cases")
    cases = [loopback.case_from_dict(row) for row in raw if isinstance(row, dict)]
    return [c for c in cases if c is not None]


def build_providers(provider: str) -> tuple[Any, Any]:
    if provider == PROVIDER_FAKE:
        return FakeTTSProvider(), FakeSTTProvider()
    if provider == PROVIDER_OPENAI:
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            raise PermissionError(f"{KEY_ENV} is not set; the OpenAI providers need it")
        return (
            OpenAITTSProvider(key, model="tts-1", timeout_s=60.0),
            OpenAISTTProvider(key, model="whisper-1", timeout_s=120.0),
        )
    raise ValueError(f"unknown provider {provider!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.voice.loopback_cli",
        description="TTS -> STT loopback proxy qualification (VOICE_SPEC §7a).",
    )
    parser.add_argument("--cases", required=True, help="corpus report JSON or a case list JSON")
    parser.add_argument(
        "--provider", choices=(PROVIDER_OPENAI, PROVIDER_FAKE), default=PROVIDER_FAKE
    )
    parser.add_argument("--out", required=True, help="where to write the report JSON")
    parser.add_argument("--max-cases", type=int, default=40)
    parser.add_argument("--per-category", type=int, default=None)
    parser.add_argument("--seed", type=int, default=loopback.DEFAULT_SEED)
    parser.add_argument("--language", default="tr-TR")
    parser.add_argument("--voice", default="default")
    parser.add_argument(
        "--no-sample",
        action="store_true",
        help="run the cases in file order (still capped by --max-cases) instead of sampling",
    )
    return parser.parse_args(argv)


def summary_line(report: dict[str, Any]) -> str:
    t = report["totals"]
    audio = report["audio"]
    marks = report["marks"]
    return (
        f"{report['suite']}: {t['cases']} cases, {t['matched']} matched, {t['degraded']} "
        f"degraded, {t['mismatched']} mismatched, {t['error']} error; mean WER "
        f"{report['mean_wer']}; audio {audio['cases_with_audio']} files, "
        f"{audio['total_ms']} ms total, mean {audio['mean_ms']} ms; providers "
        f"{report['providers']['tts']} -> {report['providers']['stt']}; "
        f"audio_generation={marks['audio_generation']['mark']} "
        f"loopback_semantics={marks['loopback_semantics']['mark']} "
        f"physical_hearing={marks['physical_hearing']['mark']} -> {report['summary']}"
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    cases_path = Path(args.cases)
    out_path = Path(args.out)
    try:
        available = load_cases(cases_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"loopback: cannot read cases from {cases_path}: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT
    if not available:
        print(f"loopback: no speakable case in {cases_path}", file=sys.stderr)
        return EXIT_BAD_INPUT

    if args.no_sample:
        selected = list(available)[: max(0, args.max_cases)]
    else:
        selected = loopback.sample_balanced(
            available, max_cases=args.max_cases, per_category=args.per_category, seed=args.seed
        )

    try:
        tts, stt = build_providers(args.provider)
    except PermissionError as exc:
        print(f"loopback: {exc}", file=sys.stderr)
        return EXIT_NO_KEY

    notes = [
        f"{len(available)} speakable cases available, {len(selected)} selected "
        + ("in file order" if args.no_sample else f"by the balanced sampler (seed {args.seed})")
    ]
    try:
        report = loopback.run_loopback(
            selected,
            tts,
            stt,
            language=args.language,
            max_cases=args.max_cases,
            voice=args.voice,
            seed=None if args.no_sample else args.seed,
            notes=notes,
        )
        payload = report.to_dict()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 - the exit code and one line, never a key
        print(f"loopback: run failed: {type(exc).__name__}: {exc}"[:400], file=sys.stderr)
        return EXIT_FAILED
    print(summary_line(payload))
    print(f"report: {out_path}")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - the process entry
    sys.exit(main())
