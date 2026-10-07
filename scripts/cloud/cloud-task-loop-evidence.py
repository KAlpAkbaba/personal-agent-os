#!/usr/bin/env python3
"""The cloud browser task loop's evidence: T1, T2 and T4 on a dev or staging api (card
cloud-task-loop-evidence).

    python scripts/cloud/cloud-task-loop-evidence.py --api-url http://127.0.0.1:28001 \
        --token-file <file holding an owner session token> [--host-kind staging] \
        [--out-dir docs/evidence]

Runs the three tasks against the api's cloud worker (``target_word`` "bulutta"), reads each
task to its end and writes ``cloud-task-loop-t1-t2-t4.json`` and ``.md``. T2 adds the test
form's site to THAT api's allow-list, runs, removes it and runs again (the gate's refusal);
the site is removed whatever happens. NEVER production: an address that says ``prod`` or is
the owner's Cloud Core is refused with exit 2 before anything is sent.

The writer half (``task_record``, ``evidence_document``, ``write_evidence``) is imported by
``services/api/tests/integration/test_webtask_cloud_companion.py``: one schema, not two
copies. Standard library only.

No owner mail, no personal data, no token or key goes into the files: the writer looks for
token-shaped and mail-shaped text in what it is about to write and refuses to write it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

SCHEMA = "cloud-task-loop-evidence/1"
JSON_NAME = "cloud-task-loop-t1-t2-t4.json"
MD_NAME = "cloud-task-loop-t1-t2-t4.md"
REPO_ROOT = Path(__file__).resolve().parents[2]

#: T1's news site: public, Turkish, not on the task deny-list.
NEWS_HOST = "www.trthaber.com"
#: T2's form: a public test form that exists to be filled (the PR-B fixture site lives on
#: loopback, and the cloud worker refuses every private destination by design).
FORM_URL = "https://httpbin.org/forms/post"
FORM_SITE = "httpbin.org"
#: The fields T2 names; each must be filled AND verified (the element's name contains it).
FORM_FIELDS = ("Customer name", "Telephone")

#: The owner's Cloud Core (tailnet name and address). With "prod" anywhere, refused.
PRODUCTION_HOSTS = ("100.90.158.26",)
PRODUCTION_HOST_PREFIXES = ("pagentos-core",)


@dataclass(frozen=True, slots=True)
class TaskSpec:
    key: str
    goal: str
    target_word: str = "bulutta"
    allowed_hosts: tuple[str, ...] = ()


TASKS: dict[str, TaskSpec] = {
    "T1": TaskSpec(
        "T1",
        "bugünkü yapay zeka haberlerinden birini bul ve özetle",
        allowed_hosts=(NEWS_HOST,),
    ),
    "T2": TaskSpec(
        "T2",
        f"{FORM_URL} adresindeki formu doldur ama gönderme: Customer name alanına "
        "Deneme Kisi, Telephone alanına 02120000000 yaz.",
    ),
    "T4": TaskSpec("T4", "YouTube'da Barış Manço - Dönence aç"),
}

OUTCOME_DONE = "done"
OUTCOME_ASK_OWNER = "ask_owner"
OUTCOME_FAILED = "failed"
OUTCOME_CANCELLED = "cancelled"
_OUTCOMES = {
    "done": OUTCOME_DONE,
    "waiting_owner": OUTCOME_ASK_OWNER,
    "failed": OUTCOME_FAILED,
    "cancelled": OUTCOME_CANCELLED,
}
SETTLED = ("done", "failed", "cancelled", "waiting_owner")
BOT_WALL_PAGE_KINDS = ("captcha", "blocked")
BOT_WALL_ASK = "challenge"

#: USD per million tokens (input, output), by model family; list prices as written
#: 2026-10-07. An ESTIMATE, labelled as one in the files.
PRICES_PER_MTOK = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (5.0, 25.0)}
#: Without the planner's own log (a remote api), a model call is assumed this size.
ASSUMED_TOKENS_PER_CALL = (4000, 250)
ASSUMED_MODEL_FAMILY = "haiku"

OWNER_TRIAL = (
    "READY_FOR_OWNER (cloud-task-loop-voice kartı ve yayın sonrası): söyleyeceğin cümle "
    '"Bulutta bugünkü yapay zeka haberlerinden birini bul ve özetle"; makine: herhangi biri '
    '(iş bulutta çalışır); göreceğin: üç cümlelik Türkçe özet ve kaynağı. Sonra "bulutta şu '
    "formu doldur ama gönderme\" için ÖNCE Onay Merkezi'nden formun sitesini izin listesine "
    "ekle; liste dışındaki sitede bulut yazmaz, Türkçe söyler."
)


# ------------------------------------------------------------------ production refusal


def refuse_production(url: str) -> str | None:
    """Why ``url`` must not be used, or None. Dev and staging only."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return f"bir http(s) adresi değil: {url!r}"
    host = parsed.hostname.lower()
    if "prod" in url.lower():
        return "adreste 'prod' geçiyor"
    if host in PRODUCTION_HOSTS or host.startswith(PRODUCTION_HOST_PREFIXES):
        return "sahibin Cloud Core adresi"
    return None


