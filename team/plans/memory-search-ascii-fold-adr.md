# ADR taslağı: memory-search-ascii-fold — hafıza kelime ayağı ASCII katlaması

**Durum:** taslak. İki yarı da uygulandı: `trgm` (SQLite) yarısı — `lexical.search_key`/
`search_key_sql` = konuşma aramasının `search_fold`/`search_fold_sql`'i (kopya yok),
`lexical.term_matches` iki tarafı `search_key` ile karşılaştırır; `like` yarısı (staging'in
varsayılan yolu) — `retrieval.keyword_candidates` terimi `search_key`, sütunu `search_key_sql`
ile katlar. PG `trgm` yolu ve ifade dizini takip kartı olarak kalır (madde 4).

**Bağlam.** Test turu t-w10070808 (staging b1f8ef94): hafızada 'sukru' araması 'Şükrü' anısını
bulmadı. Staging'de `PAGENTOS_MEMORY_LEXICAL` ayarlı değil, yani varsayılan `like` modu çalışıyor:
`retrieval.keyword_candidates` terimleri `str.lower` ile küçültüp `lower(text) LIKE '%sukru%'`
diye sorguluyor; Türkçe harfler hiç katlanmıyor. `trgm` modunda `lexical.fold` yalnız ı->i katlıyor.

**Karar (önerilen).**
1. Tek katlama fonksiyonu: konuşma aramasının `app.conversations.search.search_fold` /
   `search_fold_sql` fonksiyonları (ş->s, ü->u, ğ->g, ı/İ/I->i, ç->c, ö->o, â/î/û, küçük harf).
   İkinci bir kopya yazılmaz; `lexical` bunu `search_key` adıyla dışarı verir.
2. `like` modu (retrieval.py): terim `lexical.search_key(term)`, sütun
   `lexical.search_key_sql(Memory.text)`; terim seçimi (`_QUERY_WORD`, ilk 8) değişmez, böylece
   `test_like_mode_keeps_todays_terms` / `test_like_mode_buries_a_name_said_once` yeşil kalır.
3. `trgm` modu, SQLite yolu: `term_matches` terimi, kökünü ve metni `search_key` ile katlar.
   `lexical.fold` (ı->i) ve `stem_root` olduğu gibi kalır: `tsquery_text`, PG `turkish`
   sözlüğü ve 0073 göçünün dizini ona bağlı.
4. `trgm` modu, PostgreSQL yolu: ASCII katlaması ayrı bir iş (göç 0073'ün dizini
   `translate(lower(text),'ı','i')` ifadesinde; ifade değişirse yeni dizin göçü gerekir).
   Takip kartı: PG `trgm` ASCII katlaması + dizin göçü + entegrasyon testi; büyüyen hafızada
   `like` yolunun 19 `replace` ifadesi tam tarama yapar — gerekirse ifade dizini aynı kartta.

**Sonuç.** 'sukru' <-> 'Şükrü', 'ŞÜKRÜ' -> 'şükrü' her iki modda bulunur; başka bir kelime
eşleşmez. Göç yok (`like` yolu dizin kullanmıyordu).
