/**
 * Turkish surface text for the Core (constitution: `tr-TR` is first-class).
 *
 * The wording carries the same obligation as the geometry. "Boşta" is a claim
 * that the system reported nothing running; "Henüz bir durum bildirilmedi" is a
 * claim that it reported nothing at all. Collapsing those two into one friendly
 * sentence would undo the whole point of the state model, so they stay
 * separate here and everywhere downstream.
 */

import type { AlarmStage, DisplayState, EyeStatus, PresenceKind, ReleaseStage } from "./ambient";
import {
  APP_CAPTION_BARE,
  APP_STATE_LABEL,
  ARTIFACT_CAPTION_BARE,
  ARTIFACT_VERDICT_LABEL,
  CALENDAR_CAPTION_BARE,
  CREATIVE_CAPTION_BARE,
  DOCUMENT_CAPTION_BARE,
  EXECUTIVE_CAPTION_BARE,
  GENESIS_CAPTION_BARE,
  GENESIS_STATE_LABEL,
  MAIL_CAPTION_BARE,
  SCENE_CAPTION_BARE,
} from "./contract";
import type { DocumentRef, KnownUiState } from "./contract";
import { type AppFacts, type AppStage, appTestsPhrase } from "./apps";
import { type GenesisFacts, type GenesisStage, genesisStatePhrase } from "./genesis";
import {
  SCENE_STATE_LABEL,
  type SceneFacts,
  type SceneStage,
  sceneStatePhrase,
  sceneToolWord,
} from "./scenes";
import {
  CREATIVE_STATE_LABEL,
  type CreativeFacts,
  type CreativeStage,
  creativeOperationWord,
  creativeSimilarityPhrase,
  creativeStatePhrase,
  creativeToolWord,
} from "./creative";
import {
  EXECUTIVE_RUN_STATE_LABEL,
  type ExecutiveFacts,
  type ExecutiveStage,
  executiveStatePhrase,
  executiveStepsPhrase,
} from "./executive";
import { type ArtifactFacts, type ArtifactStage, artifactFormatLabel } from "./artifacts";
import {
  CALENDAR_PROPOSAL_STATE_LABEL,
  type CalendarFacts,
  type CalendarStage,
  calendarRangePhrase,
  conflictsPhrase,
} from "./calendar";
import {
  type DocumentFacts,
  type DocumentStage,
  documentPartPhrase,
  documentStepLabel,
  refKind,
} from "./documents";
import { MAIL_DRAFT_STATE_LABEL, type MailFacts, type MailStage, mailFolderPhrase } from "./mail";
import { type OperatorFacts, type OperatorStage, operatorPosition } from "./operator";
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
  operator_running: "Operatör çalışıyor",
  operator_verifying: "Operatör doğruluyor",
  operator_failed: "Operatör başarısız",
  document_analysis: DOCUMENT_CAPTION_BARE,
  mail_activity: MAIL_CAPTION_BARE,
  calendar_activity: CALENDAR_CAPTION_BARE,
  artifact_factory: ARTIFACT_CAPTION_BARE,
  app_factory: APP_CAPTION_BARE,
  capability_genesis: GENESIS_CAPTION_BARE,
  scene_activity: SCENE_CAPTION_BARE,
  executive_run: EXECUTIVE_CAPTION_BARE,
  creative_activity: CREATIVE_CAPTION_BARE,
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
  // M19: every sentence here is about the loop the publisher reported, not
  // about whether it worked. "Çalışıyor" is a step being sent; "doğruluyor"
  // is the second observation; a click is never reported as having succeeded.
  operator_running: "Dijital operatör masaüstünde bir adımı uyguluyor. Sonuç henüz doğrulanmadı.",
  operator_verifying: "Adım uygulandı; sonuç yeniden gözlenerek son koşul denetleniyor.",
  operator_failed: "Bir operatör adımı son koşulunu karşılamadı ya da reddedildi. Yeni bir bildirim gelene kadar bu durum kalır.",
  // M20: a document is being read on the owner's machine and answered from
  // its refs. The file and the place are named only as published; how far
  // along it is, nothing here knows.
  document_analysis:
    "Sahibin bir belgesi okunuyor; dosya ve içindeki yer yalnızca yayınlandığı kadar söylenir. İlerleme bildirilmez.",
  // M21: reading and preparing are the assistant's; sending is the owner's.
  // Both sentences say which side of that line the posture is on, and that
  // nothing on this screen crosses it by itself.
  mail_activity:
    "Sahibin postası okunuyor ya da bir taslak hazırlanıyor; klasör, konu ve taslak durumu yalnızca yayınlandığı kadar söylenir. Hiçbir şey sahip onayı olmadan gönderilmez.",
  calendar_activity:
    "Sahibin takvimi okunuyor ya da bir öneri hazırlanıyor; aralık, etkinlik ve öneri durumu yalnızca yayınlandığı kadar söylenir. Takvime sahip onayı olmadan yazılmaz.",
  // M22: a file is being made, then reopened by an independent parser and
  // compared to what was asked. The sentence says which side of "done" the
  // posture is on: a render is done when the parser found what was asked,
  // never when it was written — and nothing here says how far along it is.
  artifact_factory:
    "Sahip için bir dosya üretiliyor ya da bağımsız bir okuyucuyla yeniden açılıp istenenle karşılaştırılıyor; başlık, biçim ve sonuç yalnızca yayınlandığı kadar söylenir. Doğrulanmamış bir çıktı bitmiş sayılmaz. İlerleme bildirilmez.",
  // M23: a program is being made for the owner — planned, written into a
  // real project on the owner's machine, run there in a bounded process,
  // tested with its own tests. The sentence says what "exists" means for an
  // app (ADR-0086) and that nothing here says it on its own.
  app_factory:
    "Sahip için bir uygulama planlanıyor, iskeleti kuruluyor, sahibin makinesinde sınırlı bir süreçte çalıştırılıyor ya da kendi testleriyle sınanıyor; proje, durum, port ve test sayıları yalnızca yayınlandığı kadar söylenir. Testleri geçmemiş bir uygulama bitmiş sayılmaz. İlerleme bildirilmez.",
  // M24: the assistant lacks a capability the owner's request needs and is
  // acquiring one — the interface researched, an adapter written and tested
  // against the running application, classified, approved by the owner
  // where authority requires it, rolled out, registered, used and verified
  // by reading the application back. The sentence says what "having" the
  // capability means (ADR-0087) and that nothing here says it on its own.
  capability_genesis:
    "Sahibin istediği şey için bir yetenek yok; arayüzü araştırılıyor, bağdaştırıcısı yazılıyor, çalışan uygulamaya karşı sınanıyor, gerekirse sahip onayı bekleniyor, kaydediliyor, kullanılıyor ve sonuç uygulamadan okunarak doğrulanıyor; yetenek, durum ve hata sınıfı yalnızca yayınlandığı kadar söylenir. Doğrulanmamış bir yetenek yapıldı sayılmaz. İlerleme bildirilmez.",
  // M25: a 3D scene is being built or changed through the tool's OWN
  // scripting interface, rendered, and then READ BACK from the tool and
  // compared with the plan. The sentence says what "done" means for a scene
  // (ADR-0088 §4) and that a tool that cannot be driven is said so rather
  // than claimed (§5) — nothing here controls a mouse in an editor.
  scene_activity:
    "Sahip için bir 3B sahne, aracın kendi betik arayüzüyle kuruluyor, değiştiriliyor, render alınıyor ya da araçtan geri okunuyor; araç, sahne, adım ve nesne sayısı yalnızca yayınlandığı kadar söylenir. Geri okunup istenenle karşılaştırılmamış bir sahne doğrulanmış sayılmaz; sürülemeyen bir araç için denetim kurulmaz, yapılamadığı söylenir. İlerleme bildirilmez.",
  // M26: a multi-step job the owner asked for is being carried across the
  // families — planned, run step by step, paused and resumed on the owner's
  // word, and ended honestly. The sentence says what "done" means for a run
  // (ADR-0089 §3: only `completed` is "tamamlandı", a partial run names what
  // is missing) and that the owner keeps every lever — and that no step of
  // one can send, pay, delete or publish anything (§4).
  executive_run:
    "Sahibin istediği çok adımlı bir iş yürütülüyor: adımlar sırayla çalıştırılıyor, sahip istediği an duraklatabiliyor, sürdürebiliyor ya da iptal edebiliyor; iş, adım ve adım sayısı yalnızca yayınlandığı kadar söylenir. Yalnızca her adımı doğrulanmış bir iş tamamlandı sayılır; eksik kalan iş kısmen bitti denip nesi eksik olduğu söylenir. Hiçbir adım sahip onayı olmadan posta göndermez, ödeme yapmaz, silmez, yayımlamaz.",
  // M27: a picture the owner asked for is being analysed, planned as data,
  // applied through the most structured interface the installed application
  // really offers, reopened by an independent reader and compared with what
  // was asked. The sentence says what "done" means for a creative run
  // (ADR-0093 decision 4: the comparison is the proof), that the owner's
  // original is never touched (decision 5), and that an application that is
  // not installed is said so rather than imitated (decision 3).
  creative_activity:
    "Sahip için bir görsel inceleniyor, planlanıyor, uygulamanın en yapısal arayüzüyle düzenleniyor, bağımsız bir okuyucuyla yeniden açılıp istenenle karşılaştırılıyor; uygulama, işlem, adım ve benzerlik yalnızca yayınlandığı kadar söylenir. Karşılaştırması tutmamış bir çıktı doğrulanmış sayılmaz; kurulu olmayan bir uygulama taklit edilmez, kurulu değil denir. Sahibin özgün dosyası değiştirilmez. İlerleme bildirilmez.",
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
  // v3 — the wake alarm. "Kuruldu" is a standing arrangement; "çalıyor" is a
  // noise in the room right now. They are never worded the same way.
  "alarm.armed": "Alarm kuruldu",
  "alarm.firing": "Alarm tetiklendi",
  "alarm.playing": "Alarm çalıyor",
  "alarm.greeting": "Alarm seslendiriyor",
  "alarm.snoozed": "Alarm ertelendi",
  "alarm.stopped": "Alarm durduruldu",
  "alarm.completed": "Alarm tamamlandı",
  "alarm.failed": "Alarm çalamadı",
  // v3 — display power. Never "uyku": nothing in this milestone suspends a machine.
  "display.on": "Ekranlar açık",
  "display.off": "Ekranlar kapalı",
  // v4 — the Digital Operator (M19). "Doğruluyor" is the second OBSERVE, said
  // as its own state: a step that was sent is not a step that worked.
  "operator.running": "Operatör çalışıyor",
  "operator.verifying": "Operatör doğruluyor",
  "operator.failed": "Operatör başarısız",
  // v5 — File & Document Intelligence (M20). "İnceleniyor", not "okundu": the
  // state is entered when the read starts, and nothing publishes its end.
  "document.analysis": DOCUMENT_CAPTION_BARE,
  // v6 — Mail & Calendar (M21). The plain state is a read; a draft's or a
  // proposal's step is said only from its published metadata.
  "mail.activity": MAIL_CAPTION_BARE,
  "calendar.activity": CALENDAR_CAPTION_BARE,
  // v7 — the Artifact Factory (M22). "Üretiliyor", not "üretildi": the state
  // is entered when the render starts, and a verdict is said only from its
  // published metadata.
  "artifact.factory": ARTIFACT_CAPTION_BARE,
  // v8 — the App Factory (M23). "Yapılıyor", not "çalışıyor": the token is
  // one for the whole loop, and the project's state is said only from its
  // published metadata.
  "app.factory": APP_CAPTION_BARE,
  // v9 — Capability Genesis (M24). No verb at all: the token is one for the
  // whole run, from "yetenek yok" to "doğrulandı", and the run's state is
  // said only from its published metadata.
  "capability.genesis": GENESIS_CAPTION_BARE,
  // v10 — 3D creation (M25). No verb, for v9's reason: one token covers the
  // whole loop from "sahne kuruluyor" to "doğrulandı", and the step is said
  // only from its published metadata.
  "scene.activity": SCENE_CAPTION_BARE,
  // v11 — Executive Autonomy (M26). No verb, for the same reason: one token
  // covers a run from "planlandı" to "tamamlandı", and the state is said
  // only from its published metadata.
  "executive.run": EXECUTIVE_CAPTION_BARE,
  // v12 — the Creative Tools Operator (M27). No verb, for the same reason:
  // one token covers a run from "görsel inceleniyor" to "doğrulandı", and
  // the step is said only from its published metadata.
  "creative.activity": CREATIVE_CAPTION_BARE,
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
  operator: "Operatör",
  documents: "Belgeler",
  mail: "Posta",
  calendar: "Takvim",
  artifacts: "Üretim",
  apps: "Uygulamalar",
  genesis: "Yeni yetenek",
  creative3d: "3B sahne",
  executive: "Görevler",
  creative: "Yaratıcı",
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

