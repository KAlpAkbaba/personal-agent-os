"""B22 req 704: one Turkish dictionary for every error class this system can produce.

Eight subsystems declare their own error taxonomy — `app.voice.errors`, `app.memory.errors`,
`app.mobile.errors`, `app.evolution.errors`, `app.release.errors`, `app.security.errors`,
`app.selfhealing.errors`, `app.identity.errors` — fifty-six distinct classes between them,
and not one of them said anything in Turkish. Every surface that showed a failure therefore
showed either the class token (`provider_auth_missing`) or, worse, the exception's own
English text ("no matching weekday within a week — refusing to guess"). The owner of this
system does not read Python.

**Two sentences, never one.** Each entry says WHAT happened and WHAT CAN BE DONE, because a
failure the owner cannot act on is a failure they will ask about twice. Where nothing can be
done by them, the remedy says so plainly rather than inventing a suggestion.

**An unmapped class is named, not guessed.** `describe()` falls back to a sentence that
carries the class TOKEN and nothing else. A token is honest — it is what the log will say
too — while a generic "bir hata oluştu" would hide which failure this was, and an invented
Turkish sentence for an unknown class would be worse than either.
`test_every_error_class_has_turkish` keeps the map complete in both directions, so the
fallback is a safety net rather than the normal path.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What the owner is told when a class has no entry. Deliberately unhelpful about CAUSE and
#: precise about identity: the token is what a search of the logs will match.
UNKNOWN_WHAT = "Beklenmedik bir sorun oldu"
UNKNOWN_REMEDY = "Kaydı tuttum; sorarsan ne olduğunu anlatırım."


@dataclass(frozen=True, slots=True)
class OwnerMessage:
    """One failure, in the owner's language."""

    #: What happened, as a sentence with no full stop (the caller composes).
    what: str
    #: What can be done about it, or "" when nothing by the owner.
    remedy: str = ""

    def sentence(self) -> str:
        base = self.what.rstrip(".")
        return f"{base}." if not self.remedy else f"{base}. {self.remedy}"


def _m(what: str, remedy: str = "") -> OwnerMessage:
    return OwnerMessage(what=what, remedy=remedy)


