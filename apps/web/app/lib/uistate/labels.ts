/**
 * Turkish surface text for the Core (constitution: `tr-TR` is first-class).
 *
 * The wording carries the same obligation as the geometry. "Boşta" is a claim
 * that the system reported nothing running; "Henüz bir durum bildirilmedi" is a
 * claim that it reported nothing at all. Collapsing those two into one friendly
 * sentence would undo the whole point of the state model, so they stay
 * separate here and everywhere downstream.
 */

import type { EyeStatus, PresenceKind, ReleaseStage } from "./ambient";
import type { KnownUiState } from "./contract";
import type { CoreVisualKind, VisualSource } from "./visual";

/** Headline shown under the core. One short phrase, no invented detail. */
export const KIND_LABEL: Record<CoreVisualKind, string> = {
  connecting: "Bağlanıyor",
  untold: "Henüz bir durum bildirilmedi",
  unreachable: "Cloud Core'a ulaşılamıyor",
  unauthorized: "Oturum reddedildi",
  unknown_state: "Bilinmeyen durum",
  last_known: "Son bilinen durum",
  idle: "Boşta",
  listening: "Dinliyor",
  thinking: "Düşünüyor",
  speaking: "Konuşuyor",
  interrupted: "Kesildi",
  researching: "Araştırıyor",
  memory: "Hafıza çalışıyor",
  tool_running: "Araç çalışıyor",
  waiting_owner: "Sahibi bekliyor",
  goal_completed: "Hedef tamamlandı",
  error: "Hata",
  evolution_working: "Laboratuvar çalışıyor",
  shadow_ready: "Gölge aday hazır",
};

/**
 * The second line: what the kind actually means, in the owner's terms.
 *
 * These sentences are deliberately about *evidence*, because the difference
 * between "nothing is running" and "nothing has told us anything" is the one
 * the owner most needs, and it is invisible from the geometry alone.
 */
export const KIND_DETAIL: Record<CoreVisualKind, string> = {
  connecting: "Durum akışı henüz okunmadı.",
  untold:
    "Bağlantı çalışıyor, ancak hiçbir alt sistem bir durum yayınlamadı. Bu 'boşta' demek değil.",
  unreachable: "Aşağıdaki görüntü son bilinen durumdur, canlı değildir.",
  unauthorized: "Sahip oturumu gerekiyor.",
  unknown_state:
    "Bu sürümün tanımadığı bir durum bildirildi. Sözleşme güncellenmiş olabilir.",
  last_known:
    "Bu durumun süresi doldu. İşin bittiği bildirilmedi — yalnızca yeni bir bildirim gelmedi.",
  idle: "Çalışan bir iş bildirilmedi.",
  listening: "Sahip konuşuyor.",
  thinking: "Girdi ile yanıt arasında akıl yürütme çalışıyor.",
  speaking: "Anlatım sürüyor.",
  interrupted: "Sahip söze girdi; konuşma durduruldu.",
  researching: "Bir araştırma işi kaynak buluyor ve getiriyor.",
  memory: "Hafıza geri çağırma / pekiştirme çalışıyor.",
  tool_running: "Bir yetenek çalışıyor.",
  waiting_owner: "Sistem kasıtlı olarak duruyor: onay veya doğrulama bekleniyor.",
  goal_completed: "Bir hedef başarı ölçütlerini karşıladı.",
  error: "Sahibin bilmesi gereken bir hata var.",
  evolution_working: "Evrim laboratuvarı bir aday üzerinde çalışıyor. Canlı değil.",
  shadow_ready: "Bir aday kapılarını geçti ve onay bekliyor. Canlıya alınmadı.",
};

/**
 * Which source produced the visual (ADR-0061 §4). Stated on every readout,
 * because a listening core drawn from this device's own session and one drawn
 * from the cloud's account of some other device are different claims.
 */
export const SOURCE_LABEL: Record<VisualSource, string> = {
  bus: "Kaynak: Cloud Core durum akışı",
  voice: "Kaynak: bu cihazdaki ses oturumu",
};

/**
 * The second line for a voice-sourced intent, where the bus wording would be
 * wrong: `connecting` from the bus means "no poll has succeeded yet", from the
 * voice controller it means "a media leg is being opened".
 */