// ------------------------------------------------------- v3: display & alarm

/**
 * The screens, in three states and never in two.
 *
 * `untold` is not `off`, for the same reason the camera's is not: an indicator
 * that reads as "the screens are off" because nobody said anything would be a
 * statement about the owner's room that nothing supports.
 */
export const DISPLAY_LABEL: Record<DisplayState, string> = {
  on: "Ekranlar açık",
  off: "Ekranlar kapalı",
  untold: "Ekran durumu bildirilmedi",
};

export const DISPLAY_DETAIL: Record<DisplayState, string> = {
  on: "Ekranlara güç veriliyor.",
  // The one sentence this cell exists to make impossible to misread.
  off: "Yalnızca ekran gücü kapalı. Bilgisayar uyutulmadı, kilitlenmedi, kapatılmadı.",
  untold: "Ekranların açık mı kapalı mı olduğu bildirilmedi. Bu 'kapalı' demek değil.",
};

export const ALARM_LABEL: Record<AlarmStage, string> = {
  armed: "Alarm kuruldu",
  firing: "Alarm tetiklendi",
  playing: "Alarm çalıyor",
  greeting: "Alarm seslendiriyor",
  snoozed: "Alarm ertelendi",
  stopped: "Alarm durduruldu",
  completed: "Alarm tamamlandı",
  failed: "Alarm çalamadı",
  none: "Bildirilen bir alarm yok",
};

