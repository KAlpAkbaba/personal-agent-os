"use client";

/**
 * The Cockpit's client for the Native Application Factory (M28 spec §4, §6).
 *
 * ONE route, and deliberately only one: a GET of `/v1/native/builds`, the
 * `native_builds` rows. Each row says which application was asked for, which
 * of the five artefacts it is, the stack §3's rule chose, the step it
 * reached, and — for a build that produced something — the artefact's name,
 * its size in bytes, its sha256, and what the INDEPENDENT reader said about
 * it (`{ok, mismatches}`, the shape
 * `app/nativefactory/artifacts.py::ArtifactVerdict` produces).
 *
 * There are no POSTs here, and that is a decision rather than an omission.
 * Every other panel's chips ask the Cloud Core to do something bounded and
 * reversible — render a scene, export a picture, pause a run. A build starts
 * a twenty-minute compiler under a Job Object and can end by installing a
 * signed package for the current user (spec §5); "Derle" as a Cockpit chip
 * would be a second authority surface for an action the owner asks for by
 * voice through the ONE router, which already refuses on its own terms.
 * This panel watches. It cannot build, package, install or delete anything.
 *
 * The Cloud Core half is built on a parallel track, so the response shape
 * below is the spec's columns read defensively: a field the route does not
 * send is `null` and is rendered as "not reported", never filled in. A 404
 * is "henüz yok", which is the truthful word until the route lands.
 */

import { API_BASE } from "../session";
import { asArtifactBytes } from "../uistate/contract";
import { type Loaded, load } from "./api";

// ---------------------------------------------------------------- the route

export const NATIVE_BUILDS_PATH = "/v1/native/builds";

/** The list's URL as the browser would address it — for the absent notice, never fetched by a link. */
export function nativeBuildsUrl(): string {
  return `${API_BASE}${NATIVE_BUILDS_PATH}`;
}

// ------------------------------------------------------------------ the rows

/**
 * One build as the list route describes it (M28 spec §2, §4), every field
 * verbatim or `null`.
 *
 * The three artefact fields are the point of the row. `artifact_name` is a
 * file NAME, never a path — the build happens under an authorised root on
 * the owner's machine and where it is is not the Cockpit's business.
 * `artifact_bytes` and `artifact_sha256` are what a reader MEASURED in the
 * produced file, not what the spec asked for, which is why they may exist on
 * a `mismatch` row too: the file is real, and it is not what was wanted.
 */
export type NativeBuildRow = {
  build_id: string;
  /** The application's name as the row says it. */
  app: string | null;
  /** `windows_exe` | `windows_portable` | … , or whatever the row says. */
  target: string | null;
  /** The stack §3's rule chose, as the row says. */
  stack: string | null;
  /** The step the build reached, as the row says. */
  state: string | null;
  /** The version the spec asked for, as the row says. */
  version: string | null;
  /** The produced file's name, as the row says. Never a path. */
  artifact_name: string | null;
  /** The produced file's size in bytes, as a reader measured it. */
  artifact_bytes: number | null;
  /** The produced file's sha256, as a reader computed it. The panel shows a prefix. */
  artifact_sha256: string | null;
  /** The version the INDEPENDENT reader found INSIDE the artefact. Never the spec's. */
  artifact_version: string | null;
  /**
   * What the independent reader concluded: `true` it agreed, `false` it
   * disagreed, `null` it never reported. Read ONLY from the row's own
   * verdict — never derived from the state, which is a different statement.
   */
  verdict_ok: boolean | null;
  /** Every disagreement the reader named, verbatim and in its order. */
  verdict_mismatches: string[];
  /** On a failure or an unreachable toolchain: the taxonomy class, as the row says. */
  error_class: string | null;
  /** On a failure or an unreachable toolchain: the build's own sentence. */
  error_message: string | null;
  created_at: string | null;
  updated_at: string | null;
};

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

function flag(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

function record(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
}

/** The strings of a list the row sent, in its order; `[]` for anything else. */
function strings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string" && v.length > 0);
}