export const VOICE_KIND_DETAIL: Partial<Record<CoreVisualKind, string>> = {
  connecting: "Ses oturumu kuruluyor.",
  listening: "Mikrofon açık; sahip dinleniyor.",
  speaking: "Asistan konuşuyor. Nabız, gerçek çıkış seviyesidir.",
  tool_running: "Ses oturumunda bir araç çalışıyor.",
  interrupted: "Sahip söze girdi; ses anında kesildi.",
  error: "Ses oturumunda hata.",
};

/** The detail line for an intent, by its source. */
export function kindDetail(kind: CoreVisualKind, source: VisualSource): string {
  return (source === "voice" ? VOICE_KIND_DETAIL[kind] : undefined) ?? KIND_DETAIL[kind];
}

// ------------------------------------------------------ M18.1: the structure

/**
 * Said beside a research core that drew the fixed constellation motif. The
 * geometry is a representation of "research is running"; the sentence keeps
 * it from being read as a count.
 */
export const CONSTELLATION_MOTIF_NOTE = "Çizilen takımyıldız sabit bir temsildir, sayım değildir.";

/** The capability nodes' line: counted by the lab, or the one candidate the event is about. */
export function capabilityNodesLine(count: number, counted: boolean): string {
  if (!counted) return "Bir aday çevrede park edildi; laboratuvar sayı bildirmedi.";
  return `${count} hazır aday çevrede park edildi.`;
}

/**
 * The numeric channels (ADR-0065), named for the cockpit's telemetry. Every
 * entry is a number on `VisualIntent` and nothing else; the cockpit prints
 * them as they are so the owner can see what the geometry was drawn from.
 */
export const CHANNEL_LABEL: Record<
  | "energy"
  | "glow"
  | "shellSpread"
  | "ringSpin"
  | "flowRate"
  | "inwardFlow"
  | "topology"
  | "pulse"
  | "ownerVoice"
  | "constellationDrift"
  | "restraint"
  | "agitation",
  string
> = {
  energy: "Enerji",
  glow: "Işıma",
  shellSpread: "Kabuk açıklığı",
  ringSpin: "Halka dönüşü",
  flowRate: "Yol akışı",
  inwardFlow: "İçe akış",
  topology: "Topoloji",
  pulse: "Nabız",
  ownerVoice: "Sahibin sesi",
  constellationDrift: "Takımyıldız sürüklenmesi",
  restraint: "Kısıtlama",
  agitation: "Sarsıntı",
};

/** Raw contract token → Turkish. Used where the exact state matters. */
export const STATE_LABEL: Record<KnownUiState, string> = {
  "agent.idle": "Boşta",
  "agent.listening": "Dinliyor",
  "agent.thinking": "Düşünüyor",
  "agent.speaking": "Konuşuyor",
  "agent.researching": "Araştırıyor",
  "agent.memory_retrieval": "Hafıza",
  "agent.tool_running": "Araç çalışıyor",
  "agent.waiting_owner": "Sahibi bekliyor",
  "agent.goal_completed": "Hedef tamamlandı",
  "agent.error": "Hata",
  "evolution.researching": "Lab: araştırıyor",
  "evolution.designing": "Lab: tasarlıyor",
  "evolution.building": "Lab: inşa ediyor",
  "evolution.testing": "Lab: test ediyor",
  "evolution.shadow_ready": "Lab: gölge hazır",
  "eye.active": "Göz açık",
  "eye.disabled": "Göz kapalı",
  // "likely" is in the state name because the engine is not sure. The Turkish
  // says so too: dropping "büyük olasılıkla" would turn an inference into a fact.
  "owner.present": "Sahip burada",
  "owner.away": "Sahip yok",
  "owner.returned": "Sahip döndü",
  "owner.resting": "Sahip dinleniyor",
  "owner.likely_asleep": "Sahip büyük olasılıkla uyuyor",
  "owner.awake": "Sahip uyanık",
  "routine.armed": "Rutin kuruldu",
  "routine.triggered": "Rutin çalıştı",
  "alarm.triggered": "Alarm çaldı",
  "release.owner_approval_required": "Sahip onayı gerekiyor",
  "release.owner_authorized": "Sahip yetkilendirdi",
  "release.qualifying": "Yeterlilik kontrolü",
  "release.deploying": "Kuruluyor",
  "release.verifying": "Doğrulanıyor",
  "release.live": "Canlı",
  "release.rollback": "Geri alınıyor",
};