export const ALARM_DETAIL: Record<AlarmStage, string> = {
  armed: "Cihaz bu alarm için hazır. Henüz çalmıyor.",
  firing: "Alarm dizisi başladı.",
  playing: "Ses çalıyor.",
  greeting: "Karşılama cümlesi seslendiriliyor.",
  snoozed: "Ertelendi; yeni saat için tekrar kuruldu.",
  stopped: "Sahip durdurdu.",
  completed: "Süresi doldu ve kapandı.",
  failed: "Her iki ses yolu da başarısız oldu.",
  none: "Şu anda kurulu ya da çalan bir alarm bildirilmedi.",
};

/** The ramp level, as the publisher sent it — or the fact that it did not. */
export function formatAlarmLevel(level: number | null): string {
  if (level === null) return "ses seviyesi bildirilmedi";
  return `ses seviyesi %${Math.round(level * 100)}`;
}

/**
 * What each contract version added, in the owner's words, so the lag note can
 * name exactly the families an older server will never publish.
 */
const CONTRACT_ADDITIONS: Record<number, string> = {
  3: "alarm ve ekran durumları",
  4: "dijital operatör durumları",
  5: "belge inceleme durumu",
  6: "posta ve takvim durumları",
  7: "dosya üretim durumu",
  8: "uygulama üretim durumu",
  9: "yeni yetenek durumu",
  10: "3B sahne durumu",
  11: "çok adımlı iş durumu",
  12: "görsel çalışması durumu",
};

