"""
The Hindi words of a push (Phase 5 slice 7; 07 §4, glossary §8).

A push must show even when the app is closed, so the SERVER writes its text. This is a copy of the app's
words (app_hi.arb notif*, timeMorning…, dayToday…) and of its rules (notification_words.dart notificationText,
hindi_time.dart HindiTime.hour, booking_words.dart bookingDay). Keep them in step: the cases in
notifications/tests/push_text_cases.json must give the same sentence here AND in the app
(agriverse_app/test/fixtures/push_text_cases.json is a copy; the app's test checks the two files are equal).
"""

import re
from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

TITLE = "खेती साधन"  # push_title (approved 2026-11-27)

WEEKDAYS = ["सोम", "मंगल", "बुध", "गुरु", "शुक्र", "शनि", "रवि"]  # Monday = 0, like Python and HindiTime

TEXTS = {
    "booking_requested": "{renter} आपकी मशीन ({machine}) {date} {time} के लिए बुक करना चाहते हैं। हाँ या ना बताएँ।",
    "booking_accepted": "{owner} ने आपकी बुकिंग पक्की कर दी। अब फ़ोन करें।",
    "booking_rejected": "{owner} ने इस बार मना कर दिया। दूसरी मशीन देखें।",
    "booking_expired": "मालिक ने समय पर जवाब नहीं दिया, बुकिंग रद्द हो गई।",
    "booking_expired_owner": "आपने समय पर जवाब नहीं दिया, {renter} की बुकिंग रद्द हो गई।",
    "booking_cancelled_by_renter": "{renter} ने {date} की बुकिंग रद्द कर दी।",
    "booking_cancelled_by_owner": "{owner} ने {date} की बुकिंग रद्द कर दी।",
    "booking_completed": "{date} का काम पूरा हुआ।",
}


def hour(h: int) -> str:
    """HindiTime.hour: 4–11 सुबह, 12–15 दोपहर, 16–19 शाम, else रात; 12-hour numbers, never AM/PM."""
    h24 = h % 24
    h12 = 12 if h24 % 12 == 0 else h24 % 12
    if 4 <= h24 <= 11:
        return f"सुबह {h12} बजे"
    if 12 <= h24 <= 15:
        return f"दोपहर {h12} बजे"
    if 16 <= h24 <= 19:
        return f"शाम {h12} बजे"
    return f"रात {h12} बजे"


def day(d: date, today: date) -> str:
    """bookingDay: आज / कल / "24 मंगल"."""
    diff = (d - today).days
    if diff == 0:
        return "आज"
    if diff == 1:
        return "कल"
    return f"{d.day} {WEEKDAYS[d.weekday()]}"


def text(kind: str, params: dict, now: datetime) -> str:
    """The sentence for one notification, as the app's notificationText would write it at `now`."""
    date_word = time_words = ""
    try:
        start = datetime.fromisoformat(params["start"]).astimezone(IST)
        end = datetime.fromisoformat(params["end"]).astimezone(IST)
    except (KeyError, TypeError, ValueError):
        start = end = None
    if start is not None:
        date_word = day(start.date(), now.astimezone(IST).date())
        to = start.hour + int((end - start).total_seconds() // 3600)
        if to <= 24:  # an old per-day group runs over several days: then only its first day is named
            time_words = f"{hour(start.hour)} से {hour(to)} तक"
    machine = " + ".join(p for p in (params.get("machine") or "", params.get("implement")) if p is not None)
    sentence = TEXTS.get(kind, TEXTS["booking_expired"]).format(
        renter=params.get("renter") or "", owner=params.get("owner") or "",
        machine=machine, date=date_word, time=time_words,
    )
    return re.sub(" {2,}", " ", sentence)