/** The list under one of `keys`, or the body itself when it is the list. */
function listAt(raw: unknown, keys: string[]): unknown[] {
  if (Array.isArray(raw)) return raw;
  const body = record(raw);
  if (body) {
    for (const key of keys) {
      const value = body[key];
      if (Array.isArray(value)) return value;
    }
  }
  return [];
}

function isPresent<T>(value: T | null): value is T {
  return value !== null;
}

/**
 * The object the independent reader's own verdict is in, whichever shape the
 * route sent.
 *
 * `app/nativefactory/artifacts.py::ArtifactVerdict.as_dict()` produces
 * `{ok, mismatches, facts}` — the reader's conclusion and the facts it read
 * in one object — while `native_builds` has `verdict_json` and
 * `artifact_json` as separate columns. So a row may carry the verdict under
 * `verdict`, under `verdict_json`, or as the artefact object itself when the
 * route passed the reader's answer through whole. An `artifact` that carries
 * an `ok` IS a verdict; one that does not is only facts, and is not read as
 * one.
 */
function verdictSource(o: Record<string, unknown>): Record<string, unknown> | null {
  const explicit = record(o.verdict) ?? record(o.verdict_json);
  if (explicit) return explicit;
  const artifact = record(o.artifact) ?? record(o.artifact_json);
  return artifact && "ok" in artifact ? artifact : null;
}

/**
 * The verdict as a boolean, from the row's own report and from nothing else.
 *
 * Deliberately NOT inferred from `state === "verified"`. The step and the
 * verdict are two statements — "the build settled here" and "the reader that
 * opened the file said this" — and collapsing them would make the panel able
 * to claim a reader spoke when none did. A row that reported no verdict
 * says so.
 */
export function parseVerdictOk(o: Record<string, unknown>): boolean | null {
  const verdict = verdictSource(o);
  if (verdict) {
    const ok = flag(verdict.ok);
    if (ok !== null) return ok;
  }
  return flag(o.verdict_ok);
}

/** Every disagreement the reader named, from the verdict object or the flat field. */
export function parseVerdictMismatches(o: Record<string, unknown>): string[] {
  const verdict = verdictSource(o);
  if (verdict) {
    const named = strings(verdict.mismatches);
    if (named.length) return named;
  }
  return strings(o.verdict_mismatches ?? o.mismatches);
}

/** One build from a raw row; `null` for a row with no id, which is not a build. */
export function parseNativeBuildRow(raw: unknown): NativeBuildRow | null {
  const o = record(raw);
  if (!o) return null;
  const id = str(o.build_id) ?? str(o.id);
  if (id === null) return null;
  // The artefact's facts may arrive nested (the reader's own `facts` object)
  // or flattened onto the row; either way they are what a reader MEASURED.
  const artifact = record(o.artifact) ?? record(o.artifact_json) ?? {};
  const facts = record(artifact.facts) ?? artifact;
  return {
    build_id: id,
    app: str(o.app) ?? str(o.display_name) ?? str(o.name),
    target: str(o.target),
    stack: str(o.stack),
    state: str(o.state),
    version: str(o.version),
    artifact_name: str(o.artifact_name) ?? str(facts.name) ?? str(facts.file_name),
    artifact_bytes: asArtifactBytes(o.artifact_bytes ?? facts.size_bytes ?? o.size_bytes),
    artifact_sha256: str(o.artifact_sha256) ?? str(facts.sha256),
    artifact_version: str(o.artifact_version) ?? str(facts.version),
    verdict_ok: parseVerdictOk(o),
    verdict_mismatches: parseVerdictMismatches(o),
    error_class: str(o.error_class),
    error_message: str(o.error_message),
    created_at: str(o.created_at),
    updated_at: str(o.updated_at),
  };
}

export const fetchNativeBuilds = (): Promise<Loaded<NativeBuildRow[]>> =>
  load<NativeBuildRow[]>(NATIVE_BUILDS_PATH, (raw) =>
    listAt(raw, ["builds", "native_builds", "items"]).map(parseNativeBuildRow).filter(isPresent),
  );