/**
 * What the server's contract version means for what the owner will see.
 *
 * A v2 Cloud Core simply never publishes the alarm or display states, a v3
 * one never the operator's, and the absence of a row would otherwise read as
 * "no alarm is set" or "the operator never ran". The note names the families
 * between the server's version and this build's, and nothing more.
 */
export function contractLagNote(serverVersion: number, knownVersion: number): string {
  const missing: string[] = [];
  for (let v = serverVersion + 1; v <= knownVersion; v += 1) {
    const family = CONTRACT_ADDITIONS[v];
    if (family) missing.push(family);
  }
  const families = missing.length ? missing.join(", ") : "bu sürümün yeni durumları";
  const sentence = families.charAt(0).toLocaleUpperCase("tr-TR") + families.slice(1);
  return `Sunucu durum sözleşmesi v${serverVersion}; bu arayüz v${knownVersion}. ${sentence} bu sunucudan henüz yayınlanmıyor — yok demek değil.`;
}

// ------------------------------------------------ v4: the Digital Operator

export const OPERATOR_LABEL: Record<OperatorStage, string> = {
  running: "Operatör çalışıyor",
  verifying: "Operatör doğruluyor",
  failed: "Operatör başarısız",
  none: "Süren bir operatör görevi yok",
};

/** The panel's empty sentence: nothing was ever published, which is not "idle". */
export const OPERATOR_EMPTY = "Operatör henüz çalışmadı.";

/**
 * The published facts on one line, each one either the token the publisher
 * sent or the statement that it did not send one. "Pencere bildirilmedi" is
 * a real answer: the companion observed no window for this step, or the
 * publisher did not say — and the difference from a title is the difference
 * between a verified target and none.
 */
export function operatorFactsLine(facts: OperatorFacts): string {
  return [
    operatorStepPhrase(facts),
    facts.capability ? `yetenek: ${facts.capability}` : "yetenek bildirilmedi",
    facts.windowTitle ? `pencere: ${facts.windowTitle}` : "pencere bildirilmedi",
  ].join(" · ");
}

/**
 * The step as the publisher named it: "adım 1/3: open_notepad" when it sent
 * both the name and the place, "adım: open_notepad" for the name alone,
 * "adım 1/3" for the place alone, and the statement that none came.
 */
export function operatorStepPhrase(facts: Pick<OperatorFacts, "step" | "stepIndex" | "stepCount">): string {
  const position = operatorPosition(facts);
  if (facts.step && position) return `adım ${position}: ${facts.step}`;
  if (facts.step) return `adım: ${facts.step}`;
  if (position) return `adım ${position}`;
  return "adım bildirilmedi";
}

/** The failure's class as published, or the fact that none was. */
export function operatorErrorLine(errorClass: string | null): string {
  return errorClass ? `hata sınıfı: ${errorClass}` : "hata sınıfı bildirilmedi";
}

// ------------------------------------------- v5: File & Document Intelligence

export const DOCUMENT_LABEL: Record<DocumentStage, string> = {
  analysing: DOCUMENT_CAPTION_BARE,
  none: "İncelenen bir belge yok",
};

/** The panel's empty sentence: no document was ever published, which is not "no documents". */
export const DOCUMENT_EMPTY = "Henüz bir belge okunmadı.";

