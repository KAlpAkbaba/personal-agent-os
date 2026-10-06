# ADR (numarasız, Proje Yöneticisi numaralar): Yarım kalan çalışma ağacı kendini onarır

- Tarih: 2026-10-06
- Kart: worktree-half-made-heals (öneri team/proposals/2026-10-06-yarim-calisma-agaci-kendini-onarsin.md, sahip onayladı)
- Alan: scripts/lib/TeamRun.ps1, scripts/tests/team-run-temp.tests.ps1

## Bağlam

6 Ekim'de 12 kart koltuk açılırken durdu: `git worktree add` 300 sn'de öldürüldü ve geride ya
(i) klasör + `.git` dosyası, yönetim klasöründe `locked` + boş `index.lock`, `index` yok; ya da
(ii) dal var, klasör yok, artakalan (kilitli) `.git/worktrees/<ad>` girdisi kaldı. Eski
`New-TeamWorktree` (i)'de ".git var" deyip işçiyi kilitli ağaca yolladı, (ii)'de düştü. Her biri
Danışman'ın elle dört git komutunu bekledi.

## Karar

1. `Test-TeamWorktreeHealthy`: sağlıklı = `.git` dosyası var VE `git rev-parse --git-dir` başarılı
   VE yönetim klasöründe `index` var, `locked` yok, `index.lock` yok. Saf denetim; Healthy +
   Türkçe Reason + GitDir döndürür.
2. `New-TeamWorktree`: sağlıklı ağaç -> bugünkü "already there" (kilit almaz, yazma komutu yok).
   Sağlıksız ağaç ya da `worktree add` düştü (başarısız dönüş ya da zaman aşımı istisnası) ->
   `Repair-TeamWorktree`, sonra BİR kez daha add. Dal var/klasör yok durumunda önce bugünkü gibi
   -b'siz add denenir; ancak o düşerse onarıma gidilir. Neden: commit'li bir dalın ağacı
   `Remove-TeamWorktree` ile kaldırılıp sonra yeniden açılması (RETURN, denetçi) olağan yoldur;
   doğrudan onarıma gitmek onu "onarılmadı (n commit)" ile durdururdu.
3. Silme üçlüsü (hepsi gerekir): (a) sağlıksız ya da add düştü, (b) `git rev-list --count
   <Base>..<dal>` = 0 (dal yoksa 0), (c) klasör yok ya da içinde iş yok. Sağlanınca: `worktree
   unlock` (hata yok sayılır), `worktree remove --force --force`, kalan klasör, ortak git
   dizinindeki `worktrees/*` altında `gitdir` dosyası BU ağacın `.git`'ini gösteren girdi,
   `worktree prune`. Sağlanmazsa hiçbir şeye dokunulmaz: `yarım ağaç onarılmadı: <neden> (<n>
   commit | kirli: ...)` throw edilir, Danışman'a gider.
4. Dal SİLİNMEZ: 0 commit'li dal Base'e eşittir, -b'siz yeniden bağlanır; silmek yalnız risk
   ekler (yanlış dal adı, başka bir ağacın dalı).
5. "Kirli" nasıl ölçülür: `index` varsa düz `git status --porcelain --untracked-files=all` boş
   olmalı. `index` yoksa (yarım ağaç) git status her dosyayı "silinmiş + izlenmeyen" gösterir
   (ölçüldü), bu yüzden HEAD'den okunmuş GEÇİCİ bir index'le (`GIT_INDEX_FILE`, temp altında,
   kilitli yönetim klasörüne yazılmaz) sorulur; orada yalnız ` D` satırı (öldürülen checkout'un
   yazamadığı dosya) iş sayılmaz, başka her satır kirlidir. Git hiç sorulamıyorsa (rev-parse ya
   da read-tree düştü) DİKKATLİ yol: klasörde `.git` dışında bir öğe varsa SİLİNMEZ. Klasördeki
   dosyalar Base ağacıyla elle karşılaştırılmaz.
6. Makine başına tek `worktree add` kilidi: `System.Threading.Mutex` adı
   `Global\PagentOS-git-worktree-add` (açılamazsa `Local\`). Döngü, test turu, integrate ve nöbet
   aynı makinede aynı diske yazar; add+onar bloğunu sarar, "already there" yolu almaz. Bekleme
   sınırı `-LockTimeoutSeconds` (varsayılan 900); `AbandonedMutexException` kilidi alınmış sayar;
   `finally`'de bırakılır. Kilit alındıktan sonra sağlık yeniden okunur (bekleyen süreç, öbürünün
   yeni bitirdiği ağacı onarmaya kalkmaz).
7. "host yavaş" sözleşmesi: ikinci add de düşerse ya da kilit sınırda alınamazsa istisna mesajı
   SABİT `host yavaş: git worktree add` önekiyle başlar, `Exception.Data['PagentosReason'] =
   'host-slow'`.
8. Ayrı kart (Proje Yöneticisi, cycle.ps1 boşalınca, birkaç satır): cycle.ps1'in catch'i
   `host yavaş:` önekini görünce Stop-Task yerine kartı `approved`'a geri koyar, reason
   `host yavaş`, işçi dönüş sayacı artmaz. Bu kart cycle.ps1'e dokunmaz (üç kartın alanında);
   o bağlantı girene kadar "host yavaş" satırları döngü raporunda durdurma olarak görünür.
9. Test enjeksiyonu: `Invoke-TeamGit` suite içinde (vaka gövdesinin kapsamında) gölgelenir;
   kütüphaneye sahte çağırıcı parametresi eklenmedi - üretim yüzeyi değişmez. İki süreçli kilit
   vakası her alt süreçte aynı gölgeyle add aralığını dosyaya yazar.

## Reddedilen alternatifler

- Zaman aşımını 900 sn'ye çıkarmak: yavaş diski gizler, yarım ağacı yine bırakır (öldürülen add
  900 sn'de de öldürülür) ve her takılma 15 dakika yer.
- Koltuk başına kalıcı ağaç + `reset --hard`: başka bir koltuğun yarım işini silme riski, dal
  akışının (işçi/denetçi/kapı) değişmesi; bu kart yalnız onarır, akışı değiştirmez.

## Değişmeyen

Dal adları, işçi/denetçi/kapı akışı, `Remove-TeamWorktree`, çağıran betikler (cycle.ps1,
TeamDuty.ps1, Merge-TeamBranch, new-worktree.ps1, integration-branch.ps1) - iyileşmeyi
kendiliğinden alırlar. Yeni bağımlılık yok.