#: class token -> Turkish. Sorted by token so a reader can find one, and so a diff that adds
#: a class is one line in an obvious place.
TR: dict[str, OwnerMessage] = {
    "all_providers_failed": _m(
        "Tanımlı sağlayıcıların hepsi denendi ve hiçbiri yanıt vermedi",
        "Biraz sonra yeniden denenebilir.",
    ),
    "already_enrolled": _m("Bu varlık zaten kayıtlı", "Yeniden kaydetmeye gerek yok."),
    "backend_not_configured": _m(
        "Bu işi yapacak arka uç yapılandırılmamış",
        "Yapılandırmayı yalnızca sen ekleyebilirsin.",
    ),
    "backend_not_implemented": _m(
        "Bu depolama arka ucu bu kurulumda yok", "Varsayılan arka uçla çalışmaya devam ediyorum."
    ),
    "capability_missing": _m(
        "Bu işi yapabilecek bir yetenek bulamadım",
        "Gereken cihaz ya da sağlayıcı bağlandığında kendiliğinden çalışır.",
    ),
    "collector_root_violation": _m(
        "Toplayıcı, izin verilen dizinin dışına çıkmaya çalıştı", "İsteği reddettim."
    ),
    "constraint_violation": _m("İstek bir güvenlik kuralına takıldı", "Kuralı esnetmedim."),
    "dependency_unavailable": _m(
        "Gereken bir servise ulaşamadım", "Servis yanıt vermeye başlayınca tekrar denenebilir."
    ),
    "deploy_failed": _m("Yayına alma tamamlanamadı", "Sürüm değişmedi; eski hâli çalışıyor."),
    "dispatch_failed": _m("İş, çalıştıracak yere iletilemedi", "Yeniden denenebilir."),
    "empty_result": _m(
        "İşi yaptım ama sana verebileceğim bir sonuç çıkmadı",
        "Bunu başarı gibi sunmuyorum; istersen farklı bir kapsamla bakabilirim.",
    ),
    "evaluation_failed": _m("Değerlendirme adımı tamamlanamadı", "Aday yükseltilmedi."),
    "expired": _m("Oturumun süresi dolmuş", "Yeniden giriş yapman gerekiyor."),
    "explicit_protected": _m(
        "Bu kaydı sen açıkça yazdırmıştın; kendiliğinden değiştirmiyorum",
        "Değişmesini istiyorsan bana söylemen yeter.",
    ),
    "generation_failed": _m("Kod üretimi tamamlanamadı", "Değişiklik uygulanmadı."),
    "generation_refused": _m(
        "Bu değişikliği üretmeyi reddettim", "Gerekçesini kayıtlarda tutuyorum."
    ),
    "generator_not_configured": _m(
        "Kod üretimi için bir sağlayıcı tanımlı değil",
        "Anahtarı yalnızca sen ekleyebilirsin (scripts/secret-store.ps1).",
    ),
    "idle_timeout": _m(
        "Oturum uzun süre kullanılmadığı için kapandı", "Yeniden giriş yapabilirsin."
    ),
    "internal_bug": _m(
        "Bende bir hata var",
        "Kaydını aldım; kendi kendime düzeltmeye çalışacağım ve sonucu söylerim.",
    ),
    "lifecycle_violation": _m(
        "Bu adım şu anki durumda yapılamaz", "Sıradaki adıma geçince tekrar denenebilir."
    ),
    "malformed": _m("Gelen kimlik bilgisi okunamadı", "Yeniden giriş yapman gerekiyor."),
    "not_bootstrapped": _m(
        "Sistem henüz ilk kurulumunu yapmamış", "İlk sahip kimliğini oluşturman gerekiyor."
    ),
    "not_found": _m("Aradığın kaydı bulamadım", "Adını ya da kimliğini birlikte doğrulayabiliriz."),
    "not_superior": _m("Yeni aday, mevcut olandan daha iyi çıkmadı", "Bu yüzden değiştirmedim."),
    "optional_dependency_missing": _m(
        "Bu iş için isteğe bağlı bir bileşen kurulu değil", "Kurulunca kendiliğinden çalışır."
    ),
    "out_of_scope": _m(
        "Bu hedef, izin verdiğin kapsamın dışında", "Kapsamı yalnızca sen genişletebilirsin."
    ),
    "patch_derivation_failed": _m(
        "Hatanın düzeltmesini çıkaramadım", "Kaydı bırakıyorum; elle bakılması gerekebilir."
    ),
    "payload_too_large": _m("Gönderilen içerik fazla büyük", "Daha küçük bir parça denenebilir."),
    "permission_denied": _m("Bu işlem için yetkim yok", "İzni yalnızca sen verebilirsin."),
    "pipeline_stage_failed": _m(
        "Onarım hattının bir adımı başarısız oldu", "Hangi adım olduğunu kayıtlarda tutuyorum."
    ),
    "postcondition_failed": _m(
        "İşi yaptım ama sonucu doğrulayamadım", "Yapıldı demiyorum; doğrulanana kadar açık kalıyor."
    ),
    "preflight_refused": _m(
        "Yayın öncesi denetim geçilmedi", "Yayına almadım; eksikleri kayıtlarda tutuyorum."
    ),
    "product_change_required": _m(
        "Bu, kendi başıma yapabileceğim bir değişiklik değil",
        "Ürün kararı gerekiyor; not aldım.",
    ),
    "provider_auth_missing": _m(
        "Sağlayıcının anahtarı tanımlı değil",
        "Anahtarı yalnızca sen ekleyebilirsin (scripts/secret-store.ps1).",
    ),
    "push_token_invalid": _m(
        "Bildirim adresi geçersiz", "Uygulamayı bir kez açman yeni adresi kaydeder."
    ),
    "rate_limited": _m("Çok sık denendi", "Kısa bir süre sonra tekrar denenebilir."),
    "recursion_limit_exceeded": _m(
        "Kendi kendini çağıran bir zincir sınırı aştı", "Zinciri durdurdum."
    ),
    "registration_refused": _m("Kayıt isteği reddedildi", "Gerekçesi kayıtlarda."),
    "remediation_not_automatable": _m(
        "Bu bulguyu kendi başıma kapatamam", "Ne yapılması gerektiğini anlatabilirim."
    ),
    "reproduction_failed": _m(
        "Hatayı yeniden üretemedim", "Üretemediğim bir hatayı düzelttim demiyorum."
    ),
    "resource_budget_exceeded": _m(
        "Ayrılan kaynak bütçesi doldu", "İşi durdurdum; bütçeyi sen artırabilirsin."
    ),
    "review_rejected": _m("Değişiklik incelemeden geçemedi", "Uygulamadım."),
    "revoked": _m("Bu oturum iptal edilmiş", "Yeniden giriş yapman gerekiyor."),
    "rollback_failed": _m(
        "Geri alma tamamlanamadı",
        "Bu, elle bakılması gereken bir durum; son iyi sürümün bilgisi kayıtlarda.",
    ),
    "sandbox_violation": _m(
        "Üretilen kod izin verilmeyen bir şeye erişmeye çalıştı", "Çalıştırmadım."
    ),
    "scope_missing": _m("Bu oturumun bu iş için yetkisi yok", "Yetkiyi sen verebilirsin."),
    "secret_rejected": _m(
        "Bu, parola ya da anahtar gibi görünüyor; kaydetmedim",
        "Sırlar bellekte değil, sır deposunda durur.",
    ),
    "session_revoked": _m("Oturum iptal edilmiş", "Yeniden giriş yapman gerekiyor."),
    "supply_chain_rejected": _m("Bağımlılık zinciri denetimi geçilmedi", "Paketi almadım."),
    # B36 (req 579/680): the security gate on generated code.
    "security_refused": _m(
        "Üretilen kod güvenlik kapısından geçemedi", "Kaydetmedim; bulgular kayıtta."
    ),
    "target_unavailable": _m("Hedefe ulaşamadım", "Erişilebilir olunca tekrar denenebilir."),
    "throttled": _m("Arka arkaya çok deneme oldu", "Kısa bir süre bekleyip tekrar dene."),
    "timeout": _m("İşlem verilen sürede bitmedi", "Yeniden denenebilir."),
    "unavailable": _m("Kimlik servisi şu anda yanıt vermiyor", "Biraz sonra tekrar dene."),
    "unknown": _m("Bu kimliği tanımıyorum", "Yeniden giriş yapman gerekiyor."),
    "validation_error": _m(
        "İsteği anlayamadım", "Neyi kastettiğini bir cümleyle söylersen düzeltirim."
    ),
    "verification_failed": _m(
        "Yayın sonrası doğrulama geçilmedi", "Sürümü geri aldım; ayrıntı kayıtlarda."
    ),
    # ---------------------------------------------------------------------------------
    # The other half of the taxonomy. Not every subsystem declares an Enum: news,
    # research, artifacts, the device action path and the operator use module-level
    # `ERROR_*` string constants, and they reach the owner through exactly the same
    # surfaces. A dictionary that covered only the enums would have been "one dictionary"
    # in name and two in fact - and the completeness test reads BOTH sources, so this half
    # cannot drift either.
    "already_exists": _m("Bu zaten var", "Yenisini oluşturmadım."),
    "artifact_gone": _m("Belge artık yok", "Silinmiş olabilir; listeden birlikte bakabiliriz."),
    # B34 (req 153-167, 674): the managed file mutations.
    "mutation_disabled": _m("Dosya değiştirme bu sunucuda kapalı", "Hiçbir şeye dokunmadım."),
    "mutation_unverified": _m(
        "Değişiklik doğrulanamadı", "Cihaz sonucu okuyamadı; dosya olduğu gibi duruyor."
    ),
    "not_text": _m("Bu bir metin dosyası değil", "Yalnız metin dosyaları düzenlenir."),
    "nothing_pending": _m("Bekleyen bir değişiklik yok", "Önce ne yapacağımı söyleyin."),
    "nothing_to_undo": _m("Geri alınacak bir değişiklik yok", ""),
    # B35 (req 581-623, 680): the self-development queue.
    "selfdev_disabled": _m("Kendini geliştirme bu sunucuda kapalı", "Kuyruğa bir şey almadım."),
    "defect_not_found": _m("Böyle bir kusur kaydı yok", "Listeden birlikte bakabiliriz."),
    # B40 (req 422-439): the composed App Factory and its fix loop.
    "clarification_needed": _m("Ne istediğinizi tam anlayamadım", "Biraz daha ayrıntı verin."),
    "lint_failed": _m("Üretilen kod yapısal denetimden geçmedi", "Dosya ve satır makbuzda."),
    "no_model": _m(
        "Kod modeli yapılandırılmamış", "Analiz raporlandı; düzeltme için model anahtarı gerekir."
    ),
    "same_failure": _m("Aynı testler ikinci kez başarısız", "Döngü durdu; rapor makbuzda."),
    "exhausted": _m("Düzeltme denemeleri bitti", "Rapor makbuzda; elle bakılabilir."),
    # B41 (req 440-452): the generated application's lifecycle.
    "release_corrupt": _m(
        "Sürüm paketi kendi kaydıyla uyuşmuyor", "Çalıştırmadım; paketi yeniden üretebiliriz."
    ),
    "nothing_to_add": _m(
        "İsteği kayıt türü, alan ya da giriş olarak okuyamadım",
        "Ne ekleyeceğimizi tek cümleyle söyleyin.",
    ),
    "model_refused": _m(
        "Model bu isteği de yazamadı", "İsteği kayıt türü ya da alan olarak söyleyin."
    ),
    # B42 (req 410-416): the artifact lifecycle.
    "invalid_edit": _m(
        "Düzenleme artefaktın kurallarına uymadı", "Neyi değiştireceğinizi tek tek söyleyin."
    ),
    "archived": _m("Bu artefakt silinmiş", "Kaydı duruyor; kopyası yeniden üretilebilir."),
    "delete_denied": _m(
        "Silme politikanız buna izin vermiyor", "Politikayı ayarlardan değiştirebilirsiniz."
    ),
    "no_previous_version": _m("Karşılaştırılacak önceki sürüm yok", "Önce bir düzenleme yapın."),
    "invalid_policy": _m("Silme politikası tanınmadı", "Onaylı, kapalı ya da serbest olabilir."),
    "image_not_editable": _m(
        "Görsel artefakt burada düzenlenmez", "Yaratıcı araçla yeni bir sürüm üretilebilir."
    ),
    "object_missing": _m("Kaynak görsel depoda yok", "Önce üretimin bittiğinden emin olalım."),
    # B43 (req 489-512): the creative lifecycle.
    "provider_not_configured": _m(
        "Görsel üretimi için bir sağlayıcı tanımlı değil", "Ayarlardan yalnızca sen seçebilirsin."
    ),
    "provider_failed": _m(
        "Görsel sağlayıcı bu isteği yapamadı", "Biraz sonra yeniden deneyebiliriz."
    ),
    "nothing_to_redo": _m("Yinelenecek bir adım yok", "En son hâlindeyiz."),
    "no_output": _m("Teslim edilecek bir çıktı yok", "Önce bir görsel üretelim."),
    "drive_failed": _m(
        "Uygulama sürülürken bir adım takıldı",
        "Adımı adıyla söyledim; kaldığı yerden devam edebiliriz.",
    ),
    "drive_unsupported": _m("Bu uygulama bu adımı süremez", "Desteklenen adımları sayabilirim."),
    # B45 (req 347, 348): attachments.
    "attachment_not_found": _m(
        "Bu mailde o sırada bir ek yok", "Ekleri listeleyip numarasını söyleyebilirsiniz."
    ),
    "attachment_too_large": _m(
        "Bu ek indirmek için fazla büyük", "Mail istemcinizden kaydedebilirsiniz."
    ),
    "attachment_delivery_failed": _m(
        "Ek bilgisayarınıza indirilemedi", "Cihaz bağlıyken yeniden deneyebiliriz."
    ),
    # B46 (req 356, 354): calendar recurrence and series.
    "invalid_recurrence": _m(
        "Bu tekrar ya da hatırlatma anlaşılamadı",
        "Daha basit söyleyebilirsiniz: her hafta pazartesi gibi.",
    ),
    "recurring_series": _m(
        "Tekrarlayan bir etkinliğin tek bir tekrarı buradan değiştirilemez",
        "Takviminizden değiştirebilirsiniz.",
    ),
    # B49 (req 479): iOS without macOS.
    "platform_unreachable": _m(
        "Bu bilgisayarlarda iOS uygulaması derlenemez: macOS ve Xcode gerekir",
        "Windows ya da Android için yapabilirim.",
    ),
    "not_awaiting_owner": _m(
        "Bu aday onayınızı beklemiyor", "Ya karar verilmiş ya da koşu henüz bitmemiş."
    ),
    "not_claimable": _m(
        "Bu kusur şu an bir çalıştırıcıda değil", "Durumu listeden görebilirsiniz."
    ),
    "budget_exhausted": _m(
        "Bugünkü model bütçesi doldu",
        "Yarın kaldığı yerden devam eder; bütçeyi siz artırabilirsiniz.",
    ),
    "parallel_limit": _m("Zaten bir aday üzerinde çalışıyor", "O bitince sıradakini alır."),
    "disk_floor": _m(
        "Diskte yeterli boş alan yok",
        "Çalışma ağaçları için ayrılan alan doldu; siz yer açabilirsiniz.",
    ),
    "opportunity_already_queued": _m("Bu fırsat zaten kuyrukta", "İkinci kez eklemedim."),
    "invalid_defect": _m("Kusur tarifi eksik", "Bir başlık ve ne olduğunu söyleyin."),
    "text_not_found": _m("Aradığınız sözcük dosyada geçmiyor", "Değiştirmedim."),
    "cancelled": _m("İş iptal edildi", "Yarıda kalan bir şey bırakmadım."),
    "commit_failed": _m("Değişiklik kalıcı hâle getirilemedi", "Eski hâli duruyor."),
    "conflict": _m("Bu istek şu anki durumla çakışıyor", "Durumu birlikte gözden geçirelim."),
    "fetch_failed": _m("İçeriği getiremedim", "Kaynak yanıt verince tekrar denenebilir."),
    "hash_mismatch": _m(
        "İndirilen dosyanın özeti beklenenle uyuşmuyor",
        "Kullanmadım; bu bir bütünlük kontrolü.",
    ),
    "identity_unresolved": _m(
        "Bu kaynağın kimliğini kesinleştiremedim", "Doğru adresi verirsen bağlarım."
    ),
    "insufficient_valid_evidence": _m(
        "Elimde yeterli geçerli kanıt yok", "Sonucu uydurmuyorum; istersen daha geniş bakarım."
    ),
    "insufficient_valid_findings": _m(
        "Yeterli sağlam bulgu çıkmadı", "İncesini sunmaktansa böyle söylemeyi tercih ederim."
    ),
    "internal_error": _m("Bende bir hata var", "Kaydını aldım; sonucu sana söylerim."),
    "invalid_argument": _m(
        "Verilen değer bu iş için uygun değil", "Doğrusunu söylersen düzeltirim."
    ),
    "invalid_content_type": _m("Bu içerik türünü işleyemiyorum", "Desteklediklerimi sayabilirim."),
    "invalid_evidence_contract": _m(
        "Kanıt beklenen biçimde gelmedi", "Bu sonucu kanıtlanmış saymadım."
    ),
    "invalid_news_source_id": _m("Böyle bir haber kaynağı kimliği yok", "Listeden seçebiliriz."),
    "invalid_recipient": _m("Alıcı adresi geçerli değil", "Doğru adresi yazarsan gönderirim."),
    "invalid_render": _m("Belgenin bu görünümü üretilemedi", "Metin hâli duruyor."),
    "modal_open": _m("Pencerede açık bir iletişim kutusu var", "Onu kapatmadan devam edemem."),
    "news_source_not_found": _m("Bu haber kaynağını bulamadım", "Kayıtlı olanları sayabilirim."),
    "news_summarize_workflow_start_failed": _m(
        "Haber özetini başlatamadım", "Arka plan servisi yanıt verince tekrar denenebilir."
    ),
    "no_capable_device": _m(
        "Bunu yapabilecek bir cihaz bağlı değil", "Cihaz bağlanınca kendiliğinden çalışır."
    ),
    "no_eligible_video": _m("Ölçütlere uyan bir video bulamadım", "Kapsamı genişletebiliriz."),
    "no_news_source": _m("Tanımlı bir haber kaynağın yok", "İstersen birlikte ekleyelim."),
    "no_valid_render": _m("Gösterilebilir bir görünüm yok", "Belgeyi metin olarak okuyabilirim."),
    "open_failed": _m("Açamadım", "Cihaz yanıt verirse tekrar denenebilir."),
    "origin_refused": _m(
        "Bu adres izin verilen kaynaklar arasında değil", "Listeyi yalnızca sen genişletebilirsin."
    ),
    "playback_failed": _m("Çalma başlatılamadı", "Cihaz hazır olunca tekrar denenebilir."),
    "playback_unverified": _m(
        "Çaldığını doğrulayamadım", "Çaldı demiyorum; sesi duymadıysan haber ver."
    ),
    "precondition_failed": _m("Ön koşul sağlanmadı", "Eksik olanı söyleyebilirim."),
    "precondition_unmet": _m(
        "Bu iş için gereken şart yerine gelmemiş", "Eksiği tamamlayınca olur."
    ),
    "provider_unavailable": _m(
        "Sağlayıcıya ulaşamadım", "Yanıt vermeye başlayınca tekrar denenebilir."
    ),
    "research_failed": _m("Araştırma tamamlanamadı", "Yarım bir sonucu tam gibi sunmuyorum."),
    "research_workflow_start_failed": _m(
        "Araştırmayı başlatamadım", "Arka plan servisine ulaşamadım; tekrar denenebilir."
    ),
    "secret_refused": _m(
        "Bu bir sır gibi göründü; taşımadım", "Sırlar yalnızca sır deposunda durur."
    ),
    "send_failed": _m("Gönderemedim", "Taslak duruyor; tekrar denenebilir."),
    "too_large": _m("İçerik izin verilen boyutu aşıyor", "Daha küçük bir parça denenebilir."),
    "too_many_running": _m(
        "Aynı anda çalışan iş sayısı sınırda", "Biri bitince sıradakini başlatırım."
    ),
    "unsupported_provider": _m("Bu sağlayıcıyı tanımıyorum", "Tanıdıklarımı sayabilirim."),
    # 2026-09-18: a mission's approve/resume said by the model, not the owner.
    "owner_word_required": _m(
        "Bu kararı sizin vermeniz gerekiyor", '"Devam et" ya da "Evet, başla" demeniz yeterli.'
    ),
    "workflow_abandoned": _m(
        "Arka plandaki iş yarıda kaldı", "Yarım işi bitmiş gibi göstermiyorum."
    ),
    "workflow_start_failed": _m(
        "Arka plandaki işi başlatamadım", "Servis yanıt verince tekrar denenebilir."
    ),
    # B27 (req 731-733): the everyday sentences' own refusals, spoken by the tools that
    # answer them; listed here so the web and the notification ladder read the same words.
    "cancelled_by_owner": _m(
        "Araştırmayı sen iptal ettin", "Yeniden başlatmak istersen söylemen yeter."
    ),
    "deletion_not_permitted": _m(
        "Takvimden etkinlik silme yetkim yok",
        "Bunu takvim uygulamandan sen yapabilirsin; ben yalnız ekler ve taşırım.",
    ),
    "nothing_running": _m("Şu anda süren bir araştırma yok", "İptal edecek bir şey kalmadı."),
    # B31 req 203/204: the research pause and resume refusals.
    "already_paused": _m("Araştırma zaten duraklatılmış", "Devam etmesini isterseniz söyleyin."),
    "not_paused": _m("Duraklatılmış bir araştırma yok", "Araştırma zaten sürüyor."),
    "volume_failed": _m(
        "Ses seviyesini değiştiremedim",
        "Tarayıcıdaki sekme kapanmış olabilir; yeniden açtırabilirsin.",
    ),
}


def describe(error_class: str | None) -> OwnerMessage:
    """The Turkish for one error class; a named fallback for anything unmapped."""
    token = (error_class or "").strip()
    known = TR.get(token)
    if known is not None:
        return known
    return OwnerMessage(what=f"{UNKNOWN_WHAT} ({token or 'sınıfsız'})", remedy=UNKNOWN_REMEDY)


def sentence(error_class: str | None) -> str:
    """One Turkish sentence for a class — what happened, then what can be done."""
    return describe(error_class).sentence()


__all__ = ["TR", "UNKNOWN_REMEDY", "UNKNOWN_WHAT", "OwnerMessage", "describe", "sentence"]