/**
 * The published facts on one line, each one either what the publisher sent
 * or the statement that it did not send it. "Yer bildirilmedi" is a real
 * answer: a search or an inspect names a file and no place inside it.
 */
export function documentFactsLine(facts: DocumentFacts): string {
  const place = documentPartPhrase(facts.part, facts.kind);
  const step = documentStepLabel(facts.step);
  return [
    facts.file ? `dosya: ${facts.file}` : "dosya bildirilmedi",
    place ? `yer: ${place}` : "yer bildirilmedi",
    step ? `adım: ${step}` : "adım bildirilmedi",
  ].join(" · ");
}

/**
 * One cited reference as "dosya · yer · alıntı": the path the answer named
 * (or the fact that it named none), the place in the owner's words — the
 * kind read from the cited path, since the ref is about THAT file — and the
 * excerpt the answer rests on, or the fact that none came with it.
 */
export function documentRefLine(ref: DocumentRef): string {
  return [
    ref.path ?? "dosya bildirilmedi",
    documentPartPhrase(ref.ref, refKind(ref)) ?? ref.ref,
    ref.excerpt ?? "alıntı bildirilmedi",
  ].join(" · ");
}

// ------------------------------------------------------- v6: Mail & Calendar

export const MAIL_LABEL: Record<MailStage, string> = {
  active: MAIL_CAPTION_BARE,
  none: "Süren bir posta işi yok",
};

/** The Posta panel's empty sentence: the pending route answered, and holds no draft. */
export const MAIL_EMPTY = "Bekleyen taslak yok.";

/** The Posta panel's line when the bus never carried a mail event: not "no mail", "nothing reported". */
export const MAIL_UNTOLD = "Posta etkinliği bildirilmedi.";

/**
 * The published mail facts on one line, each one either what the publisher
 * sent or the statement that it did not send it. "Taslak bildirilmedi" is a
 * real answer: a read of the inbox names no draft.
 */
export function mailFactsLine(facts: MailFacts): string {
  const folder = mailFolderPhrase(facts.folder);
  const draft =
    facts.draftState !== null
      ? MAIL_DRAFT_STATE_LABEL[facts.draftState]
      : facts.draftStateToken;
  return [
    folder ? `klasör: ${folder}` : "klasör bildirilmedi",
    facts.subject ? `konu: ${facts.subject}` : "konu bildirilmedi",
    draft ? `taslak: ${draft}` : "taslak bildirilmedi",
  ].join(" · ");
}

export const CALENDAR_LABEL: Record<CalendarStage, string> = {
  active: CALENDAR_CAPTION_BARE,
  none: "Süren bir takvim işi yok",
};

/** The Takvim panel's empty sentence for today: the bus carried no event for today. */
export const CALENDAR_EMPTY = "Bugün için kayıt yok.";

/** The Takvim panel's empty sentence for the pending route: it answered, and holds no proposal. */
export const CALENDAR_NO_PROPOSAL = "Bekleyen öneri yok.";

/** The Takvim panel's line when the bus never carried a calendar event. */
export const CALENDAR_UNTOLD = "Takvim etkinliği bildirilmedi.";

/**
 * The published calendar facts on one line. The conflicts figure is printed
 * only beside a proposal: a plain agenda read has nothing to collide with,
 * and "çakışma bildirilmedi" there would be noise rather than a fact.
 */
export function calendarFactsLine(facts: CalendarFacts): string {
  const range = calendarRangePhrase(facts.range);
  const proposal =
    facts.proposalState !== null
      ? CALENDAR_PROPOSAL_STATE_LABEL[facts.proposalState]
      : facts.proposalStateToken;
  const parts = [
    range ? `aralık: ${range}` : "aralık bildirilmedi",
    facts.event ? `etkinlik: ${facts.event}` : "etkinlik bildirilmedi",
    proposal ? `öneri: ${proposal}` : "öneri bildirilmedi",
  ];
  if (proposal) parts.push(conflictsPhrase(facts.conflicts) ?? "çakışma bildirilmedi");
  return parts.join(" · ");
}

// ------------------------------------------------------- v7: the Artifact Factory

export const ARTIFACT_LABEL: Record<ArtifactStage, string> = {
  making: ARTIFACT_CAPTION_BARE,
  none: "Süren bir üretim yok",
};

/** The Üretilenler panel's empty sentence: the list route answered, and holds no artifact. */
export const ARTIFACT_EMPTY = "Henüz bir şey üretilmedi.";

/** The Üretilenler panel's line when the bus never carried a factory event: not "nothing made", "nothing reported". */
export const ARTIFACT_UNTOLD = "Üretim etkinliği bildirilmedi.";

/**
 * A render the list route holds with no validation state at all — an M13
 * render made before the factory validated anything, or a row the route
 * does not describe. Not "doğrulanamadı": nobody said it failed; and never
 * "doğrulandı": nobody said it passed.
 */
