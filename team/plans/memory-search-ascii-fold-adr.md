# ADR taslağı: memory-search-ascii-fold — hafıza kelime ayağı ASCII katlaması

**Durum:** taslak (kırmızı test işlendi; uygulama alan isteğini bekliyor).

**Bağlam.** Test turu t-w10070808 (staging b1f8ef94): hafızada 'sukru' araması 'Şükrü' anısını
bulmadı. Staging'de `PAGENTOS_MEMORY_LEXICAL` ayarlı değil, yani varsayılan `like` modu çalışıyor:
`retrieval.keyword_candidates` terimleri `str.lower` ile küçültüp `lower(text) LIKE '%sukru%'`
diye sorguluyor; Türkçe harfler hiç katlanmıyor. `trgm` modunda `lexical.fold` yalnız ı->i katlıyor.

**Karar (önerilen).**
1. Tek katlama fonksiyonu: konuşma aramasının `app.conversations.search.search_fold` /
   `search_fold_sql` fonksiyonları (ş->s, ü->u, ğ->g, ı/İ/I->i, ç->c, ö->o, â/î/û, küçük harf).
   İkinci bir kopya yazılmaz; `lexical` bunu `search_key` adıyla dışarı verir.
2. `like` modu: terim `search_key(term)`, sütun `search_fold_sql(Memory.text)`.
3. `trgm` modu, SQLite yolu: terim ve metin `search_key` ile karşılaştırılır.
   `lexical.fold` (ı->i) ve `stem_root` olduğu gibi kalır: `tsquery_text`, PG `turkish`
   sözlüğü ve 0073 göçünün dizini ona bağlı.
4. `trgm` modu, PostgreSQL yolu: ASCII katlaması ayrı bir iş (göç 0073'ün dizini
   `translate(lower(text),'ı','i')` ifadesinde; ifade değişirse yeni dizin göçü gerekir).

**Sonuç.** 'sukru' <-> 'Şükrü', 'ŞÜKRÜ' -> 'şükrü' her iki modda bulunur; başka bir kelime
eşleşmez. Göç yok (`like` yolu dizin kullanmıyordu).
