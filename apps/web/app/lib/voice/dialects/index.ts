import type { Dialect } from "../transport";
import { TransportConfigError } from "../transport";
import { OPENAI_REALTIME_DIALECT, OpenAIRealtimeDialect } from "./openaiRealtime";

const REGISTRY: Record<string, () => Dialect> = {
  [OPENAI_REALTIME_DIALECT]: () => new OpenAIRealtimeDialect(),
};

export function knownDialects(): string[] {
  return Object.keys(REGISTRY).toSorted();
}

/**
 * Resolve the dialect named by the transport descriptor. There is no default:
 * a descriptor without a dialect is a server-side gap, not something the
 * browser papers over with a vendor guess.
 */
export function dialectFor(name: string | undefined): Dialect {
  if (!name) {
    throw new TransportConfigError(
      "transport descriptor names no event dialect; the provider adapter must set `dialect`",
    );
  }
  const factory = REGISTRY[name];
  if (!factory) {
    throw new TransportConfigError(
      `unknown event dialect ${JSON.stringify(name)}; known: ${knownDialects().join(", ")}`,
    );
  }
  return factory();
}
