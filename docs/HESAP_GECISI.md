# İki Claude hesabı arasında geçiş — sahibin tarifi

İki hesap aynı bilgisayarda, aynı proje klasöründe çalışır. Kod, `CLAUDE.md`, `docs/HANDOFF.md`
ve Claude'un hafıza notları (`C:\Users\alpak\.claude\projects\…\memory\`) iki hesapta
**ortaktır**. Hesaba bağlı olan tek şey konuşmanın kendisidir; onu da devir notu taşır.

## Geçişten ÖNCE (token bitmek üzereyken — mümkünse)

Eski hesaba tek cümle yazın:

> **"Devir notunu güncelle, hesap değiştireceğim."**

O oturum `docs/HANDOFF.md`'deki "Şu an üzerinde çalışılan" bölümünü ve yarım işi yazar.
Token hiç haber vermeden biterse sorun değil: bu bölüm zaten her işin başında yazılıyor,
yarım kalan dosyalar da klasörde duruyor.

## Geçiş

1. Eski hesabın oturumunu kapatın ya da bekletin — **iki hesabı aynı anda bu klasörde
   çalıştırmayın** (aynı dosyaları düzenleyip birbirinin işini ezerler).
2. Claude uygulamasında diğer hesaba geçin.
3. Code sekmesinde **aynı klasörü** açın:
   `E:\AI\PersonalAgentOS_Claude_Autonomous_Build_Package_v1`
4. Yeni oturum başlatın.

## Geçişten SONRA

Tek kelime yazın:

> **"devam"**

Oturum açılır açılmaz devir notunun canlı kısmı, `git status` ve son commitler otomatik
olarak bağlama girer (`.claude/hooks/session-start.ps1`). Yeni oturum ne yapıldığını,
nerede kalındığını ve sıradakini bilir; yarım iş varsa onu tamamlar.

Aynı hesapta yeni bir konuşma açtığınızda da aynısı geçerlidir.

## Bir şey ters giderse

- Yeni oturum kaldığı yeri bilmiyor gibi davranırsa: **"HANDOFF.md'yi oku ve devam et"** yazın.
- İlk açılışta Claude klasöre güvenip güvenmediğinizi sorabilir — "güven" deyin, yoksa
  otomatik kanca çalışmaz.
- İlk kez kullanılan hesap bazı izinleri (komut çalıştırma vb.) yeniden sorabilir; bu hesaba
  özel bir ayardır, bir kez onaylamak yeter.