export const ARTIFACT_RENDER_UNVALIDATED = "doğrulama bildirilmedi";

/**
 * A verdict token as one word: the spec's word for the three this build
 * knows, the token verbatim for one it does not (still a published fact),
 * and the statement that none came.
 */
export function artifactVerdictWord(token: string | null): string {
  if (token === null) return ARTIFACT_RENDER_UNVALIDATED;
  return (ARTIFACT_VERDICT_LABEL as Record<string, string>)[token] ?? token;
}

/**
 * The published factory facts on one line, each one either what the
 * publisher sent or the statement that it did not send it. The failing ref
 * is printed only beside an `invalid` verdict: a valid render has no place
 * that failed, and "yer bildirilmedi" there would be noise rather than a fact.
 */
export function artifactFactsLine(facts: ArtifactFacts): string {
  const format = artifactFormatLabel(facts.format);
  const parts = [
    facts.title ? `başlık: ${facts.title}` : "başlık bildirilmedi",
    format ? `biçim: ${format}` : "biçim bildirilmedi",
    facts.verdictToken ? `sonuç: ${artifactVerdictWord(facts.verdictToken)}` : "sonuç bildirilmedi",
  ];
  if (facts.verdict === "invalid") parts.push(facts.failingRef ? `yer: ${facts.failingRef}` : "yer bildirilmedi");
  return parts.join(" · ");
}

// ------------------------------------------------------- v8: the App Factory

export const APP_LABEL: Record<AppStage, string> = {
  active: APP_CAPTION_BARE,
  none: "Süren bir uygulama işi yok",
};

/** The Uygulamalar panel's empty sentence: the list route answered, and holds no project. */
export const APP_EMPTY = "Henüz bir uygulama yapılmadı.";

/** The Uygulamalar panel's line when the bus never carried an app event: not "no apps", "nothing reported". */
export const APP_UNTOLD = "Uygulama etkinliği bildirilmedi.";

/**
 * A project state token as one word: the spec's word for the six this
 * build knows, the token verbatim for one it does not (still a published
 * fact), and the statement that none came.
 */
export function appStateWord(token: string | null): string {
  if (token === null) return "durum bildirilmedi";
  return (APP_STATE_LABEL as Record<string, string>)[token] ?? token;
}

/**
 * The published app facts on one line, each one either what the publisher
 * sent or the statement that it did not send it. The port is printed
 * whenever one was sent and its absence is said only beside `running` — a
 * scaffolded project has no port to report, and "port bildirilmedi" there
 * would be noise rather than a fact. The counts likewise: printed whenever
 * sent, their absence said only beside `tested` or `failed`.
 */
export function appFactsLine(facts: AppFacts): string {
  const parts = [
    facts.project ? `proje: ${facts.project}` : "proje bildirilmedi",
    facts.stateToken ? `durum: ${appStateWord(facts.stateToken)}` : "durum bildirilmedi",
  ];
  if (facts.port !== null) parts.push(`port: ${facts.port}`);
  else if (facts.state === "running") parts.push("port bildirilmedi");
  const tests = appTestsPhrase(facts.tests);
  if (tests) parts.push(`testler: ${tests}`);
  else if (facts.state === "tested" || facts.state === "failed") parts.push("test sayısı bildirilmedi");
  return parts.join(" · ");
}

// ------------------------------------------------------- v9: Capability Genesis

export const GENESIS_LABEL: Record<GenesisStage, string> = {
  active: GENESIS_CAPTION_BARE,
  none: "Süren bir yetenek edinimi yok",
};

/** The Yeni Yetenek panel's empty sentence (M24 spec §8): the list route answered, and holds no run. */
export const GENESIS_EMPTY = "Henüz yeni bir yetenek istenmedi.";

/** The Yeni Yetenek panel's line when the bus never carried a genesis event: not "no runs", "nothing reported". */
export const GENESIS_UNTOLD = "Yeni yetenek etkinliği bildirilmedi.";

/** Said beside `failed` when the publisher named no class: a failure whose class nobody published. */
export const GENESIS_ERROR_CLASS_UNTOLD = "hata sınıfı bildirilmedi";

/**
 * A run state token as one word: the spec's word for the thirteen this
 * build knows, the token verbatim for one it does not (still a published
 * fact), and the statement that none came.
 */
export function genesisStateWord(token: string | null): string {
  if (token === null) return "durum bildirilmedi";
  return (GENESIS_STATE_LABEL as Record<string, string>)[token] ?? token;
}

/**
 * The published genesis facts on one line, each one either what the
 * publisher sent or the statement that it did not send it. The approval
 * flag is printed whenever one was sent ("onay gerekli" / "onay
 * gerekmiyor") and its absence is never said: a flag nobody published is
 * not a fact either way. The error class is printed beside `failed` only,
 * and its absence is said only there — a verified run has no class to
 * name, and "hata sınıfı bildirilmedi" beside it would be noise.
 */
