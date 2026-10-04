# ADR (taslak, numarayı lead verir): koruyucu sonucunun kurallar katmanı — kartın `guards` alanı

Görev: branch-guards-rules (d20261004). Öneri: `team/proposals/2026-10-02-koruyucu-testler-is-dalinda.md`.
Kod: `scripts/lib/TeamGuardRules.ps1` (saf; süreç başlatmaz, dosya okumaz, depoya yazmaz).
Testler: `services/api/tests/unit/test_team_guard_rules.py`.

## Karar

1. **Kartın alanı.** Koruyucu koşusunun sonucu kartta tek bir alana yazılır:
   `task.guards = {at, sha, status, runs, open: [{id, label, outcome, detail, sha}], resolved: [{id, by: 'branch' | 'integration', sha, at}]}`.
   `open` en son dal koşusunun yeşil olmayan satırlarıdır (`red`, `hung`, `missing`); `resolved` bir satırın nasıl
   kapandığının kaydıdır. Aynı sha'lı RESULT ikinci kez verilirse hiçbir şey değişmez (`runs` sayılmaz).
2. **Satırı kim, nasıl çözer.** Yalnızca aynı koruyucunun daha sonraki YEŞİL bir koşusu: işin kendi dalında
   (`Set-TeamGuardResult`, `by = 'branch'`) ya da entegrasyon dalında (`Resolve-TeamGuardRows`, `by = 'integration'`,
   yalnız `merged` işler için, o RESULT'ın sha'sıyla). Elle düzeltme yolu yoktur. Entegrasyon RESULT'ında hâlâ
   kırmızı olan ya da hiç bulunmayan koruyucunun satırı açık kalır. Dal koşusunda listeden çıkmış bir koruyucunun
   satırı `open`'dan düşer (open tam olarak bu RESULT'ın yeşil olmayan satırlarıdır), `resolved`'a girmez.
3. **Kırmızı tek başına durum değiştirmez.** `Set-TeamGuardResult` yalnız `task.guards`'a yazar; `state`, `returns`,
   `reason`, `area`, `depends_on` aynı kalır. Neden: yeni bir test dosyası kapıya ancak birleştirmede bağlanır;
   iş dalında `ci-covers-every-suite` doğası gereği kırmızı olabilir (`2c691585`). İşi durdurmak ya da geri
   vermek denetleyicinin hükmüdür.
4. **Denetleyicinin hükmü ezilmez.** `Get-TeamGuardInspectorNote` denetleyicinin önüne her açık satırı (id, etiket,
   sonuç, ayrıntı) ve iki yollu kuralı koyar: düzeltme kartın alanının içindeyse geri verilecek işin maddesidir;
   dışındaysa (ortak dosya, kapının listesi) raporda adıyla yazılır ve lead'in bağlama listesine satır olur.
   Notun hiçbir satırı tek başına bir hüküm sözcüğü değildir ve ters tırnak taşımaz; `Get-TeamVerdict` "not +
   rapor" üzerinde raporun hükmünü okur (test 4).
5. **Kapıyı ne durdurur.** `Test-TeamGuardsBlockGate`: en az bir `merged` işin açık satırı varsa tam kapı başlamaz;
   `Why` her işin kimliğini ve etiketlerini Türkçe tek cümlede söyler. Lead'in listesi (`Get-TeamGuardWiringList`)
   aynı kümedir: yalnız `merged` işlerin açık satırları. Başka durumdaki işin satırı kapıyı durdurmaz (dal
   entegrasyonda değildir; o hâlâ denetleyicinin işidir).
   **`guards` alanı olmayan iş kapıyı durdurmaz** (bu adımdan önceki kartlar; koşulmamış koruyucu "çözülmemiş"
   sayılırsa ilk entegrasyon sonsuza dek bekler). Bu seçim — sahip incelemesi bekliyor.
6. **Ofis cümlesi.** Hiç koşulmamış: boş; yeşil: `koruyucular: yeşil`; açık satırlar: `koruyucu kırmızı: <etiket>`,
   `koruyucu asılı kaldı, durduruldu: <etiket>`, `koruyucu dosyası bu dalda yok: <etiket>`, birden çoğu `; ` ile.
   Etiketsiz satır kimliğiyle adlanır; çıktıda `null` olmaz.
7. **Bağlama.** Bu kart hiçbir şeyi bağlamaz: alan, Bulut Çekirdeği deposuna ve `team/queue.schema.json`'a ancak
   bağlama kartıyla (döngü adımı, şema alanı, denetleyici paragrafı, Ofis satırı) ulaşır. O zamana dek sahip yeni
   bir şey görmez.

## Kanıt

PROVEN_AUTOMATED: 10 test tek PowerShell 5.1 sürecinde; gerçek `scripts/team/guards.ps1` geçici bir git deposunda
koşup yazdığı RESULT dosyası bu katmana verilir (test 10). Üç mutasyon (kapı açık satırı yok sayar; entegrasyon
kırmızı satırı çözer; aynı-sha denetimi kalkar) sırasıyla 7, 6 ve 3. testleri kırmızıya çevirir.
PROVEN_REAL bağlama kartının ilk gerçek döngülerine aittir.
