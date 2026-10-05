# Olumsuz emir koruyucusu: üretilen vakalar

Bu dosya `test_destructive_negation_guard.py` üreticisinin çıktısıdır; test birebir aynı olduğunu sınar. Yeniden üretmek için (`services/api` içinde): `uv run python -m tests.unit.test_destructive_negation_guard`.

Beklenen her satırda aynıdır: cümle hiçbir silen/iptal eden niyete gitmez. `AÇIK` satırları bugün kırmızıdır (`KNOWN_OPEN`, xfail strict) ve düzeltme kartını adlar.

| araç | tür | olumlu (kimlik) | olumlu cümle | olumsuz cümle | beklenen |
|---|---|---|---|---|---|
| memory_forget | generated | m.forget.1 | Bunu unut. | Bunu unutma. | silen araca gitmez |
| memory_forget | generated | m.forget.1 | Bunu unut. | Bunu unutmayın. | silen araca gitmez |
| memory_forget | generated | m.forget.1 | Bunu unut. | Bunu unutmayınız. | silen araca gitmez |
| memory_forget | generated | b51.memory_forget.1 | Bunu hafızandan sil. | Bunu hafızandan silme. | AÇIK: negation-fix-cancel-verb-stems |
| memory_forget | generated | b51.memory_forget.1 | Bunu hafızandan sil. | Bunu hafızandan silmeyin. | AÇIK: negation-fix-cancel-verb-stems |
| memory_forget | generated | b51.memory_forget.1 | Bunu hafızandan sil. | Bunu hafızandan silmeyiniz. | AÇIK: negation-fix-cancel-verb-stems |
| research_cancel | generated | d.research_cancel.1 | Araştırmayı iptal et. | Araştırmayı iptal etme. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.1 | Araştırmayı iptal et. | Araştırmayı iptal etmeyin. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.1 | Araştırmayı iptal et. | Araştırmayı iptal etmeyiniz. | AÇIK: negation-fix-research-cancel |
| research_cancel | generated | d.research_cancel.2 | Araştırmayı durdur. | Araştırmayı durdurma. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.2 | Araştırmayı durdur. | Araştırmayı durdurmayın. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.2 | Araştırmayı durdur. | Araştırmayı durdurmayınız. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.3 | Araştırmayı bırak. | Araştırmayı bırakma. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.3 | Araştırmayı bırak. | Araştırmayı bırakmayın. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.3 | Araştırmayı bırak. | Araştırmayı bırakmayınız. | silen araca gitmez |
| research_cancel | generated | d.research_cancel.4 | Araştırmadan vazgeç. | Araştırmadan vazgeçme. | AÇIK: negation-fix-research-cancel |
| research_cancel | generated | d.research_cancel.4 | Araştırmadan vazgeç. | Araştırmadan vazgeçmeyin. | AÇIK: negation-fix-research-cancel |
| research_cancel | generated | d.research_cancel.4 | Araştırmadan vazgeç. | Araştırmadan vazgeçmeyiniz. | AÇIK: negation-fix-research-cancel |
| routine_cancel | generated | r.cancel.1 | Sabah rutinini iptal et. | Sabah rutinini iptal etme. | AÇIK: negation-fix-cancel-verb-stems |
| routine_cancel | generated | r.cancel.1 | Sabah rutinini iptal et. | Sabah rutinini iptal etmeyin. | AÇIK: negation-fix-cancel-verb-stems |
| routine_cancel | generated | r.cancel.1 | Sabah rutinini iptal et. | Sabah rutinini iptal etmeyiniz. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | r.collision.alarm_cancel | Sabah alarmımı iptal et. | Sabah alarmımı iptal etme. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | r.collision.alarm_cancel | Sabah alarmımı iptal et. | Sabah alarmımı iptal etmeyin. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | r.collision.alarm_cancel | Sabah alarmımı iptal et. | Sabah alarmımı iptal etmeyiniz. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.1 | Alarmı iptal et. | Alarmı iptal etme. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.1 | Alarmı iptal et. | Alarmı iptal etmeyin. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.1 | Alarmı iptal et. | Alarmı iptal etmeyiniz. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.2 | Sabah alarmını iptal et. | Sabah alarmını iptal etme. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.2 | Sabah alarmını iptal et. | Sabah alarmını iptal etmeyin. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.2 | Sabah alarmını iptal et. | Sabah alarmını iptal etmeyiniz. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.3 | Alarmı kaldır. | Alarmı kaldırma. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.3 | Alarmı kaldır. | Alarmı kaldırmayın. | AÇIK: negation-fix-cancel-verb-stems |
| alarm_cancel | generated | a.cancel.3 | Alarmı kaldır. | Alarmı kaldırmayınız. | AÇIK: negation-fix-cancel-verb-stems |
| calendar_cancel | generated | d.cancel_event.2 | Perşembeki toplantıyı iptal et. | Perşembeki toplantıyı iptal etme. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.2 | Perşembeki toplantıyı iptal et. | Perşembeki toplantıyı iptal etmeyin. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.2 | Perşembeki toplantıyı iptal et. | Perşembeki toplantıyı iptal etmeyiniz. | AÇIK: negation-fix-calendar-cancel |
| calendar_cancel | generated | d.cancel_event.3 | Yarınki randevuyu iptal et. | Yarınki randevuyu iptal etme. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.3 | Yarınki randevuyu iptal et. | Yarınki randevuyu iptal etmeyin. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.3 | Yarınki randevuyu iptal et. | Yarınki randevuyu iptal etmeyiniz. | AÇIK: negation-fix-calendar-cancel |
| calendar_cancel | generated | d.cancel_event.4 | Toplantıyı takvimden sil. | Toplantıyı takvimden silme. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.4 | Toplantıyı takvimden sil. | Toplantıyı takvimden silmeyin. | silen araca gitmez |
| calendar_cancel | generated | d.cancel_event.4 | Toplantıyı takvimden sil. | Toplantıyı takvimden silmeyiniz. | silen araca gitmez |
| evolution_cancel | generated | ev.cancel.1 | Bu geliştirmeyi iptal et. | Bu geliştirmeyi iptal etme. | AÇIK: negation-fix-evolution-cancel |
| evolution_cancel | generated | ev.cancel.1 | Bu geliştirmeyi iptal et. | Bu geliştirmeyi iptal etmeyin. | AÇIK: negation-fix-evolution-cancel |
| evolution_cancel | generated | ev.cancel.1 | Bu geliştirmeyi iptal et. | Bu geliştirmeyi iptal etmeyiniz. | AÇIK: negation-fix-evolution-cancel |
| evolution_cancel | generated | ev.cancel.2 | Geliştirmeden vazgeç. | Geliştirmeden vazgeçme. | AÇIK: negation-fix-evolution-cancel |
| evolution_cancel | generated | ev.cancel.2 | Geliştirmeden vazgeç. | Geliştirmeden vazgeçmeyin. | AÇIK: negation-fix-evolution-cancel |
| evolution_cancel | generated | ev.cancel.2 | Geliştirmeden vazgeç. | Geliştirmeden vazgeçmeyiniz. | AÇIK: negation-fix-evolution-cancel |
| macro_record_cancel | generated | macro.cancel.1 | Hareketi iptal et. | Hareketi iptal etme. | AÇIK: negation-fix-macro |
| macro_record_cancel | generated | macro.cancel.1 | Hareketi iptal et. | Hareketi iptal etmeyin. | AÇIK: negation-fix-macro |
| macro_record_cancel | generated | macro.cancel.1 | Hareketi iptal et. | Hareketi iptal etmeyiniz. | AÇIK: negation-fix-macro |
| macro_delete | generated | macro.delete.1 | Yeni mail sekmesi hareketini sil. | Yeni mail sekmesi hareketini silme. | AÇIK: negation-fix-macro |
| macro_delete | generated | macro.delete.1 | Yeni mail sekmesi hareketini sil. | Yeni mail sekmesi hareketini silmeyin. | AÇIK: negation-fix-macro |
| macro_delete | generated | macro.delete.1 | Yeni mail sekmesi hareketini sil. | Yeni mail sekmesi hareketini silmeyiniz. | AÇIK: negation-fix-macro |
| operator_cancel | generated | op.cancel.1 | İptal et. | İptal etme. | AÇIK: negation-fix-operator-exec-cancel |
| operator_cancel | generated | op.cancel.1 | İptal et. | İptal etmeyin. | AÇIK: negation-fix-operator-exec-cancel |
| operator_cancel | generated | op.cancel.1 | İptal et. | İptal etmeyiniz. | AÇIK: negation-fix-operator-exec-cancel |
| operator_cancel | generated | op.cancel.2 | Dur. | Durma. | silen araca gitmez |
| operator_cancel | generated | op.cancel.2 | Dur. | Durmayın. | silen araca gitmez |
| operator_cancel | generated | op.cancel.2 | Dur. | Durmayınız. | silen araca gitmez |
| document_delete | generated | doc.delete.first_word | Bu dosyayı sil. | Bu dosyayı silme. | silen araca gitmez |
| document_delete | generated | doc.delete.first_word | Bu dosyayı sil. | Bu dosyayı silmeyin. | silen araca gitmez |
| document_delete | generated | doc.delete.first_word | Bu dosyayı sil. | Bu dosyayı silmeyiniz. | silen araca gitmez |
| artifact_delete | generated | art.delete.asks_first | Bunu sil. | Bunu silme. | silen araca gitmez |
| artifact_delete | generated | art.delete.asks_first | Bunu sil. | Bunu silmeyin. | silen araca gitmez |
| artifact_delete | generated | art.delete.asks_first | Bunu sil. | Bunu silmeyiniz. | silen araca gitmez |
| exec_cancel | generated | exec.cancel.1 | Bunu iptal et. | Bunu iptal etme. | AÇIK: negation-fix-operator-exec-cancel |
| exec_cancel | generated | exec.cancel.1 | Bunu iptal et. | Bunu iptal etmeyin. | AÇIK: negation-fix-operator-exec-cancel |
| exec_cancel | generated | exec.cancel.1 | Bunu iptal et. | Bunu iptal etmeyiniz. | AÇIK: negation-fix-operator-exec-cancel |
| exec_cancel | generated | exec.cancel.2 | Vazgeç. | Vazgeçme. | AÇIK: negation-fix-discard |
| exec_cancel | generated | exec.cancel.2 | Vazgeç. | Vazgeçmeyin. | AÇIK: negation-fix-discard |
| exec_cancel | generated | exec.cancel.2 | Vazgeç. | Vazgeçmeyiniz. | AÇIK: negation-fix-discard |
| exec_cancel | generated | b51.exec_cancel.1 | Bu işi iptal et. | Bu işi iptal etme. | AÇIK: negation-fix-operator-exec-cancel |
| exec_cancel | generated | b51.exec_cancel.1 | Bu işi iptal et. | Bu işi iptal etmeyin. | AÇIK: negation-fix-operator-exec-cancel |
| exec_cancel | generated | b51.exec_cancel.1 | Bu işi iptal et. | Bu işi iptal etmeyiniz. | AÇIK: negation-fix-operator-exec-cancel |
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
| native_uninstall | generated | nativeapps.uninstall.canonical | Kurulumu kaldır. | Kurulumu kaldırma. | AÇIK: negation-fix-native-uninstall |
| native_uninstall | generated | nativeapps.uninstall.canonical | Kurulumu kaldır. | Kurulumu kaldırmayın. | AÇIK: negation-fix-native-uninstall |
| native_uninstall | generated | nativeapps.uninstall.canonical | Kurulumu kaldır. | Kurulumu kaldırmayınız. | AÇIK: negation-fix-native-uninstall |
| native_uninstall | generated | nativeapps.uninstall.app | Uygulamayı kaldır. | Uygulamayı kaldırma. | AÇIK: negation-fix-native-uninstall |
| native_uninstall | generated | nativeapps.uninstall.app | Uygulamayı kaldır. | Uygulamayı kaldırmayın. | AÇIK: negation-fix-native-uninstall |
| native_uninstall | generated | nativeapps.uninstall.app | Uygulamayı kaldır. | Uygulamayı kaldırmayınız. | AÇIK: negation-fix-native-uninstall |
| discard | generated | mc.discard.mail.2 | Vazgeç. | Vazgeçme. | AÇIK: negation-fix-discard |
| discard | generated | mc.discard.mail.2 | Vazgeç. | Vazgeçmeyin. | AÇIK: negation-fix-discard |
| discard | generated | mc.discard.mail.2 | Vazgeç. | Vazgeçmeyiniz. | AÇIK: negation-fix-discard |
| process_stop | generated | op.process.stop.1 | Chrome'u sonlandır. | Chrome'u sonlandırma. | AÇIK: negation-fix-process-stop |
| process_stop | generated | op.process.stop.1 | Chrome'u sonlandır. | Chrome'u sonlandırmayın. | AÇIK: negation-fix-process-stop |
| process_stop | generated | op.process.stop.1 | Chrome'u sonlandır. | Chrome'u sonlandırmayınız. | AÇIK: negation-fix-process-stop |
| process_stop | generated | op.process.stop.2 | Not Defteri'ni sonlandır. | Not Defteri'ni sonlandırma. | AÇIK: negation-fix-process-stop |
| process_stop | generated | op.process.stop.2 | Not Defteri'ni sonlandır. | Not Defteri'ni sonlandırmayın. | AÇIK: negation-fix-process-stop |
| process_stop | generated | op.process.stop.2 | Not Defteri'ni sonlandır. | Not Defteri'ni sonlandırmayınız. | AÇIK: negation-fix-process-stop |
| release_rollback | generated | ev.rollback.1 | Önceki sürüme dön. | Önceki sürüme dönme. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.1 | Önceki sürüme dön. | Önceki sürüme dönmeyin. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.1 | Önceki sürüme dön. | Önceki sürüme dönmeyiniz. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.2 | Eski sürüme geri al. | Eski sürüme geri alma. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.2 | Eski sürüme geri al. | Eski sürüme geri almayın. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.2 | Eski sürüme geri al. | Eski sürüme geri almayınız. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.3 | Bir önceki sürüme geri dön. | Bir önceki sürüme geri dönme. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.3 | Bir önceki sürüme geri dön. | Bir önceki sürüme geri dönmeyin. | AÇIK: negation-fix-release-rollback |
| release_rollback | generated | ev.rollback.3 | Bir önceki sürüme geri dön. | Bir önceki sürüme geri dönmeyiniz. | AÇIK: negation-fix-release-rollback |
| memory_forget | incident | B16 2026-09-13 |  | Bunu unutma. | silen araca gitmez |
| memory_forget | incident | öneri 2026-10-05 gerçek cihaz cümlesi |  | Hafızadan bunu unutma. | silen araca gitmez |
| research_cancel | incident | B27 2026-09-14 |  | Araştırmayı iptal etme. | silen araca gitmez |
| watch_forget_all | incident | watch-voice-inspector-2 bulgu 1 |  | Nöbetleri silme. | silen araca gitmez |
| watch_forget_all | ablative | watch-voice-inspector-2 bulgu 2 |  | Nöbetlerimden birini sil. | silen araca gitmez |
| watch_forget_all | ablative | pano 2026-10-05 06:33 denetleyici |  | Nöbetlerden fiyatı kaldır. | silen araca gitmez |
| capability_cancel | olumlu yok | - | - | - | korpusun cümleleri ('Vazgeç, yapma.', 'Vazgeçtim.') emirle bitmiyor: son kelime zaten olumsuz ya da geçmiş zaman |