export function genesisFactsLine(facts: GenesisFacts): string {
  const parts = [
    facts.capability ? `yetenek: ${facts.capability}` : "yetenek bildirilmedi",
    facts.stateToken ? `durum: ${facts.state === "failed" ? GENESIS_STATE_LABEL.failed : genesisStateWord(facts.stateToken)}` : "durum bildirilmedi",
  ];
  if (facts.approvalRequired !== null) parts.push(facts.approvalRequired ? "onay gerekli" : "onay gerekmiyor");
  if (facts.state === "failed") parts.push(facts.errorClass ? `hata: ${facts.errorClass}` : GENESIS_ERROR_CLASS_UNTOLD);
  return parts.join(" · ");
}

/** The state with its failure class, for a row or a caption, or the statement that none came. */
export function genesisStateLine(facts: Pick<GenesisFacts, "state" | "stateToken" | "errorClass">): string {
  return genesisStatePhrase(facts) ?? genesisStateWord(facts.stateToken);
}

// -------------------------------------------------------- v10: 3D creation

export const SCENE_LABEL: Record<SceneStage, string> = {
  active: SCENE_CAPTION_BARE,
  none: "Süren bir 3B sahne işi yok",
};

/** The 3B Sahne panel's empty sentence (M25 spec §6): the list route answered, and holds no scene. */
export const SCENE_EMPTY = "Henüz bir sahne yapılmadı.";

/** The 3B Sahne panel's line when the bus never carried a scene event: not "no scenes", "nothing reported". */
export const SCENE_UNTOLD = "3B sahne etkinliği bildirilmedi.";

/** Said where a scene's object count was never read back: not "0 nesne", which nobody counted. */
export const SCENE_OBJECTS_UNTOLD = "nesne sayısı bildirilmedi";

/** Said where an inspection named no objects at all: nothing was listed, which is not "no objects". */
export const SCENE_NO_OBJECT_NAMES = "nesne adı bildirilmedi";

/**
 * A scene step token as one word: the spec's word for the eight this build
 * knows, the token verbatim for one it does not (still a published fact),
 * and the statement that none came.
 */
export function sceneStateWord(token: string | null): string {
  if (token === null) return "durum bildirilmedi";
  return (SCENE_STATE_LABEL as Record<string, string>)[token] ?? token;
}

/**
 * The published scene facts on one line, each one either what the publisher
 * sent or the statement that it did not send it. The object count is
 * printed whenever the inspection counted, and its absence said only beside
 * the two states that rest on a read-back (`verified`, `mismatch`) — a
 * scene that is still rendering has nothing read back yet, and "nesne
 * sayısı bildirilmedi" there would be noise rather than a fact.
 */
export function sceneFactsLine(facts: SceneFacts): string {
  const tool = sceneToolWord(facts.toolToken);
  const parts = [
    tool ? `araç: ${tool}` : "araç bildirilmedi",
    facts.scene ? `sahne: ${facts.scene}` : "sahne bildirilmedi",
    facts.stateToken ? `durum: ${sceneStateWord(facts.stateToken)}` : "durum bildirilmedi",
  ];
  if (facts.objects !== null) parts.push(`nesneler: ${facts.objects}`);
  else if (facts.state === "verified" || facts.state === "mismatch") parts.push(SCENE_OBJECTS_UNTOLD);
  return parts.join(" · ");
}

/** The step with everything the publisher attached to it, for a row or a caption, or the statement that none came. */
export function sceneStateLine(
  facts: Pick<SceneFacts, "state" | "stateToken" | "tool" | "objects">,
  object: string | null = null,
): string {
  return sceneStatePhrase(facts, object) ?? sceneStateWord(facts.stateToken);
}

// -------------------------------------------------- v11: Executive Autonomy

export const EXECUTIVE_LABEL: Record<ExecutiveStage, string> = {
  active: EXECUTIVE_CAPTION_BARE,
  none: "Süren bir iş bildirilmedi",
};

/**
 * The Görevler panel's empty sentence — the spec's own words (M26 §6): the
 * list route answered, and holds no run.
 */
export const EXECUTIVE_EMPTY = "Devam eden bir iş yok.";

/** The Görevler panel's line when the bus never carried a run event: not "no runs", "nothing reported". */
export const EXECUTIVE_UNTOLD = "Çok adımlı iş etkinliği bildirilmedi.";

/** Said beside a partial run whose step counts nobody published: the one state where "how much got done" is the question. */
export const EXECUTIVE_STEPS_UNTOLD = "adım sayısı bildirilmedi";

