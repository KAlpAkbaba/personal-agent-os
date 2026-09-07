"""Generate the M21 mail & calendar fixtures and their ground truth.

Usage (from services/api): uv run python ../../scripts/tests/make-mail-calendar-fixtures.py ../../services/api/tests/fixtures/mail_calendar

Deterministic: fixed dates, fixed ids, LF newlines. ``mailbox.json`` is what the fake IMAP
server serves (decoded text; the server encodes RFC 2047 / MIME on the wire), ``calendar.ics``
is what the CalDAV/ICS providers parse, ``truth.json`` is what the services must answer for a
frozen "now" — computed here from the same data with dateutil, never typed by hand.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dateutil import rrule

TZ = ZoneInfo("Europe/Istanbul")
NOW = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)  # Wednesday
OWNER = ("Alp Akbaba", "alp@example.com")
PEOPLE = {
    "ali": ("Ali Yılmaz", "ali.yilmaz@example.com"),
    "ayse": ("Ayşe Kaya", "ayse.kaya@example.com"),
    "mehmet": ("Mehmet Demir", "mehmet.demir@example.com"),
    "fatura": ("Örnek Enerji", "fatura@ornekenerji.example"),
    "haber": ("Haftalık Bülten", "bulten@haber.example"),
    "zeynep": ("Zeynep Arslan", "zeynep.arslan@example.com"),
}


def addr(key: str) -> dict:
    name, email = PEOPLE.get(key, OWNER) if key != "owner" else OWNER
    return {"name": name, "email": email}


def msg(uid: int, folder: str, frm: str, to: list[str], subject: str, when: datetime, body: str, *, seen: bool = True,
        cc: list[str] | None = None, attachments: list[dict] | None = None, html: str | None = None,
        in_reply_to: str | None = None, references: list[str] | None = None, thread: str | None = None) -> dict:
    message_id = f"<m{uid}@fixture.example>"
    return {
        "uid": uid,
        "folder": folder,
        "message_id": message_id,
        "from": addr(frm),
        "to": [addr(t) for t in to],
        "cc": [addr(c) for c in (cc or [])],
        "subject": subject,
        "date": when.isoformat(),
        "flags": ["\\Seen"] if seen else [],
        "body_text": body,
        "body_html": html,
        "attachments": attachments or [],
        "in_reply_to": in_reply_to,
        "references": references or [],
        "thread": thread or subject,
    }


def d(day: int, hour: int, minute: int = 0, month: int = 9) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ)


MESSAGES: list[dict] = []
uid = 100

# Thread "Proje planı" (4 messages, alternating), the latest unread from Ali
plan = [
    ("ali", ["owner"], d(1, 9, 15), "Merhaba Alp, proje planının ilk taslağını ekte gönderiyorum. Görüşlerini bekliyorum.", [{"filename": "proje-plani-v1.docx", "size": 48213, "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}], True),
    ("owner", ["ali"], d(1, 14, 40), "Teşekkürler Ali, ikinci aşamayı bir hafta öne çekebilir miyiz?", [], True),
    ("ali", ["owner"], d(2, 10, 5), "Öne çekebiliriz; test süresini kısaltmamız gerekir.", [], True),
    ("ali", ["owner"], d(8, 17, 30), "Güncel planı ekledim: ikinci aşama 15 Eylül'de başlıyor. Onaylarsan ekibe duyuruyorum.", [{"filename": "proje-plani-v2.docx", "size": 50122, "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}], False),
]
prev_ids: list[str] = []
for i, (frm, to, when, body, atts, seen) in enumerate(plan):
    uid += 1
    folder = "Gönderilmiş" if frm == "owner" else "INBOX"
    subject = "Proje planı" if i == 0 else "Re: Proje planı"
    m = msg(uid, folder, frm, to, subject, when, body, seen=seen, attachments=atts,
            in_reply_to=prev_ids[-1] if prev_ids else None, references=list(prev_ids), thread="Proje planı")
    MESSAGES.append(m)
    prev_ids.append(m["message_id"])

# Thread "Fatura" (2), both read, one HTML-only body
uid += 1
MESSAGES.append(msg(uid, "INBOX", "fatura", ["owner"], "Eylül 2026 elektrik faturanız", d(3, 8, 0),
                    "Sayın Alp Akbaba, Eylül 2026 dönemi elektrik faturanız 1.284,50 TL olarak düzenlenmiştir. Son ödeme tarihi 20 Eylül 2026.",
                    html="<html><body><p>Sayın Alp Akbaba,</p><p>Eylül 2026 dönemi elektrik faturanız <b>1.284,50 TL</b> olarak düzenlenmiştir.</p><p>Son ödeme tarihi 20 Eylül 2026.</p></body></html>",
                    attachments=[{"filename": "fatura-2026-09.pdf", "size": 118004, "content_type": "application/pdf"}], thread="Fatura"))
uid += 1
MESSAGES.append(msg(uid, "Gönderilmiş", "owner", ["fatura"], "Re: Eylül 2026 elektrik faturanız", d(3, 9, 12), "Faturayı aldım, teşekkürler.", in_reply_to=MESSAGES[-1]["message_id"], references=[MESSAGES[-1]["message_id"]], thread="Fatura"))

# Unread run in INBOX (with the Ali one above: 6 unread total)
unread = [
    ("ayse", "Yarınki toplantı", d(8, 9, 30), "Yarın 10:00'daki ekip toplantısına katılabilecek misin? Gündem: sürüm takvimi."),
    ("mehmet", "Sunum dosyası", d(8, 11, 5), "Q3 sunumunun son halini ekte bulabilirsin."),
    ("zeynep", "Öğle yemeği?", d(8, 12, 45), "Perşembe öğle yemeğine ne dersin? 12:30 uygun mu?"),
    ("haber", "Haftalık bülten — 36. hafta", d(8, 7, 0), "Bu hafta: yapay zekâ ajanları, yerel modeller ve gizlilik."),
    ("ayse", "Bütçe tablosu güncellendi", d(8, 16, 20), "Bütçe tablosunda Yazılım kalemini 8.000 TL olarak güncelledim."),
]
for frm, subject, when, body in unread:
    uid += 1
    atts = [{"filename": "sunum-q3.pptx", "size": 33632, "content_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}] if subject == "Sunum dosyası" else []
    MESSAGES.append(msg(uid, "INBOX", frm, ["owner"], subject, when, body, seen=False, attachments=atts))

# Older read mail in INBOX and Arşiv (filler with real Turkish, deterministic)
filler = [
    ("INBOX", "mehmet", "Toplantı notları", d(4, 15, 0), "Dünkü toplantının notlarını paylaşıyorum: kararlar ve sorumlular listede."),
    ("INBOX", "ayse", "Tatil planı", d(5, 10, 30), "14 Eylül bayram tatili; ofis kapalı."),
    ("INBOX", "haber", "Haftalık bülten — 35. hafta", d(1, 7, 0), "Bu hafta: tarayıcı ajanları ve erişilebilirlik ağaçları."),
    ("Arşiv", "ali", "Sözleşme taslağı", d(20, 11, 0, month=8), "Sözleşme taslağının ilk sürümü ekte. Ödeme süresi 30 gün olarak yazıldı."),
    ("Arşiv", "zeynep", "Fotoğraflar", d(22, 18, 40, month=8), "Geçen haftaki etkinliğin fotoğraflarını paylaşıyorum."),
    ("Arşiv", "mehmet", "Sunucu bakımı", d(25, 9, 0, month=8), "Cumartesi 02:00-04:00 arasında sunucu bakımı yapılacak."),
]
for folder, frm, subject, when, body in filler:
    uid += 1
    atts = [{"filename": "sozlesme-taslak.docx", "size": 37178, "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}] if subject == "Sözleşme taslağı" else []
    MESSAGES.append(msg(uid, folder, frm, ["owner"], subject, when, body, attachments=atts))

# a few more archive rows to reach a realistic count
for i in range(20):
    uid += 1
    who = ["ali", "ayse", "mehmet", "zeynep", "haber"][i % 5]
    when = d(1 + (i % 15), 8 + (i % 9), 5 * (i % 12), month=8)
    MESSAGES.append(msg(uid, "Arşiv", who, ["owner"], f"Arşiv notu {i + 1}", when, f"Arşivlenmiş {i + 1}. not. Bu satır sabit bir dolgu metnidir."))

MESSAGES.sort(key=lambda m: (m["folder"], m["uid"]))

# --------------------------------------------------------------------------- calendar
ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//PagentOS fixtures//M21//TR
CALSCALE:GREGORIAN
BEGIN:VTIMEZONE
TZID:Europe/Istanbul
BEGIN:STANDARD
DTSTART:20160907T000000
TZOFFSETFROM:+0300
TZOFFSETTO:+0300
TZNAME:+03
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:ev-dis@fixture.example
DTSTAMP:20260901T080000Z
DTSTART;TZID=Europe/Istanbul:20260910T150000
DTEND;TZID=Europe/Istanbul:20260910T160000
SUMMARY:Diş hekimi
LOCATION:Kadıköy
END:VEVENT
BEGIN:VEVENT
UID:ev-ekip@fixture.example
DTSTAMP:20260901T080000Z
DTSTART;TZID=Europe/Istanbul:20260908T100000
DTEND;TZID=Europe/Istanbul:20260908T110000
RRULE:FREQ=WEEKLY;BYDAY=TU;COUNT=10
EXDATE;TZID=Europe/Istanbul:20260922T100000
SUMMARY:Haftalık ekip toplantısı
END:VEVENT
BEGIN:VEVENT
UID:ev-bayram@fixture.example
DTSTAMP:20260901T080000Z
DTSTART;VALUE=DATE:20260914
DTEND;VALUE=DATE:20260915
SUMMARY:Bayram (ofis kapalı)
END:VEVENT
BEGIN:VEVENT
UID:ev-musteri@fixture.example
DTSTAMP:20260901T080000Z
DTSTART;TZID=Europe/Istanbul:20260911T140000
DTEND;TZID=Europe/Istanbul:20260911T150000
SUMMARY:Müşteri görüşmesi
END:VEVENT
BEGIN:VEVENT
UID:ev-doktor@fixture.example
DTSTAMP:20260901T080000Z
DTSTART;TZID=Europe/Istanbul:20260911T143000
DTEND;TZID=Europe/Istanbul:20260911T153000
SUMMARY:Doktor kontrolü
END:VEVENT
BEGIN:VEVENT
UID:ev-spor@fixture.example
DTSTAMP:20260901T080000Z
DTSTART;TZID=Europe/Istanbul:20260908T070000
DTEND;TZID=Europe/Istanbul:20260908T080000
RRULE:FREQ=DAILY;COUNT=5
SUMMARY:Spor
END:VEVENT
BEGIN:VEVENT
UID:ev-ogle@fixture.example
DTSTAMP:20260901T080000Z
DTSTART;TZID=Europe/Istanbul:20260909T123000
DTEND;TZID=Europe/Istanbul:20260909T133000
SUMMARY:Öğle yemeği (Zeynep)
END:VEVENT
END:VCALENDAR
"""


