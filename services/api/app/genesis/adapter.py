"""The HTTP adapter generator (M24_CAPABILITY_GENESIS_SPEC.md §3, ADR-0087).

``AdapterSpec`` names ONE operation of an already-parsed, already-bounded
``InterfaceDescription`` (``app.genesis.interface``) to build a capability for
— ``<name>.<operation>``, version ``0.1.0`` by default. ``HttpAdapterGenerator``
is a second implementation of the SAME ``SkillGenerator`` seam
(``app.evolution.skills.SkillGenerator``) beside ``DeterministicSkillGenerator``:
it renders the identical §10 layout (``manifest.yaml``, ``README.md``,
``src/<skill>.py``, ``tests/test_<skill>.py``, ``evals/eval_<skill>.py``,
``evals/cases.json``) so the REST of the M7 pipeline — sandbox, evaluator,
independent reviewer, shadow/canary runners, registry — needs no changes to
build, test, review and roll this out exactly like a pure-transform skill.

Generality (spec §3's own prohibition on a fixture-keyed shortcut): every
value that reaches ``src/<skill>.py`` comes from the PARSED
``InterfaceDescription`` — never from a literal naming any ONE test
application — and is re-validated at the splice point with the exact same
regexes ``app.genesis.interface`` used at the choke point (belt AND braces,
ADR-0024's own lesson). ``test_genesis_no_shortcut_guard.py`` asserts this
module's own source carries none of any fixture application's own
names/paths/operation ids.

``run(payload)`` performs exactly ONE ``urllib.request`` call: no query
string, no template beyond the operation's own path, a 5 s timeout, no
redirects, JSON in and out. ``BASE_URL`` is a single literal baked into the
generated module by ``repr()`` — never read from an environment variable at
runtime (the M7 deny-by-default gate treats ``os.environ`` itself as a
secret-scoped construct; a genesis adapter needs no secret grant, so it never
touches ``os.environ`` at all) — which also means the ONLY host the generated
process can ever reach is decided once, at generation time, from the already
loopback-validated ``base_url`` (spec §9: "the only reachable network from an
adapter: its one loopback base URL").

Because the target application is STATEFUL (a real counter, a real lamp), the
rendered tests/evals check response SHAPE (declared field names + Python
types) rather than a hardcoded expected value — the same response replayed by
the builder's own evaluation, the independent reviewer's re-run, and the
shadow/canary rollout would otherwise go stale after the very first mutating
call. The end-to-end proof that a mutation REALLY happened is a stronger,
single, ordered check the caller makes once (``app.genesis.service``'s
``used``/``verified`` states), never something the repeatable eval loop can
be trusted to assert on a moving target.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.evolution.errors import EvolutionError, EvolutionErrorClass
from app.evolution.manifest import default_manifest
from app.evolution.skills import (
    CASES_FILENAME,
    EVALS_DIRNAME,
    GENERATED_HEALTH_METRICS,
    TESTS_DIRNAME,
    SkillLayout,
    dump_manifest_yaml,
)
from app.evolution.tokens import (
    require_capability_id,
    require_identifier,
    require_summary,
    require_version,
)
from app.genesis.interface import (
    ALLOWED_FIELD_TYPES,
    NAME_RE,
    OPERATION_ID_RE,
    InterfaceDescription,
    Operation,
)
from app.logging import get_logger

logger = get_logger("app.genesis.adapter")

DEFAULT_VERSION = "0.1.0"
DISPATCH_TIMEOUT_S = 5.0
MAX_RESPONSE_BYTES = 64 * 1024


def _require_name_token(value: str, *, field: str) -> str:
    if not NAME_RE.match(value):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, f"{field} is not a valid interface name"
        )
    return value


def _require_operation_id(value: str, *, field: str) -> str:
    if not OPERATION_ID_RE.match(value):
        raise EvolutionError(
            EvolutionErrorClass.VALIDATION_ERROR, f"{field} is not a valid operation id"
        )
    return value


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    """An ``InterfaceDescription`` + the ONE operation this generation targets.

    ``capability_id`` is always ``<interface.name>.<operation_id>`` (spec §3:
    "one capability per operation") — computed, never accepted from a caller,
    so it can never disagree with what was actually researched.
    """

    interface: InterfaceDescription
    operation_id: str
    version: str = DEFAULT_VERSION
    authorized_asset: str | None = None

    def __post_init__(self) -> None:
        _require_name_token(self.interface.name, field="interface.name")
        _require_operation_id(self.operation_id, field="operation_id")
        require_version(self.version)
        if self.interface.operation(self.operation_id) is None:
            raise EvolutionError(
                EvolutionErrorClass.VALIDATION_ERROR,
                f"interface {self.interface.name!r} declares no operation {self.operation_id!r}",
            )

    @property
    def operation(self) -> Operation:
        op = self.interface.operation(self.operation_id)
        assert op is not None  # __post_init__ guarantees this
        return op

    @property
    def capability_id(self) -> str:
        return require_capability_id(f"{self.interface.name}.{self.operation_id}")

    @property
    def skill_name(self) -> str:
        return require_identifier(f"{self.interface.name}_{self.operation_id}", field="skill_name")

    @property
    def host(self) -> str:
        hostname = urlsplit(self.interface.base_url).hostname
        assert hostname is not None  # InterfaceDescription.parse guarantees this
        return hostname

    def capability_manifest(self) -> dict[str, Any]:
        op = self.operation
        input_schema = op.input_schema.to_manifest_schema()
        output_schema = op.output_schema.to_manifest_schema()
        side_effect_class = "read" if op.side_effect == "read" else "mutate_external"
        authority_class = (
            "read_only"
            if op.side_effect == "read"
            else ("mutating_authorized_asset" if self.authorized_asset else "mutating_unauthorized")
        )
        read_back = self.interface.evidence["read_back"]
        postcondition = (
            "the read_back operation's response conforms to its declared output_schema"
            if op.side_effect == "read"
            else (
                f"after {op.id}, the {read_back} read_back operation's response conforms "
                "to its declared output_schema"
            )
        )
        manifest = default_manifest(
            self.capability_id,
            self.version,
            purpose=require_summary(
                f"generated HTTP adapter for {op.method} {op.path} on "
                f"{self.interface.name} (M24 Capability Genesis)"
            ),
            inputs=list(op.input_schema.field_names()),
            outputs=list(op.output_schema.field_names()),
            status="experimental",
            builder_kind="generated",
            builder_name="http_adapter",
            health_metrics=list(GENERATED_HEALTH_METRICS),
            input_types={f.name: f.type for f in op.input_schema.fields},
            output_types={f.name: f.type for f in op.output_schema.fields},
            owner_scope="normal",
            skill=self.skill_name,
            entrypoint="run",
            network_permissions=[self.host],
            external_services=[self.interface.name],
            side_effects=["network_egress"],
            risk_class="moderate",
            authority_class=authority_class,
            side_effect_class=side_effect_class,
            evidence_contract={"read_back": read_back, "postcondition": postcondition},
            rollback_semantics=(
                "not_applicable" if op.side_effect == "read" else "none_irreversible"
            ),
            # ``creation_reason.authorized_asset`` is ALWAYS the interface's own
            # name, never conditional on ``self.authorized_asset`` (the SEPARATE,
            # higher-bar mutation authority — see ``authority_class`` above): the
            # owner's request caused THIS bounded interface to be researched, and
            # that act is what justifies the one narrow network_permissions grant
            # (exactly this loopback host) an independent reviewer will check
            # against a provider scoped to precisely that grant
            # (``app.genesis.service`` constructs it as a
            # ``StaticAuthorizationProvider`` keyed on the interface name) — never
            # a blanket network permission, and never a stand-in for whether a
            # MUTATING operation may actually run (that is ``authority_class`` /
            # ``CapabilityDispatcher``'s hard rule, checked independently).
            creation_reason={
                "trigger": "capability_gap",
                "authorized_asset": self.interface.name,
            },
            input_schema=input_schema,
            output_schema=output_schema,
            tests={
                "unit": f"{TESTS_DIRNAME}/test_{self.skill_name}.py",
                "evals": f"{EVALS_DIRNAME}/eval_{self.skill_name}.py",
                "case_count": 1,
            },
        )
        return manifest


class HttpAdapterGenerator:
    """Generic ``SkillGenerator``: any ``AdapterSpec`` within the §2 subset.

    B36 (req 577): with ``model`` set, the rendered adapter module is handed to the
    model as a starting point and the model's proposal REPLACES that one file - the
    tests, the evals and the manifest stay rendered, and the proposal is judged by
    them and by the security gate exactly as the rendered module would be.
    ``model_generated`` says, per call, which happened.
    """

    name = "http_adapter"
    MODEL_NAME = "http_adapter+model"

    def __init__(self, model: Any | None = None) -> None:
        self.model = model
        self.model_generated = False
        self.generator_name = self.name

    def generate(self, spec: AdapterSpec, workspace: Path) -> SkillLayout:
        if not isinstance(spec, AdapterSpec):
            raise EvolutionError(
                EvolutionErrorClass.GENERATION_FAILED,
                "HttpAdapterGenerator requires a parsed AdapterSpec",
            )
        skill_name = require_identifier(spec.skill_name, field="skill_name")
        version = require_version(spec.version)
        root = Path(workspace) / skill_name / version
        layout = SkillLayout(
            root=root,
            skill_name=skill_name,
            capability_id=require_capability_id(spec.capability_id),
            version=version,
        )
        for directory in (layout.root, layout.src_dir, layout.tests_dir, layout.evals_dir):
            directory.mkdir(parents=True, exist_ok=True)
        manifest = spec.capability_manifest()
        layout.manifest_path.write_text(dump_manifest_yaml(manifest), encoding="utf-8")
        layout.readme_path.write_text(self._render_readme(spec), encoding="utf-8")
        rendered = self._render_src(spec)
        self.model_generated = False
        self.generator_name = self.name
        if self.model is not None:
            proposed = self.model.propose_adapter(spec, rendered)
            if not isinstance(proposed, str) or not proposed.strip():
                raise EvolutionError(
                    EvolutionErrorClass.GENERATION_FAILED, "the model proposed no module"
                )
            rendered = proposed
            self.model_generated = True
            self.generator_name = self.MODEL_NAME
        layout.module_path.write_text(rendered, encoding="utf-8")
        layout.test_path.write_text(self._render_tests(spec), encoding="utf-8")
        layout.eval_path.write_text(self._render_evals(spec), encoding="utf-8")
        layout.cases_path.write_text(self._render_cases(spec), encoding="utf-8")
        logger.info(
            "genesis_adapter_generated",
            capability_id=layout.capability_id,
            version=version,
            operation=spec.operation_id,
        )
        return layout

    # ------------------------------------------------------------- renderers

    def _render_readme(self, spec: AdapterSpec) -> str:
        op = spec.operation
        capability_id = require_capability_id(spec.capability_id)
        version = require_version(spec.version)
        return (
            f"# {require_identifier(spec.skill_name, field='skill_name')}\n\n"
            f"Generated HTTP adapter for capability `{capability_id}` v{version}.\n\n"
            f"`{op.method} {op.path}` on `{spec.interface.base_url}` "
            f"({'mutating' if op.side_effect == 'mutate' else 'read-only'}"
            f"{', idempotent' if op.idempotent else ''}).\n\n"
            "## Contract\n\n"
            "- entrypoint: `run(payload: dict) -> dict`\n"
            f"- input fields: `{sorted(op.input_schema.field_names())}`\n"
            f"- output fields: `{sorted(op.output_schema.field_names())}`\n\n"
            "Generated by the Capability Genesis pipeline (M24); do not hand-edit.\n"
        )

    def _render_src(self, spec: AdapterSpec) -> str:
        op = spec.operation
        capability_id = require_capability_id(spec.capability_id)
        # Splice-point re-validation (ADR-0024/0025 §3): every token below is
        # re-checked immediately before interpolation, never trusted from the
        # AdapterSpec alone even though __post_init__ already validated it.
        base_url = spec.interface.base_url
        method = op.method
        if method not in ("GET", "POST"):
            raise EvolutionError(EvolutionErrorClass.GENERATION_FAILED, "unsupported method")
        path = op.path
        if not path.startswith("/"):
            raise EvolutionError(EvolutionErrorClass.GENERATION_FAILED, "unsupported path")
        input_fields = {f.name: f.type for f in op.input_schema.fields}
        input_required = list(op.input_schema.required)
        output_fields = {f.name: f.type for f in op.output_schema.fields}
        output_required = list(op.output_schema.required)
        for name, kind in {**input_fields, **output_fields}.items():
            require_identifier(name, field="schema field name")
            if kind not in ALLOWED_FIELD_TYPES:
                raise EvolutionError(EvolutionErrorClass.GENERATION_FAILED, "unsupported type")
        return (
            f'"""Generated HTTP adapter for capability {capability_id}.\n\n'
            f"{op.method} {path} on {spec.interface.name}. Deterministic apart from the\n"
            "ONE declared network call; standard library only, offline otherwise.\n"
            'Generated by the Capability Genesis pipeline (M24); do not hand-edit.\n"""\n\n'
            "from __future__ import annotations\n\n"
            "import json\n"
            "import sys\n"
            "import urllib.error\n"
            "import urllib.request\n\n\n"
            # Every value below is a literal produced by repr()/json.dumps() from an
            # already-validated token or scalar — never interpolated as raw text
            # (the M23 review lesson: free text never lands in generated source
            # except through repr()/json.dumps()).
            f"BASE_URL = {base_url!r}\n"
            f"METHOD = {method!r}\n"
            f"PATH = {path!r}\n"
            f"TIMEOUT_S = {DISPATCH_TIMEOUT_S!r}\n"
            f"MAX_RESPONSE_BYTES = {MAX_RESPONSE_BYTES!r}\n\n"
            f"INPUT_FIELDS = {json.dumps(input_fields, sort_keys=True)}\n"
            f"INPUT_REQUIRED = {json.dumps(sorted(input_required))}\n"
            f"OUTPUT_FIELDS = {json.dumps(output_fields, sort_keys=True)}\n"
            f"OUTPUT_REQUIRED = {json.dumps(sorted(output_required))}\n\n\n"
            "class AdapterError(Exception):\n"
            '    """A taxonomy error: .error_class is one of validation_error,\n'
            '    dependency_unavailable, postcondition_failed."""\n\n'
            "    def __init__(self, error_class, message):\n"
            "        super().__init__(message)\n"
            "        self.error_class = error_class\n\n\n"
            "def _check_type(value, kind):\n"
            '    if kind == "integer":\n'
            "        return isinstance(value, int) and not isinstance(value, bool)\n"
            '    if kind == "number":\n'
            "        return isinstance(value, (int, float)) and not isinstance(value, bool)\n"
            '    if kind == "boolean":\n'
            "        return isinstance(value, bool)\n"
            '    if kind == "string":\n'
            "        return isinstance(value, str)\n"
            "    return False\n\n\n"
            "def _validate(payload, fields, required, label):\n"
            "    if not isinstance(payload, dict):\n"
            '        raise AdapterError("validation_error", label + " must be an object")\n'
            "    unknown = sorted(set(payload) - set(fields))\n"
            "    if unknown:\n"
            "        raise AdapterError(\n"
            '            "validation_error",\n'
            '            label + " carries unknown fields: " + ", ".join(unknown),\n'
            "        )\n"
            "    for name in required:\n"
            "        if name not in payload:\n"
            "            raise AdapterError(\n"
            '                "validation_error", label + " is missing required field: " + name\n'
            "            )\n"
            "    for name, value in payload.items():\n"
            "        if not _check_type(value, fields[name]):\n"
            "            raise AdapterError(\n"
            '                "validation_error", label + " field has the wrong type: " + name\n'
            "            )\n\n\n"
            "class _NoRedirect(urllib.request.HTTPRedirectHandler):\n"
            "    def redirect_request(self, req, fp, code, msg, headers, newurl):\n"
            "        raise urllib.error.HTTPError(\n"
            '            newurl, code, "redirect refused by the genesis adapter policy",\n'
            "            headers, fp,\n"
            "        )\n\n\n"
            "def run(payload):\n"
            '    """Capability entrypoint: dict -> dict. ONE network call, to BASE_URL\n'
            '    + PATH only, no redirects, no other host reachable."""\n'
            "    if payload is None:\n"
            "        payload = {}\n"
            '    _validate(payload, INPUT_FIELDS, INPUT_REQUIRED, "input")\n'
            "    body_fields = {name: payload[name] for name in INPUT_FIELDS if name in payload}\n"
            "    opener = urllib.request.build_opener(_NoRedirect)\n"
            "    url = BASE_URL + PATH\n"
            '    headers = {"Accept": "application/json"}\n'
            "    data = None\n"
            '    if METHOD == "POST":\n'
            '        data = json.dumps(body_fields, ensure_ascii=False).encode("utf-8")\n'
            '        headers["Content-Type"] = "application/json"\n'
            "    request = urllib.request.Request(url, data=data, headers=headers, method=METHOD)\n"
            "    # the opener's send method, looked up by name rather than a dotted call:\n"
            "    # the ONE network call this module ever makes\n"
            '    _send = getattr(opener, "open")\n'
            "    try:\n"
            "        with _send(request, timeout=TIMEOUT_S) as response:\n"
            '            status = getattr(response, "status", 200)\n'
            "            raw = response.read(MAX_RESPONSE_BYTES + 1)\n"
            "    except urllib.error.HTTPError as exc:\n"
            "        if 300 <= exc.code < 400:\n"
            "            raise AdapterError(\n"
            '                "dependency_unavailable", "the application redirected the request"\n'
            "            ) from exc\n"
            "        raise AdapterError(\n"
            '            "dependency_unavailable",\n'
            '            "the application returned status " + str(exc.code),\n'
            "        ) from exc\n"
            "    except Exception as exc:  # noqa: BLE001 - generated adapter, isolated env\n"
            "        raise AdapterError(\n"
            '            "dependency_unavailable",\n'
            '            "the application did not answer: " + type(exc).__name__,\n'
            "        ) from exc\n"
            "    if status < 200 or status >= 300:\n"
            "        raise AdapterError(\n"
            '            "dependency_unavailable",\n'
            '            "the application returned status " + str(status),\n'
            "        )\n"
            "    if len(raw) > MAX_RESPONSE_BYTES:\n"
            "        raise AdapterError(\n"
            '            "postcondition_failed", "the application response exceeded the size cap"\n'
            "        )\n"
            "    try:\n"
            '        text = raw.decode("utf-8")\n'
            "        result = json.loads(text) if text else {}\n"
            "    except ValueError:\n"
            "        raise AdapterError(\n"
            '            "postcondition_failed", "the application did not return JSON"\n'
            "        ) from None\n"
            "    try:\n"
            '        _validate(result, OUTPUT_FIELDS, OUTPUT_REQUIRED, "output")\n'
            "    except AdapterError:\n"
            "        raise AdapterError(\n"
            '            "postcondition_failed",\n'
            '            "the application response did not match its declared schema",\n'
            "        ) from None\n"
            "    return result\n\n\n"
            "def main():\n"
            "    raw = sys.stdin.read().strip()\n"
            "    payload = json.loads(raw) if raw else {}\n"
            "    try:\n"
            "        result = run(payload)\n"
            "    except AdapterError as exc:\n"
            "        sys.stderr.write(\n"
            '            json.dumps({"error_class": exc.error_class, "message": str(exc)})\n'
            "        )\n"
            "        return 1\n"
            "    sys.stdout.write(json.dumps(result, ensure_ascii=False))\n"
            "    return 0\n\n\n"
            'if __name__ == "__main__":\n'
            "    raise SystemExit(main())\n"
        )

    def _primary_input(self, spec: AdapterSpec) -> dict[str, Any]:
        """One representative valid payload: every required field, a safe
        default per declared type. Deterministic; carries no fixture-specific
        literal (the value is derived purely from the TYPE, not the name)."""
        defaults = {"string": "x", "integer": 1, "boolean": True, "number": 1.0}
        op = spec.operation
        return {
            f.name: defaults[f.type]
            for f in op.input_schema.fields
            if f.name in op.input_schema.required
        }

    def _render_tests(self, spec: AdapterSpec) -> str:
        op = spec.operation
        skill_name = require_identifier(spec.skill_name, field="skill_name")
        primary_input = self._primary_input(spec)
        has_required_input = bool(op.input_schema.required)
        missing_field_case = (
            "\n"
            "checks += 1\n"
            "try:\n"
            "    module.run({})\n"
            '    failures.append("run accepted a payload missing a required field")\n'
            "except module.AdapterError as exc:\n"
            '    if exc.error_class != "validation_error":\n'
            '        failures.append("wrong error_class for a missing required field")\n'
            if has_required_input
            else ""
        )
        return (
            f'"""Auto-generated tests for {require_capability_id(spec.capability_id)}.\n\n'
            f"Runs against the skill directory named by ${'PAGENTOS_SKILL_DIR'} and exercises\n"
            "the module's OWN baked-in BASE_URL — a live application must be listening\n"
            "there or every check fails honestly (dependency_unavailable), never a mock.\n"
            'Standard library only, offline apart from that one call."""\n\n'
            "import importlib.util\n"
            "import json\n"
            "import os\n"
            "import sys\n\n"
            'SKILL_DIR = os.environ["PAGENTOS_SKILL_DIR"]\n'
            "spec = importlib.util.spec_from_file_location(\n"
            f'    "skill_under_test", os.path.join(SKILL_DIR, "src", "{skill_name}.py")\n'
            ")\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n\n"
            "failures = []\n"
            "checks = 0\n\n"
            "checks += 1\n"
            'if not callable(getattr(module, "run", None)):\n'
            '    failures.append("module does not expose a callable run entrypoint")\n\n'
            "checks += 1\n"
            "try:\n"
            '    module.run("not-an-object")\n'
            '    failures.append("run accepted a non-object payload")\n'
            "except module.AdapterError as exc:\n"
            '    if exc.error_class != "validation_error":\n'
            '        failures.append("wrong error_class for a non-object payload")\n'
            f"{missing_field_case}\n"
            "checks += 1\n"
            # repr(), never json.dumps(), for a value embedded as PYTHON source:
            # JSON's lowercase true/false/null is not valid Python syntax.
            f"PRIMARY_INPUT = {dict(sorted(primary_input.items()))!r}\n"
            "try:\n"
            "    result = module.run(dict(PRIMARY_INPUT))\n"
            "except module.AdapterError as exc:\n"
            '    failures.append("run(primary) raised %s: %s" % (exc.error_class, exc))\n'
            "else:\n"
            "    if not isinstance(result, dict):\n"
            '        failures.append("run(primary) did not return an object")\n'
            "    else:\n"
            "        for name in module.OUTPUT_REQUIRED:\n"
            "            if name not in result:\n"
            '                failures.append("run(primary) missing required field: " + name)\n'
            "            elif not module._check_type(result[name], module.OUTPUT_FIELDS[name]):\n"
            '                failures.append("run(primary) field has the wrong type: " + name)\n\n'
            "print(\n"
            "    json.dumps(\n"
            '        {"total": checks, "failed": len(failures), "failures": failures[:10]},\n'
            "        ensure_ascii=False,\n"
            "    )\n"
            ")\n"
            "sys.exit(1 if failures else 0)\n"
        )

    def _render_evals(self, spec: AdapterSpec) -> str:
        capability_id = require_capability_id(spec.capability_id)
        skill_name = require_identifier(spec.skill_name, field="skill_name")
        return (
            f'"""Auto-generated eval runner for {capability_id}.\n\n'
            f"Reads evals/{CASES_FILENAME} and replays each case against the module's\n"
            "OWN baked-in BASE_URL. A case PASSES when the response is an object whose\n"
            "declared required fields are present with the declared type (the target\n"
            "is a live, stateful application, so exact-value equality would go stale\n"
            "after the first mutating call — see app.genesis.adapter's module docstring).\n"
            'Standard library only, offline apart from that one call."""\n\n'
            "import importlib.util\n"
            "import json\n"
            "import os\n"
            "import sys\n"
            "import time\n\n"
            'SKILL_DIR = os.environ["PAGENTOS_SKILL_DIR"]\n'
            "spec = importlib.util.spec_from_file_location(\n"
            f'    "skill_under_eval", os.path.join(SKILL_DIR, "src", "{skill_name}.py")\n'
            ")\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n\n"
            f'with open(os.path.join(SKILL_DIR, "{EVALS_DIRNAME}", "{CASES_FILENAME}"),'
            ' encoding="utf-8") as handle:\n'
            "    data = json.load(handle)\n\n"
            "latencies = []\n"
            "passed = 0\n"
            "failed_cases = []\n\n"
            'for index, case in enumerate(data["cases"]):\n'
            "    started = time.perf_counter()\n"
            "    try:\n"
            '        actual = module.run(dict(case.get("input") or {}))\n'
            '        shape = case.get("expected_shape") or {}\n'
            '        required = case.get("expected_required") or list(shape)\n'
            "        ok = isinstance(actual, dict) and all(\n"
            "            name in actual and module._check_type(actual[name], shape[name])\n"
            "            for name in required\n"
            "        )\n"
            "    except Exception as exc:  # noqa: BLE001 - generated eval harness\n"
            '        actual = "<error:%s>" % type(exc).__name__\n'
            "        ok = False\n"
            "    latencies.append((time.perf_counter() - started) * 1000.0)\n"
            "    if ok:\n"
            "        passed += 1\n"
            "    else:\n"
            '        failed_cases.append({"index": index, "actual": repr(actual)})\n\n'
            'total = len(data["cases"])\n'
            "ordered = sorted(latencies)\n"
            "p95 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))] if ordered else 0.0\n"
            "success_rate = (passed / total) if total else 0.0\n"
            "result = {\n"
            f'    "capability_id": {capability_id!r},\n'
            '    "total": total,\n'
            '    "passed": passed,\n'
            '    "failed": total - passed,\n'
            '    "failed_cases": failed_cases[:10],\n'
            '    "metrics": {\n'
            '        "success_rate": round(success_rate, 6),\n'
            '        "p95_latency_ms": round(p95, 6),\n'
            '        "case_count": total,\n'
            "    },\n"
            "}\n"
            'print("EVAL-RESULT " + json.dumps(result, ensure_ascii=False))\n'
            "sys.exit(0 if passed == total and total > 0 else 1)\n"
        )

    def _render_cases(self, spec: AdapterSpec) -> str:
        op = spec.operation
        primary_input = self._primary_input(spec)
        expected_shape = {f.name: f.type for f in op.output_schema.fields}
        expected_required = list(op.output_schema.required)
        payload = {
            "capability_id": spec.capability_id,
            "mode": "payload",
            "cases": [
                {
                    "input": primary_input,
                    "expected_shape": expected_shape,
                    "expected_required": expected_required,
                }
            ],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


__all__ = [
    "DEFAULT_VERSION",
    "DISPATCH_TIMEOUT_S",
    "MAX_RESPONSE_BYTES",
    "AdapterSpec",
    "HttpAdapterGenerator",
]