export function stateLabel(state: string): string {
  return (STATE_LABEL as Record<string, string>)[state] ?? state;
}

export const SUBSYSTEM_LABEL: Record<string, string> = {
  voice: "Ses",
  research: "Araştırma",
  browser: "Tarayıcı",
  memory: "Hafıza",
  experience: "Deneyim",
  goal: "Hedef",
  cognitive: "Biliş",
  self_model: "Öz model",
  evolution: "Evrim",
  deployment: "Dağıtım",
  ledger: "Defter",
  system: "Sistem",
  presence: "Varlık",
};

export function subsystemLabel(subsystem: string): string {
  return SUBSYSTEM_LABEL[subsystem] ?? subsystem;
}

export const SEVERITY_LABEL: Record<string, string> = {
  info: "bilgi",
  notice: "dikkat",
  warning: "uyarı",
  critical: "kritik",
};

/** "3 sn önce" / "4 dk önce" / "2 sa önce". `null` age yields "bilinmiyor". */
export function formatAge(ms: number | null): string {
  if (ms === null) return "bilinmiyor";
  const seconds = Math.floor(ms / 1000);
  if (seconds < 1) return "az önce";
  if (seconds < 60) return `${seconds} sn önce`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} dk önce`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} sa önce`;
  return `${Math.floor(hours / 24)} gün önce`;
}

/** Percent for display, only ever called with a real progress figure. */
export function formatProgress(progress: number): string {
  return `%${Math.round(progress * 100)}`;
}

// ------------------------------------------------------------ v2: the room

/**
 * The eye's three states, and why "untold" is not "off".
 *
 * A camera indicator that shows "kapalı" when nobody has said anything is the
 * one failure mode a privacy indicator may never have: it would tell the owner
 * they are not being watched on no evidence at all.
 */
export const EYE_LABEL: Record<EyeStatus, string> = {
  active: "Göz açık",
  disabled: "Göz kapalı",
  untold: "Kamera durumu bildirilmedi",
};

export const EYE_DETAIL: Record<EyeStatus, string> = {
  active: "Yerel algı çalışıyor. Görüntü buluta gönderilmiyor ve kaydedilmiyor.",
  disabled: "Algı durduruldu.",
  untold: "Kameranın açık mı kapalı mı olduğu bildirilmedi. Bu 'kapalı' demek değil.",
};

export const PRESENCE_LABEL: Record<PresenceKind, string> = {
  present: "Sahip burada",
  away: "Sahip yok",
  returned: "Sahip döndü",
  resting: "Sahip dinleniyor",
  likely_asleep: "Sahip büyük olasılıkla uyuyor",
  awake: "Sahip uyanık",
  unknown: "Sahip durumu bilinmiyor",
};

export const RELEASE_LABEL: Record<ReleaseStage, string> = {
  owner_approval_required: "Sahip onayı bekleniyor",
  owner_authorized: "Sahip yetkilendirdi",
  qualifying: "Yeterlilik kontrolü",
  deploying: "Kuruluyor",
  verifying: "Doğrulanıyor",
  live: "Canlı",
  rollback: "Geri alınıyor",
  routine_armed: "Rutin kuruldu",
  routine_triggered: "Rutin çalıştı",
  alarm_triggered: "Alarm çaldı",
  none: "Süren bir yayın yok",
};

/**
 * Confidence, stated as the engine stated it.
 *
 * Never rounded to "kesin", never omitted when present, and when the publisher
 * sent none the answer is that it sent none — the M18 spec's example is exactly
 * this: `LIKELY_ASLEEP confidence=0.86`, not `OWNER_IS_ASLEEP`.
 */
export function formatConfidence(confidence: number | null): string {
  if (confidence === null) return "güven bildirilmedi";
  return `güven %${Math.round(confidence * 100)}`;
}