def expand() -> list[dict]:
    """The same expansion the provider must do, done here with dateutil to write the truth."""
    out: list[dict] = []

    def add(uid_: str, summary: str, start: datetime, end: datetime, all_day: bool = False) -> None:
        out.append({"uid": uid_, "summary": summary, "start": start.isoformat(), "end": end.isoformat(), "all_day": all_day})

    add("ev-dis@fixture.example", "Diş hekimi", d(10, 15), d(10, 16))
    ekip = rrule.rrule(rrule.WEEKLY, byweekday=rrule.TU, count=10, dtstart=d(8, 10))
    for s in ekip:
        if s == d(22, 10):
            continue
        add("ev-ekip@fixture.example", "Haftalık ekip toplantısı", s, s + timedelta(hours=1))
    add("ev-bayram@fixture.example", "Bayram (ofis kapalı)", datetime(2026, 9, 14, tzinfo=TZ), datetime(2026, 9, 15, tzinfo=TZ), True)
    add("ev-musteri@fixture.example", "Müşteri görüşmesi", d(11, 14), d(11, 15))
    add("ev-doktor@fixture.example", "Doktor kontrolü", d(11, 14, 30), d(11, 15, 30))
    for s in rrule.rrule(rrule.DAILY, count=5, dtstart=d(8, 7)):
        add("ev-spor@fixture.example", "Spor", s, s + timedelta(hours=1))
    add("ev-ogle@fixture.example", "Öğle yemeği (Zeynep)", d(9, 12, 30), d(9, 13, 30))
    out.sort(key=lambda e: e["start"])
    return out