# ------------------------------------------------------------------ the api


class ApiError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


def _call(
    base_url: str, method: str, path: str, body: Any = None, token: str = "", timeout: float = 30
) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310 - dev/staging only, checked by the caller
        base_url.rstrip("/") + path, data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode("utf-8", "replace")) from None
    return json.loads(raw) if raw else {}


def bootstrap_owner(base_url: str) -> str:
    """A fresh api's one-time owner credential (loopback only)."""
    return str(_call(base_url, "POST", "/v1/identity/bootstrap")["owner_credential"])


def open_session(base_url: str, credential: str, *, label: str) -> str:
    body = {"owner_credential": credential, "client_kind": "cli", "label": label}
    return str(_call(base_url, "POST", "/v1/identity/sessions", body)["token"])


class ApiClient:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token

    def call(self, method: str, path: str, body: Any = None) -> Any:
        return _call(self.base_url, method, path, body, self._token)

    def devices(self) -> list[dict[str, Any]]:
        return list(self.call("GET", "/v1/devices").get("devices") or [])

    def enrollment_token(self) -> str:
        return str(self.call("POST", "/v1/devices/enrollment-tokens")["token"])

    def revoke_device(self, device_id: str) -> None:
        self.call("POST", f"/v1/devices/{device_id}/revoke")

    def close_session(self) -> None:
        self.call("DELETE", "/v1/identity/sessions/current")

    def start_task(self, spec: TaskSpec) -> dict[str, Any]:
        body = {
            "goal": spec.goal,
            "target_word": spec.target_word,
            "allowed_hosts": list(spec.allowed_hosts),
        }
        return dict(self.call("POST", "/v1/web-tasks", body))

    def get_task(self, task_id: str) -> dict[str, Any]:
        return dict(self.call("GET", f"/v1/web-tasks/{task_id}"))

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        return dict(self.call("POST", f"/v1/web-tasks/{task_id}/cancel"))

    def allowlist_add(self, site: str) -> None:
        self.call("POST", "/v1/team/allowlist", {"site": site})

    def allowlist_remove(self, site: str) -> None:
        try:
            self.call("DELETE", f"/v1/team/allowlist/{quote(site)}")
        except ApiError as exc:
            if exc.status != 404:  # already gone is the goal
                raise

    def command(
        self, device_id: str, capability: str, payload: dict[str, Any], *, timeout_s: float = 60
    ) -> dict[str, Any]:
        created = self.call(
            "POST",
            f"/v1/devices/{device_id}/commands",
            {"capability": capability, "payload": payload, "timeout_s": timeout_s},
        )
        command_id = created["command_id"]
        deadline = time.monotonic() + timeout_s + 15
        while time.monotonic() < deadline:
            answer = self.call("GET", f"/v1/devices/{device_id}/commands/{command_id}")
            if answer.get("status") in ("succeeded", "failed", "cancelled", "expired"):
                return dict(answer)
            time.sleep(1.0)
        return {"status": "timeout"}


def wait_until_settled(read: Any, task_id: str, *, timeout_s: float = 720, poll_s: float = 2.0):
    """The task as it is once it is no longer ``running`` (a hang guard at ``timeout_s``)."""
    deadline = time.monotonic() + timeout_s
    task: dict[str, Any] = {}
    while time.monotonic() < deadline:
        task = read(task_id)
        if task.get("status") in SETTLED:
            return task
        time.sleep(poll_s)
    raise TimeoutError(f"task {task_id} still {task.get('status')!r} after {timeout_s:.0f}s")


