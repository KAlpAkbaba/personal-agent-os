import type { Verification, VerifySource } from "./api";

const DATE = new Intl.DateTimeFormat("tr-TR", {
  day: "numeric",
  month: "long",
  year: "numeric",
  timeZone: "Europe/Istanbul",
});

function day(iso: string | null): string | null {
  if (!iso) return null;
  const at = Date.parse(iso);
  return Number.isNaN(at) ? null : DATE.format(at);
}

const STANCE_LABEL: Record<VerifySource["stance"], string> = {
  supports: "destekliyor",
  refutes: "çürütüyor",
  neutral: "karar vermiyor",
};

function SourceLine({ source }: { source: VerifySource }) {
  const published = day(source.published_at);
  return (
    <li data-source>
      <a href={source.url} target="_blank" rel="noreferrer noopener">
        {source.title}
      </a>
      {published ? <span className="muted"> · {published}</span> : null}
      <span className="muted"> · {STANCE_LABEL[source.stance]}</span>
      {source.quote ? <blockquote>{source.quote}</blockquote> : null}
    </li>
  );
}

function VerdictBadge({ item }: { item: Verification }) {
  if (item.status !== "settled" || !item.verdict) {
    return (
      <span className="badge" data-verdict="pending">
        Bekliyor
      </span>
    );
  }
  const percent = item.confidence === null ? null : Math.round(item.confidence * 100);
  return (
    <span className="badge" data-verdict={item.verdict}>
      {item.verdict_label ?? item.verdict}
      {percent === null ? null : ` · %${percent}`}
    </span>
  );
}

/** The owner's verifications, newest first, as the server sent them. */
export default function VerificationList({ items }: { items: Verification[] }) {
  if (items.length === 0) {
    return <p className="muted">Bu aralıkta doğrulanan bir iddia yok.</p>;
  }
  return (
    <ul className="task-list" data-verifications>
      {items.map((item) => {
        const when = day(item.created_at);
        const settled = item.status === "settled";
        return (
          <li key={item.verification_id} className="panel task" data-verification>
            <div className="status-row" style={{ borderBottom: "none", padding: 0 }}>
              <VerdictBadge item={item} />
              <strong>{item.claim}</strong>
              {when ? <span className="muted">{when}</span> : null}
            </div>
            {item.spoken ? <p data-spoken>{item.spoken}</p> : null}
            {settled && item.sources.length === 0 ? (
              <p className="muted" data-no-source>
                Karar veren kaynak bulunamadı; hüküm tahmin edilmedi.
              </p>
            ) : null}
            {item.sources.length > 0 ? (
              <ol data-sources>
                {item.sources.map((source) => (
                  <SourceLine key={source.url} source={source} />
                ))}
              </ol>
            ) : null}
            {item.counter_argument ? (
              <div data-counter>
                <span className="muted">En güçlü karşı argüman:</span>
                <ul>
                  <SourceLine source={item.counter_argument} />
                </ul>
              </div>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}