def free_slots(events: list[dict], day: date, start_h: int, end_h: int, minutes: int) -> list[dict]:
    busy = []
    for e in events:
        s = datetime.fromisoformat(e["start"])
        t = datetime.fromisoformat(e["end"])
        if e["all_day"]:
            continue
        if s.date() == day:
            busy.append((s, t))
    busy.sort()
    cursor = datetime(day.year, day.month, day.day, start_h, tzinfo=TZ)
    end = datetime(day.year, day.month, day.day, end_h, tzinfo=TZ)
    slots = []
    for s, t in busy:
        if s - cursor >= timedelta(minutes=minutes):
            slots.append({"start": cursor.isoformat(), "end": s.isoformat()})
        cursor = max(cursor, t)
    if end - cursor >= timedelta(minutes=minutes):
        slots.append({"start": cursor.isoformat(), "end": end.isoformat()})
    return slots


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "mailbox.json").write_text(json.dumps({"owner": {"name": OWNER[0], "email": OWNER[1]}, "folders": ["INBOX", "Gönderilmiş", "Arşiv"], "messages": MESSAGES}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (out / "calendar.ics").write_text(ICS, encoding="utf-8", newline="\n")
    events = expand()
    inbox = [m for m in MESSAGES if m["folder"] == "INBOX"]
    unread_inbox = [m for m in inbox if "\\Seen" not in m["flags"]]
    latest_ali = max((m for m in MESSAGES if m["from"]["email"] == PEOPLE["ali"][1]), key=lambda m: m["date"])
    plan_thread = sorted((m for m in MESSAGES if m["thread"] == "Proje planı"), key=lambda m: m["date"])
    today = [e for e in events if datetime.fromisoformat(e["start"]).date() == NOW.date() or (e["all_day"] and datetime.fromisoformat(e["start"]).date() <= NOW.date() < datetime.fromisoformat(e["end"]).date())]
    fri = date(2026, 9, 11)
    conflicts = [(a["uid"], b["uid"]) for i, a in enumerate(events) for b in events[i + 1:] if not a["all_day"] and not b["all_day"] and datetime.fromisoformat(a["start"]) < datetime.fromisoformat(b["end"]) and datetime.fromisoformat(b["start"]) < datetime.fromisoformat(a["end"])]
    truth = {
        "generated_by": "scripts/tests/make-mail-calendar-fixtures.py",
        "now": NOW.isoformat(),
        "timezone": "Europe/Istanbul",
        "mail": {
            "folders": ["INBOX", "Gönderilmiş", "Arşiv"],
            "counts": {f: sum(1 for m in MESSAGES if m["folder"] == f) for f in ["INBOX", "Gönderilmiş", "Arşiv"]},
            "unread_inbox": len(unread_inbox),
            "unread_inbox_uids": [m["uid"] for m in unread_inbox],
            "latest_from_ali": {"uid": latest_ali["uid"], "subject": latest_ali["subject"], "date": latest_ali["date"], "attachment": latest_ali["attachments"][0]["filename"]},
            "thread_proje_plani": [m["uid"] for m in plan_thread],
            "html_only_body_uid": next(m["uid"] for m in MESSAGES if m["body_html"]),
            "html_only_body_contains": "1.284,50 TL",
            "search": {"fatura": [m["uid"] for m in MESSAGES if "fatura" in m["subject"].casefold() or "fatura" in m["body_text"].casefold()], "bütçe": [m["uid"] for m in MESSAGES if "bütçe" in m["subject"].casefold() or "bütçe" in m["body_text"].casefold()]},
            "reply_to_latest_ali": {"to": PEOPLE["ali"][1], "subject": "Re: Proje planı", "in_reply_to": latest_ali["message_id"], "references": latest_ali["references"] + [latest_ali["message_id"]]},
        },
        "calendar": {
            "expanded_events": events,
            "event_count": len(events),
            "today": [e["summary"] for e in today],
            "today_uids": [e["uid"] for e in today],
            "ekip_occurrences": sum(1 for e in events if e["uid"] == "ev-ekip@fixture.example"),
            "exdate_skipped": "2026-09-22T10:00:00+03:00",
            "conflicts": conflicts,
            "free_slots_2026_09_11_60min_0900_1800": free_slots(events, fri, 9, 18, 60),
            "propose_dentist_move": {"uid": "ev-dis@fixture.example", "to_start": d(10, 16).isoformat(), "to_end": d(10, 17).isoformat(), "conflicts": []},
            "propose_new_1500_friday": {"start": d(11, 15).isoformat(), "end": d(11, 16).isoformat(), "conflicts": ["ev-doktor@fixture.example"]},
        },
    }
    (out / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {len(MESSAGES)} messages, {len(events)} event occurrences to {out}")


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