def close_task(client: ApiClient, task_id: str) -> None:
    """Cancel a task that is still live, and wait for it to end. Best effort."""
    try:
        task = client.get_task(task_id)
        if task.get("status") in ("running", "waiting_owner"):
            client.cancel_task(task_id)
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                if client.get_task(task_id).get("status") in ("done", "failed", "cancelled"):
                    return
                time.sleep(1.0)
    except ApiError:
        pass


# ------------------------------------------------------------------ T4: is it playing?

_PLAYER_TIME = re.compile(r"(?<!\d)(\d{1,2}):(\d{2})\s*/\s*(\d{1,2}):(\d{2})(?!\d)")


def player_seconds(observation: dict[str, Any]) -> int | None:
    """The player's "elapsed / length" clock, in seconds, from an observation's text or its
    element names; None when the page shows no such clock."""
    names = " ".join(str(e.get("name") or "") for e in observation.get("elements") or [])
    for text in (str(observation.get("text") or ""), names):
        match = _PLAYER_TIME.search(text)
        if match:
            return int(match.group(1)) * 60 + int(match.group(2))
    return None


def measure_playback(
    client: ApiClient, device_id: str, task_id: str, *, gap_s: float = 8.0
) -> dict[str, Any]:
    """Two observations of the task's own session ``gap_s`` apart: did the clock move?
    (``browser.media_status`` is a media-session operation, and the cloud worker opens only
    its research profile, so the clock is read from the page the task left.)"""
    session = {"session_id": f"webtask-{task_id}", "scope": "page"}
    readings: list[int | None] = []
    errors: list[str] = []
    for index in range(2):
        if index:
            time.sleep(gap_s)
        answer = client.command(device_id, "browser.observe", session)
        if answer.get("status") != "succeeded":
            errors.append(str((answer.get("error") or {}).get("class") or answer.get("status")))
            readings.append(None)
            continue
        readings.append(player_seconds(answer.get("result") or {}))
    first, second = readings
    return {
        "method": "browser.observe x2 on the task's session (player clock text)",
        "gap_s": gap_s,
        "first_s": first,
        "second_s": second,
        "advanced": None if first is None or second is None else second > first,
        "errors": errors,
    }


# ------------------------------------------------------------------ the record


def usage_from_log_lines(lines: list[str]) -> list[dict[str, Any]]:
    """The planner's ``webtask_planner_answered`` lines (model, tokens) from a JSON log."""
    usage = []
    for line in lines:
        if "webtask_planner_answered" not in line:
            continue
        try:
            entry = json.loads(line[line.index("{") :])
        except ValueError:
            continue
        if entry.get("event") != "webtask_planner_answered":
            continue
        usage.append(
            {
                "model": str(entry.get("model") or ""),
                "input_tokens": int(entry.get("input_tokens") or 0),
                "output_tokens": int(entry.get("output_tokens") or 0),
            }
        )
    return usage


def _price(model: str) -> tuple[float, float] | None:
    for family, price in PRICES_PER_MTOK.items():
        if family in model.lower():
            return price
    return None


def estimate_usd(usage: list[dict[str, Any]], model_calls: int) -> tuple[float | None, str]:
    """(USD, basis). Measured tokens when the planner's log was read, else an assumed size."""
    if usage:
        total = 0.0
        for call in usage:
            price = _price(call["model"])
            if price is None:
                return None, f"measured_tokens; unknown price for {call['model']!r}"
            total += (call["input_tokens"] * price[0] + call["output_tokens"] * price[1]) / 1e6
        return round(total, 5), "measured_tokens x list price (estimate)"
    if model_calls == 0:
        return 0.0, "no model call"
    price = PRICES_PER_MTOK[ASSUMED_MODEL_FAMILY]
    per_call = (ASSUMED_TOKENS_PER_CALL[0] * price[0] + ASSUMED_TOKENS_PER_CALL[1] * price[1]) / 1e6
    return round(per_call * model_calls, 5), (
        f"assumed {ASSUMED_TOKENS_PER_CALL[0]}+{ASSUMED_TOKENS_PER_CALL[1]} tokens per call "
        f"x {ASSUMED_MODEL_FAMILY} list price (estimate)"
    )


