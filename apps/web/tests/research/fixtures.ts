import type { DeviceInfo, ResearchReport, ResearchTaskDetail } from "../../app/lib/research/model";

/** A spec §3 report: every section populated, one dangling citation, one flagged source. */
export const REPORT: ResearchReport = {
  schema_version: 1,
  task_id: "task-1",
  topic: "Son üç gündeki yapay zekâ ajanlarıyla ilgili önemli gelişmeler",
  window: { start: "2026-08-31T00:00:00Z", end: "2026-09-03T00:00:00Z", label: "son 3 gün" },
  generated_at: "2026-09-03T08:00:00Z",
  synthesis_provider: "deterministic",
  executive_summary: "Üç günde ajan çerçevelerinde iki büyük sürüm ve bir güvenlik olayı öne çıktı.",
  findings: [
    {
      id: "f1",
      title: "Çerçeve A 2.0 yayınlandı",
      summary: "Çok ajanlı orkestrasyon varsayılan oldu.",
      why_it_matters: "Mevcut iş akışları göç gerektirecek.",
      importance: 5,
      label: "source_fact",
      evidence_ids: ["e1", "e2"],
      first_seen: "2026-09-01T10:00:00Z",
    },
    {
      id: "f2",
      title: "Enjeksiyon olayı bildirildi",
      summary: "Bir tarayıcı ajanı sayfa talimatına uydu.",
      why_it_matters: "Güvenilmeyen içerik sınırı zorunlu.",
      importance: 4,
      label: "source_fact",
      evidence_ids: ["e3"],
    },
    {
      id: "f3",
      title: "Pazar hareketleniyor",
      summary: "Birkaç sağlayıcı fiyat indirdi.",
      why_it_matters: "Maliyet planı gözden geçirilmeli.",
      importance: 2,
      label: "model_inference",
      evidence_ids: ["e9"],
      provenance_note: "kanıt alıntısı ifadeyi desteklemediği için düşürüldü",
    },
  ],
  why_it_matters: [{ text: "Ajan çerçeveleri olgunlaşıyor.", label: "model_inference", evidence_ids: ["e1"] }],
  watch_next: [{ text: "Çerçeve A göç kılavuzunu izle.", label: "recommendation", evidence_ids: [] }],
  details: [
    {
      heading: "Çerçeve A",
      statements: [
        { text: "Sürüm notları çok ajanlı modu anlatıyor.", label: "source_fact", evidence_ids: ["e1"] },
        { text: "Geçiş süresi tahmini iki hafta.", label: "model_inference", evidence_ids: [] },
      ],
    },
  ],
  uncertainty: [{ text: "Olayın kapsamı henüz doğrulanmadı.", label: "uncertainty", evidence_ids: [] }],
  sources: [
    {
      id: "e1",
      url: "https://example.org/framework-a/2.0?utm_source=x",
      final_url: "https://example.org/framework-a/2.0",
      title: "Framework A 2.0 release notes",
      publisher: "Framework A",
      source_class: "official",
      published_at: "2026-09-01T10:00:00Z",
      retrieved_at: "2026-09-03T07:55:00Z",
      excerpt: "Multi-agent orchestration is now the default.",
      injection_suspected: false,
      syndicated_of: null,
    },
    {
      id: "e2",
      url: "https://news.example.net/framework-a-2",
      title: "Framework A ships 2.0",
      publisher: "Example News",
      source_class: "news",
      published_at: "2026-09-01T12:00:00Z",
      retrieved_at: "2026-09-03T07:56:00Z",
      injection_suspected: false,
      syndicated_of: "e1",
    },
    {
      id: "e3",
      url: "https://forum.example.com/incident",
      title: "Browser agent followed page instructions",
      publisher: "Example Forum",
      source_class: "community",
      published_at: "2026-09-02T09:00:00Z",
      retrieved_at: "2026-09-03T07:57:00Z",
      injection_suspected: true,
      syndicated_of: null,
    },
  ],
  stats: { queries: 9, discovered: 41, fetched: 18, fetch_failed: 3, deduplicated: 6, evidence: 12 },
};

export const DEVICES: DeviceInfo[] = [
  {
    device_id: "dev-home",
    name: "Ev bilgisayarı",
    platform: "windows",
    status: "online",
    presence: "online",
    capabilities: ["browser.chrome", "browser.search", "fs.read"],
    aliases: ["ev", "masaüstü"],
  },
  {
    device_id: "dev-laptop",
    name: "Dizüstü",
    platform: "windows",
    status: "offline",
    presence: "stale",
    capabilities: ["fs.read"],
    aliases: [],
  },
  {
    device_id: "dev-old",
    name: "Eski makine",
    platform: "windows",
    status: "offline",
    capabilities: ["browser.chrome"],
  },
];

export function taskAt(stage: string, extra: Partial<ResearchTaskDetail> = {}): ResearchTaskDetail {
  return {
    task_id: "task-1",
    topic: REPORT.topic,
    status: stage === "ready" ? "READY" : stage === "failed" ? "FAILED" : "RUNNING",
    stage,
    progress: { queries_total: 9, queries_done: 4, discovered: 12, fetch_total: 12, fetch_done: 3, fetch_failed: 1, evidence: 2 },
    device: { device_id: "dev-home", name: "Ev bilgisayarı" },
    report: stage === "ready" ? REPORT : null,
    artifact_id: stage === "ready" ? "art-1" : null,
    memory_id: null,
    error: null,
    events: [
      { at: "2026-09-03T07:50:00Z", stage: "planned", detail: "9 sorgu" },
      { at: "2026-09-03T07:50:05Z", stage: "selecting_device", detail: "Ev bilgisayarı" },
      { at: "2026-09-03T07:51:00Z", stage, detail: null },
    ],
    ...extra,
  };
}