/** Said for a partial run whose missing steps the route did not name: what is missing is unknown, not nothing. */
export const EXECUTIVE_MISSING_UNTOLD = "eksik adımlar bildirilmedi";

/**
 * A run state token as one word: the spec's word for the seven this build
 * knows, the token verbatim for one it does not (still a published fact),
 * and the statement that none came.
 */
export function executiveStateWord(token: string | null): string {
  if (token === null) return "durum bildirilmedi";
  return (EXECUTIVE_RUN_STATE_LABEL as Record<string, string>)[token] ?? token;
}

/**
 * The published run facts on one line, each one either what the publisher
 * sent or the statement that it did not send it: "iş: r1 · adım: s3 ·
 * durum: çalışıyor · 3/5 adım". The counts are printed only when BOTH came,
 * and their absence is said only beside `partial` — the one state whose
 * whole meaning is how much of the job exists. A `running` that counted
 * nothing has nothing to say about totals, and saying so there would be
 * noise rather than a fact.
 */
export function executiveFactsLine(facts: ExecutiveFacts): string {
  const parts = [
    facts.run ? `iş: ${facts.run}` : "iş bildirilmedi",
    facts.step ? `adım: ${facts.step}` : "adım bildirilmedi",
    facts.stateToken ? `durum: ${executiveStateWord(facts.stateToken)}` : "durum bildirilmedi",
  ];
  const steps = executiveStepsPhrase(facts.done, facts.total);
  if (steps) parts.push(steps);
  else if (facts.state === "partial") parts.push(EXECUTIVE_STEPS_UNTOLD);
  return parts.join(" · ");
}

/** The state with what the publisher attached to it, for a row or a caption, or the statement that none came. */
export function executiveStateLine(
  facts: Pick<ExecutiveFacts, "state" | "stateToken">,
  missing: readonly string[] = [],
): string {
  return executiveStatePhrase(facts, missing) ?? executiveStateWord(facts.stateToken);
}

// ------------------------------------------ v12: the Creative Tools Operator

export const CREATIVE_LABEL: Record<CreativeStage, string> = {
  active: CREATIVE_CAPTION_BARE,
  none: "Süren bir görsel çalışması yok",
};

/** The Yaratıcı panel's empty sentence (M27 spec §6): the list route answered, and holds no run. */
export const CREATIVE_EMPTY = "Henüz bir görsel çalışması yapılmadı.";

/** The Yaratıcı panel's line when the bus never carried a creative event: not "no runs", "nothing reported". */
export const CREATIVE_UNTOLD = "Görsel çalışması etkinliği bildirilmedi.";

/** Said where a comparison's figure was never measured: not "%0", which nobody measured. */
export const CREATIVE_SIMILARITY_UNTOLD = "benzerlik ölçülmedi";

/** Said where a mismatch named no defect: what disagreed is unknown, which is not "nothing did". */
export const CREATIVE_DEFECT_UNTOLD = "kusur bildirilmedi";

/**
 * A creative step token as one word: the spec's word for the twelve this
 * build knows, the token verbatim for one it does not (still a published
 * fact), and the statement that none came.
 */
export function creativeStateWord(token: string | null): string {
  if (token === null) return "durum bildirilmedi";
  return (CREATIVE_STATE_LABEL as Record<string, string>)[token] ?? token;
}

/**
 * The published creative facts on one line, each one either what the
 * publisher sent or the statement that it did not send it: "uygulama: Paint ·
 * işlem: çizim · durum: doğrulandı · (benzerlik %92)". The similarity is
 * printed whenever a comparison measured one, and its absence said only
 * beside the two states that REST on a completed comparison (`verified`,
 * `mismatch`) — a run that is still executing has measured nothing yet, and
 * "benzerlik ölçülmedi" there would be noise rather than a fact.
 */
export function creativeFactsLine(facts: CreativeFacts): string {
  const tool = creativeToolWord(facts.toolToken);
  const operation = creativeOperationWord(facts.operationToken);
  const parts = [
    tool ? `uygulama: ${tool}` : "uygulama bildirilmedi",
    operation ? `işlem: ${operation}` : "işlem bildirilmedi",
    facts.stateToken ? `durum: ${creativeStateWord(facts.stateToken)}` : "durum bildirilmedi",
  ];
  const similarity = creativeSimilarityPhrase(facts.similarity);
  if (similarity) parts.push(similarity);
  else if (facts.state === "verified" || facts.state === "mismatch") parts.push(CREATIVE_SIMILARITY_UNTOLD);
  return parts.join(" · ");
}

/** The step with everything the publisher attached to it, for a row or a caption, or the statement that none came. */
export function creativeStateLine(
  facts: Pick<CreativeFacts, "state" | "stateToken" | "tool" | "similarity" | "defectToken">,
  defect: string | null = null,
): string {
  return creativeStatePhrase(facts, defect) ?? creativeStateWord(facts.stateToken);
}