def _short(text: str, limit: int = 600) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def site_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    labels = [x for x in host.split(".") if x]
    if len(labels) <= 2:
        return host
    if labels[-1] in ("tr", "uk") and labels[-2] in ("com", "org", "net", "gov", "edu", "co"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def is_article_url(url: str) -> bool:
    """A page below the site's front page (a story, not the home page)."""
    path = urlparse(url).path.strip("/")
    return bool(path) and "/" in path or path.endswith(".html")


def task_record(
    *,
    spec: TaskSpec,
    task: dict[str, Any],
    planner_calls: int,
    planner_model_calls: int,
    usage: list[dict[str, Any]],
    observation: dict[str, Any] | None,
    **extra: Any,
) -> dict[str, Any]:
    """One task's line in the evidence. ``observation`` is the task's last page when the
    caller can read the row (the test), None from a remote api (the script)."""
    status = str(task.get("status") or "")
    outcome = _OUTCOMES.get(status, status)
    observation = observation or {}
    page_kind = str(observation.get("page_kind") or "")
    waiting_for = str(task.get("waiting_for") or "")
    usd, basis = estimate_usd(usage, planner_model_calls)
    rounds = list(task.get("rounds") or [])
    return {
        "task": spec.key,
        "goal": spec.goal,
        "target_word": spec.target_word,
        "allowed_hosts": list(spec.allowed_hosts),
        "target": str(task.get("target") or ""),
        "device_platform": "cloud",
        "attended": task.get("attended"),
        "status": status,
        "outcome": outcome,
        "failure": str(task.get("failure") or ""),
        "ask_owner_reason": f"{waiting_for}: {_short(task.get('message') or '', 300)}"
        if outcome == OUTCOME_ASK_OWNER
        else "",
        "bot_wall": waiting_for == BOT_WALL_ASK or page_kind in BOT_WALL_PAGE_KINDS,
        "message": _short(task.get("message") or ""),
        "rounds": len(rounds),
        "round_index": int(task.get("round_index") or 0),
        "planner_calls": planner_calls,
        "planner_model_calls": planner_model_calls,
        "model_usage": {
            "calls_logged": len(usage),
            "input_tokens": sum(u["input_tokens"] for u in usage),
            "output_tokens": sum(u["output_tokens"] for u in usage),
            "models": sorted({u["model"] for u in usage}),
        },
        "cost_usd_estimate": usd,
        "cost_basis": basis,
        "last_observation_url": str(observation.get("url") or ""),
        "last_site": rounds[-1].get("site", "") if rounds else "",
        "last_page_kind": page_kind,
        "submit_seen": any(e.get("submits") for e in observation.get("elements") or []),
        "trail": [
            {
                k: r.get(k)
                for k in (
                    "index",
                    "site",
                    "action",
                    "element",
                    "outcome",
                    "detail",
                    "verified",
                    "planner",
                )
            }
            for r in rounds
        ],
        **extra,
    }


def record_from_api(spec: TaskSpec, task: dict[str, Any], **extra: Any) -> dict[str, Any]:
    """The script's record: the planner's counts from the trail (rules cost nothing)."""
    planners = [str(r.get("planner") or "") for r in task.get("rounds") or []]
    return task_record(
        spec=spec,
        task=task,
        planner_calls=sum(1 for p in planners if p and p != "confirmed"),
        planner_model_calls=sum(1 for p in planners if p == "model"),
        usage=[],
        observation=None,
        **extra,
    )


def evidence_document(
    *,
    host_kind: str,
    records: list[dict[str, Any]],
    companion: dict[str, Any],
    cleanup: dict[str, Any],
) -> dict[str, Any]:
    costs = [r["cost_usd_estimate"] for r in records if r.get("cost_usd_estimate") is not None]
    return {
        "schema": SCHEMA,
        "written_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "host_kind": host_kind,
        "evidence_class": "PROVEN_PROXY",
        "companion": companion,
        "tasks": records,
        "total_model_calls": sum(int(r.get("planner_model_calls") or 0) for r in records),
        "total_cost_usd_estimate": round(sum(costs), 5),
        "cleanup": cleanup,
        "owner_trial": OWNER_TRIAL,
    }


# ------------------------------------------------------------------ the files

_SECRET_SHAPES = (
    re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_TOKEN_RUN = re.compile(r"[A-Za-z0-9_-]{32,}")
_MAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}")
_MAIL_OK = ("@example.com", "@example.org")


def find_secrets(text: str) -> list[str]:
    """Token-shaped or mail-shaped text, abbreviated; [] when the text is clean. A slug
    (lower-case words joined by hyphens) is not token-shaped; a mixed-case run with digits is."""
    found = [m.group(0)[:12] + "…" for p in _SECRET_SHAPES for m in p.finditer(text)]
    for match in _TOKEN_RUN.finditer(text):
        run = match.group(0)
        if re.search(r"\d", run) and re.search(r"[A-Z]", run) and re.search(r"[a-z]", run):
            found.append(run[:8] + "…")
    for match in _MAIL.finditer(text):
        if not match.group(0).lower().endswith(_MAIL_OK):
            found.append(match.group(0).split("@")[0][:3] + "…@…")
    return found


def _cell(value: Any) -> str:
    return str(value if value not in (None, "") else "-").replace("|", " / ").replace("\n", " ")


def render_markdown(doc: dict[str, Any]) -> str:
    lines = [
        "# Bulutta tarayıcı görev döngüsü: T1, T2, T4 kanıtı",
        "",
        f"- Yazıldı: {doc['written_at']}",
        f"- Host: {doc['host_kind']}",
        f"- Kanıt sınıfı: {doc['evidence_class']} (gerçek bulut yardımcısı, gerçek planlayıcı "
        "model, gerçek kamu siteleri; PROVEN_REAL yalnız sahibin denemesinden)",
        f"- Toplam model çağrısı: {doc['total_model_calls']}; tahmini toplam maliyet: "
        f"{doc['total_cost_usd_estimate']} USD",
        "",
        "| Görev | Hedef | Sonuç | Tur | Model çağrısı | Tahmini USD | ask_owner nedeni | "
        "Son gözlem |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in doc["tasks"]:
        allow = r.get("allow_listed")
        name = r["task"] + ("" if allow is None else (" (listede)" if allow else " (liste dışı)"))
        result = r["outcome"] + (f" / {r['failure']}" if r.get("failure") else "")
        lines.append(
            "| "
            + " | ".join(
                _cell(v)
                for v in (
                    name,
                    r["target"],
                    result,
                    r["rounds"],
                    f"{r['planner_model_calls']} / {r['planner_calls']}",
                    r["cost_usd_estimate"],
                    r["ask_owner_reason"],
                    r["last_observation_url"] or r["last_site"],
                )
            )
            + " |"
        )
    lines += ["", "Model çağrısı sütunu: modelin yanıtladığı / planlayıcıya sorulan tur.", ""]
    lines += ["## Görev ayrıntıları", ""]
    for r in doc["tasks"]:
        lines.append(f"### {r['task']}: {r['goal']}")
        lines.append("")
        lines.append(f"- Sonuç: {r['outcome']}; sahip başında: {r.get('attended')}")
        if r.get("message"):
            lines.append(f"- Son söz: {r['message']}")
        if r.get("owner_left_at_round") is not None:
            lines.append(
                f"- Sahip oturumu {r['owner_left_at_round']}. turda kapatıldı; görev "
                f"{r['round_index']}. tura kadar sürdü."
            )
        if r.get("playback"):
            lines.append(f"- Oynatma ölçümü: {json.dumps(r['playback'], ensure_ascii=False)}")
        usage = r.get("model_usage") or {}
        lines.append(
            f"- Tokenlar: {usage.get('input_tokens', 0)} girdi / {usage.get('output_tokens', 0)} "
            f"çıktı; modeller: {', '.join(usage.get('models') or []) or '-'}; maliyet tabanı: "
            f"{r['cost_basis']}"
        )
        lines.append(
            "- İz: "
            + "; ".join(
                f"{t['index']}.{t['action']} {_cell(t['element'])[:40]} -> {t['outcome']}"
                + (f" ({t['detail']})" if t.get("detail") else "")
                for t in r["trail"]
            )
        )
        lines.append("")
    walls = [r["task"] for r in doc["tasks"] if r.get("bot_wall")]
    lines += [
        "## Bot duvarı",
        "",
        ("Bot duvarı / captcha görülen görevler: " + ", ".join(walls) + ". Bunlar başarı sayılmaz.")
        if walls
        else "Bu koşuda hiçbir görev bot duvarına ya da captcha'ya çarpmadı.",
        "",
        "IP notu: bu koşunun yardımcısı " + doc["host_kind"] + " üzerinde çalıştı. Veri "
        "merkezi IP'sinin (Cloud Core) duvarı ancak yardımcı Cloud Core'da koşunca ölçülür; "
        "o ölçüm sahibin deneme satırına bağlıdır.",
        "",
        "## Temizlik",
        "",
        "```",
        json.dumps(doc["cleanup"], ensure_ascii=False),
        "```",
        "",
        "## Sahibin deneme satırı",
        "",
        doc["owner_trial"],
        "",
    ]
    return "\n".join(lines)


def write_evidence(doc: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    """Both files, or neither: a secret- or mail-shaped value refuses the write."""
    body = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    md = render_markdown(doc)
    leaks = find_secrets(body) + find_secrets(md)
    if leaks:
        raise ValueError(f"evidence refused: token- or mail-shaped text {leaks}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = out_dir / JSON_NAME, out_dir / MD_NAME
    json_path.write_bytes(body.encode("utf-8"))
    md_path.write_bytes(md.encode("utf-8"))
    return json_path, md_path


# ------------------------------------------------------------------ the run


def _cloud_device(client: ApiClient) -> str:
    for device in client.devices():
        if device.get("platform") == "cloud" and device.get("status") == "online":
            return str(device["device_id"])
    raise SystemExit("bu api'de çevrimiçi bir bulut cihazı yok (platform 'cloud')")


def run(client: ApiClient, host_kind: str, out_dir: Path) -> tuple[Path, Path]:
    device_id = _cloud_device(client)
    records: list[dict[str, Any]] = []
    removed = False

    def one(spec: TaskSpec, **extra: Any) -> dict[str, Any]:
        started = client.start_task(spec)
        task_id = str(started["task_id"])
        try:
            task = wait_until_settled(client.get_task, task_id)
            if spec.key == "T4" and task.get("status") == "done":
                extra["playback"] = measure_playback(client, device_id, task_id)
        finally:
            close_task(client, task_id)
        records.append(record_from_api(spec, task, **extra))
        return task

    one(TASKS["T1"])
    client.allowlist_add(FORM_SITE)
    try:
        one(TASKS["T2"], allow_listed=True)
    finally:
        client.allowlist_remove(FORM_SITE)
        removed = True
    one(TASKS["T2"], allow_listed=False)
    one(TASKS["T4"])
    sites = [s["site"] for s in client.call("GET", "/v1/team/allowlist").get("sites") or []]
    cleanup = {"form_site_removed": removed, "test_sites_left_on_list": sites.count(FORM_SITE)}
    doc = evidence_document(
        host_kind=host_kind,
        records=records,
        companion={"mode": "the api's own cloud worker", "device_id": device_id},
        cleanup=cleanup,
    )
    return write_evidence(doc, out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api-url", required=True)
    parser.add_argument("--token-file", required=True, type=Path)
    parser.add_argument("--host-kind", default="dev", choices=("dev", "staging"))
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / "docs" / "evidence")
    args = parser.parse_args(argv)
    # The Windows console's code page would mangle the Turkish sentences below.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    why = refuse_production(args.api_url)
    if why:
        sys.stderr.write(f"üretim adresi reddedildi ({why}): bu betik yalnız dev/staging'e koşar\n")
        return 2
    token = args.token_file.read_text(encoding="utf-8").strip()
    try:
        paths = run(ApiClient(args.api_url, token), args.host_kind, args.out_dir)
    except ApiError as exc:
        sys.stderr.write(f"api isteği reddedildi: {exc}\n")
        return 1
    print("\n".join(str(p) for p in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
