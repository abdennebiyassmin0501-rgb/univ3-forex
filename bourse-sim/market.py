"""بيانات السوق: أسعار تاريخية حقيقية، للقراءة فقط.

لا يوجد في المشروع أي كود يكتب في ملف البيانات أو يعدّله؛
صفقات الطلبة تُحفظ في قاعدة بيانات منفصلة ولا تلمس هذه الأسعار.
"""
import csv
import os
from types import MappingProxyType

DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "F_2023.csv")

STOCK = MappingProxyType({
    "company": "Ford Motor Company",
    "company_ar": "فورد موتور",
    "symbol": "F",
    "exchange": "بورصة نيويورك (NYSE)",
    "currency": "USD",
    "source": "Yahoo Finance (بيانات يومية تاريخية)",
    "period": "من 1 جوان 2023 إلى 28 جويلية 2023",
})

# الرصيد الافتراضي الأولي لكل طالب بالدولار الأمريكي
INITIAL_CASH_USD = 500
# فترة المحاكاة (مقتطعة من ملف البيانات الحقيقي دون أي تعديل على الأسعار)
FIRST_DATE, LAST_DATE = "2023-06-01", "2023-07-28"
# عدد الشموع الظاهرة عند بداية المحاكاة (حتى 28 جوان 2023)، ثم 21 مرحلة
START_VISIBLE = 19


class Candle(tuple):
    __slots__ = ()
    date = property(lambda s: s[0])
    open = property(lambda s: s[1])
    high = property(lambda s: s[2])
    low = property(lambda s: s[3])
    close = property(lambda s: s[4])
    volume = property(lambda s: s[5])

    def as_dict(self):
        return {"d": self.date, "o": self.open, "h": self.high,
                "l": self.low, "c": self.close, "v": self.volume}


def _load():
    with open(DATA_FILE, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    candles = tuple(
        Candle((r["date"], float(r["open"]), float(r["high"]), float(r["low"]),
                float(r["close"]), int(r["volume"])))
        for r in rows
    )
    dates = [c.date for c in candles]
    assert dates == sorted(dates) and len(set(dates)) == len(dates), "dates must be sorted/unique"
    return candles


ALL_CANDLES = _load()  # الملف كاملًا كما نُزِّل من Yahoo Finance
# tuple ثابتة: لا يمكن تعديلها أثناء التشغيل
CANDLES = tuple(c for c in ALL_CANDLES if FIRST_DATE <= c.date <= LAST_DATE)
N = len(CANDLES)
assert 0 < START_VISIBLE < N


def visible(step):
    """الشموع المسموح للطالب برؤيتها فقط (لا يُرسل أي شيء من المستقبل)."""
    step = max(0, min(int(step), N))
    return CANDLES[:step]
