# Olumsuz emir koruyucusu: üretilen vakalar

Bu dosya `test_destructive_negation_guard.py` üreticisinin çıktısıdır; test birebir aynı olduğunu sınar. Yeniden üretmek için (`services/api` içinde): `uv run python -m tests.unit.test_destructive_negation_guard`.

Beklenen her satırda aynıdır: cümle hiçbir silen/iptal eden niyete gitmez. `AÇIK` satırları bugün kırmızıdır (`KNOWN_OPEN`, xfail strict) ve düzeltme kartını adlar.

| araç | tür | olumlu (kimlik) | olumlu cümle | olumsuz cümle | beklenen |
|---|---|---|---|---|---|
| memory_forget | generated | m.forget.1 | Bunu unut. | Bunu unutma. | silen araca gitmez |
| memory_forget | generated | m.forget.1 | Bunu unut. | Bunu unutmayın. | silen araca gitmez |
| memory_forget | generated | m.forget.1 | Bunu unut. | Bunu unutmayınız. | silen araca gitmez |
| memory_forget | generated | b51.memory_forget.1 | Bunu hafızandan sil. | Bunu hafızandan silme. | silen araca gitmez |
| memory_forget | generated | b51.memory_forget.1 | Bunu hafızandan sil. | Bunu hafızandan silmeyin. | silen araca gitmez |
| memory_forget | generated | b51.memory_forget.1 | Bunu hafızandan sil. | Bunu hafızandan silmeyiniz. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.1 | Araştırmayı iptal et. | Araştırmayı iptal etme. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.1 | Araştırmayı iptal et. | Araştırmayı iptal etmeyin. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.1 | Araştırmayı iptal et. | Araştırmayı iptal etmeyiniz. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.2 | Araştırmayı durdur. | Araştırmayı durdurma. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.2 | Araştırmayı durdur. | Araştırmayı durdurmayın. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.2 | Araştırmayı durdur. | Araştırmayı durdurmayınız. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.3 | Araştırmayı bırak. | Araştırmayı bırakma. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.3 | Araştırmayı bırak. | Araştırmayı bırakmayın. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.3 | Araştırmayı bırak. | Araştırmayı bırakmayınız. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.4 | Araştırmadan vazgeç. | Araştırmadan vazgeçme. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.4 | Araştırmadan vazgeç. | Araştırmadan vazgeçmeyin. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.4 | Araştırmadan vazgeç. | Araştırmadan vazgeçmeyiniz. | silen araca gitmez |
| routine_cancel | generated | r.cancel.1 | Sabah rutinini iptal et. | Sabah rutinini iptal etme. | silen araca gitmez |
| routine_cancel | generated | r.cancel.1 | Sabah rutinini iptal et. | Sabah rutinini iptal etmeyin. | silen araca gitmez |
| routine_cancel | generated | r.cancel.1 | Sabah rutinini iptal et. | Sabah rutinini iptal etmeyiniz. | silen araca gitmez |
| alarm_cancel | generated | r.collision.alarm_cancel | Sabah alarmımı iptal et. | Sabah alarmımı iptal etme. | silen araca gitmez |
| alarm_cancel | generated | r.collision.alarm_cancel | Sabah alarmımı iptal et. | Sabah alarmımı iptal etmeyin. | silen araca gitmez |
| alarm_cancel | generated | r.collision.alarm_cancel | Sabah alarmımı iptal et. | Sabah alarmımı iptal etmeyiniz. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.1 | Alarmı iptal et. | Alarmı iptal etme. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.1 | Alarmı iptal et. | Alarmı iptal etmeyin. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.1 | Alarmı iptal et. | Alarmı iptal etmeyiniz. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.2 | Sabah alarmını iptal et. | Sabah alarmını iptal etme. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.2 | Sabah alarmını iptal et. | Sabah alarmını iptal etmeyin. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.2 | Sabah alarmını iptal et. | Sabah alarmını iptal etmeyiniz. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.3 | Alarmı kaldır. | Alarmı kaldırma. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.3 | Alarmı kaldır. | Alarmı kaldırmayın. | silen araca gitmez |
| alarm_cancel | generated | a.cancel.3 | Alarmı kaldır. | Alarmı kaldırmayınız. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.2 | Perşembeki toplantıyı iptal et. | Perşembeki toplantıyı iptal etme. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.2 | Perşembeki toplantıyı iptal et. | Perşembeki toplantıyı iptal etmeyin. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.2 | Perşembeki toplantıyı iptal et. | Perşembeki toplantıyı iptal etmeyiniz. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.3 | Yarınki randevuyu iptal et. | Yarınki randevuyu iptal etme. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.3 | Yarınki randevuyu iptal et. | Yarınki randevuyu iptal etmeyin. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.3 | Yarınki randevuyu iptal et. | Yarınki randevuyu iptal etmeyiniz. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.4 | Toplantıyı takvimden sil. | Toplantıyı takvimden silme. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.4 | Toplantıyı takvimden sil. | Toplantıyı takvimden silmeyin. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.4 | Toplantıyı takvimden sil. | Toplantıyı takvimden silmeyiniz. | silen araca gitmez |
| evolution_cancel | generated | ev.cancel.1 | Bu geliştirmeyi iptal et. | Bu geliştirmeyi iptal etme. | silen araca gitmez |
| evolution_cancel | generated | ev.cancel.1 | Bu geliştirmeyi iptal et. | Bu geliştirmeyi iptal etmeyin. | silen araca gitmez |
| evolution_cancel | generated | ev.cancel.1 | Bu geliştirmeyi iptal et. | Bu geliştirmeyi iptal etmeyiniz. | silen araca gitmez |
| evolution_cancel | generated | ev.cancel.2 | Geliştirmeden vazgeç. | Geliştirmeden vazgeçme. | silen araca gitmez |
| evolution_cancel | generated | ev.cancel.2 | Geliştirmeden vazgeç. | Geliştirmeden vazgeçmeyin. | silen araca gitmez |
| evolution_cancel | generated | ev.cancel.2 | Geliştirmeden vazgeç. | Geliştirmeden vazgeçmeyiniz. | silen araca gitmez |
| macro_record_cancel | generated | macro.cancel.1 | Hareketi iptal et. | Hareketi iptal etme. | silen araca gitmez |
| macro_record_cancel | generated | macro.cancel.1 | Hareketi iptal et. | Hareketi iptal etmeyin. | silen araca gitmez |
| macro_record_cancel | generated | macro.cancel.1 | Hareketi iptal et. | Hareketi iptal etmeyiniz. | silen araca gitmez |
| macro_delete | generated | macro.delete.1 | Yeni mail sekmesi hareketini sil. | Yeni mail sekmesi hareketini silme. | silen araca gitmez |
| macro_delete | generated | macro.delete.1 | Yeni mail sekmesi hareketini sil. | Yeni mail sekmesi hareketini silmeyin. | silen araca gitmez |
| macro_delete | generated | macro.delete.1 | Yeni mail sekmesi hareketini sil. | Yeni mail sekmesi hareketini silmeyiniz. | silen araca gitmez |
| operator_cancel | generated | op.cancel.1 | İptal et. | İptal etme. | silen araca gitmez |
| operator_cancel | generated | op.cancel.1 | İptal et. | İptal etmeyin. | silen araca gitmez |
| operator_cancel | generated | op.cancel.1 | İptal et. | İptal etmeyiniz. | silen araca gitmez |
| operator_cancel | generated | op.cancel.2 | Dur. | Durma. | silen araca gitmez |
| operator_cancel | generated | op.cancel.2 | Dur. | Durmayın. | silen araca gitmez |
| operator_cancel | generated | op.cancel.2 | Dur. | Durmayınız. | silen araca gitmez |
| document_delete | generated | doc.delete.first_word | Bu dosyayı sil. | Bu dosyayı silme. | silen araca gitmez |
| document_delete | generated | doc.delete.first_word | Bu dosyayı sil. | Bu dosyayı silmeyin. | silen araca gitmez |
| document_delete | generated | doc.delete.first_word | Bu dosyayı sil. | Bu dosyayı silmeyiniz. | silen araca gitmez |
| artifact_delete | generated | art.delete.asks_first | Bunu sil. | Bunu silme. | silen araca gitmez |
| artifact_delete | generated | art.delete.asks_first | Bunu sil. | Bunu silmeyin. | silen araca gitmez |
| artifact_delete | generated | art.delete.asks_first | Bunu sil. | Bunu silmeyiniz. | silen araca gitmez |
| exec_cancel | generated | exec.cancel.1 | Bunu iptal et. | Bunu iptal etme. | silen araca gitmez |
| exec_cancel | generated | exec.cancel.1 | Bunu iptal et. | Bunu iptal etmeyin. | silen araca gitmez |
| exec_cancel | generated | exec.cancel.1 | Bunu iptal et. | Bunu iptal etmeyiniz. | silen araca gitmez |
| exec_cancel | generated | exec.cancel.2 | Vazgeç. | Vazgeçme. | silen araca gitmez |
| exec_cancel | generated | exec.cancel.2 | Vazgeç. | Vazgeçmeyin. | silen araca gitmez |
| exec_cancel | generated | exec.cancel.2 | Vazgeç. | Vazgeçmeyiniz. | silen araca gitmez |
| exec_cancel | generated | b51.exec_cancel.1 | Bu işi iptal et. | Bu işi iptal etme. | silen araca gitmez |
| exec_cancel | generated | b51.exec_cancel.1 | Bu işi iptal et. | Bu işi iptal etmeyin. | silen araca gitmez |
| exec_cancel | generated | b51.exec_cancel.1 | Bu işi iptal et. | Bu işi iptal etmeyiniz. | silen araca gitmez |
| watch_remove | generated | w.remove.1 | Fiyat nöbetini kaldır. | Fiyat nöbetini kaldırma. | silen araca gitmez |
| watch_remove | generated | w.remove.1 | Fiyat nöbetini kaldır. | Fiyat nöbetini kaldırmayın. | silen araca gitmez |
| watch_remove | generated | w.remove.1 | Fiyat nöbetini kaldır. | Fiyat nöbetini kaldırmayınız. | silen araca gitmez |
| watch_remove | generated | w.remove.2 | Nöbeti kaldır. | Nöbeti kaldırma. | silen araca gitmez |
| watch_remove | generated | w.remove.2 | Nöbeti kaldır. | Nöbeti kaldırmayın. | silen araca gitmez |
| watch_remove | generated | w.remove.2 | Nöbeti kaldır. | Nöbeti kaldırmayınız. | silen araca gitmez |
| watch_forget_all | generated | w.forget.1 | Nöbetleri unut. | Nöbetleri unutma. | silen araca gitmez |
| watch_forget_all | generated | w.forget.1 | Nöbetleri unut. | Nöbetleri unutmayın. | silen araca gitmez |
| watch_forget_all | generated | w.forget.1 | Nöbetleri unut. | Nöbetleri unutmayınız. | silen araca gitmez |
| watch_forget_all | generated | w.forget.2 | Bütün nöbetleri sil. | Bütün nöbetleri silme. | silen araca gitmez |
| watch_forget_all | generated | w.forget.2 | Bütün nöbetleri sil. | Bütün nöbetleri silmeyin. | silen araca gitmez |
| watch_forget_all | generated | w.forget.2 | Bütün nöbetleri sil. | Bütün nöbetleri silmeyiniz. | silen araca gitmez |
| memory_forget | incident | B16 2026-09-13 |  | Bunu unutma. | silen araca gitmez |
| memory_forget | incident | öneri 2026-10-05 gerçek cihaz cümlesi |  | Hafızadan bunu unutma. | silen araca gitmez |
| research_cancel | incident | B27 2026-09-14 |  | Araştırmayı iptal etme. | silen araca gitmez |
| watch_forget_all | incident | watch-voice-inspector-2 bulgu 1 |  | Nöbetleri silme. | silen araca gitmez |
| watch_forget_all | ablative | watch-voice-inspector-2 bulgu 2 |  | Nöbetlerimden birini sil. | silen araca gitmez |
| watch_forget_all | ablative | pano 2026-10-05 06:33 denetleyici |  | Nöbetlerden fiyatı kaldır. | silen araca gitmez |
| capability_cancel | olumlu yok | - | - | - | korpusun cümleleri ('Vazgeç, yapma.', 'Vazgeçtim.') emirle bitmiyor: son kelime zaten olumsuz ya da geçmiş zaman |
