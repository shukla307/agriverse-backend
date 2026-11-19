"""
Turning what a farmer typed or said into machine types (decision §13, 05-data-model.md §6).

The SAME rules will run on the phone for offline suggestions (Phase 4 slice 5), so they are kept small
and written out in docs/05-data-model.md §6. If you change a rule here, change it in the app too.

  1. normalise():  small letters, one space between words, Hindi variants folded (फ़→फ, ँ→ं, no ZWJ/ZWNJ),
                   punctuation → space.
  2. words:        filler words ("मुझे", "चाहिए", "kiraye"…) are dropped, so a spoken sentence still works.
  3. phrases:      1, 2 and 3 words in a row are compared with every name / alias (longest first, so
                   "mini tractor" wins over "tractor"; words used by a match aren't used again).
  4. close enough: equal; or the alias starts with it (3+ letters typed); or at most 1 change
                   (4–6 letters) / 2 changes (7+ letters). Up to 3 letters: exact or start only. "Letters" as a
                   reader counts them: Hindi vowel signs and ् are not letters (ट्रैक्टर = 5), but changing one is a change.
  5. one word of a longer name, exactly: "हल" → एमबी हल + डिस्क हल, "आलू" → both potato machines
                   (no spelling mistakes allowed here; too many short words would match by accident).
  6. job names:    "जुताई" means every machine type that does that job.
Words that match no machine are returned as `rest`; search then looks for them in brand, model and
names typed under अन्य (pg_trgm).
"""

import unicodedata
from dataclasses import dataclass, field

from .models import JobCategory, MachineType

# Precomposed nukta letters → base letter (क़ ख़ ग़ ज़ ड़ ढ़ फ़ य़). The separate nukta sign is removed below.
_NUKTA_LETTERS = str.maketrans({
    "क़": "क", "ख़": "ख", "ग़": "ग", "ज़": "ज",
    "ड़": "ड", "ढ़": "ढ", "फ़": "फ", "य़": "य",
})
_DROP = dict.fromkeys(map(ord, "़‌‍"), None)  # nukta sign, ZWNJ, ZWJ
_CHANDRABINDU = str.maketrans({"ँ": "ं"})  # ँ → ं

# Words people add around the machine's name ("मुझे ट्रैक्टर चाहिए", "tractor kiraye par"). Kept short on
# purpose: a word that might be a brand or a village must not be on this list.
FILLER_WORDS = frozenset(
    "मुझे हमें चाहिए चाहिये का की के है हैं एक किराये किराए किराया पर भाड़े भाडे वाला वाली वाले मशीन "
    "और से में को लिए दो दे दें please mujhe hume chahiye chahie ka ki ke hai ek kiraye kiraya kiraaye par "
    "pe bhade wala wali wale machine aur se me mein ko liye do de chaiye chahiye".split()
)


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "").lower()
    text = text.translate(_NUKTA_LETTERS).translate(_DROP).translate(_CHANDRABINDU)
    # Keep letters, numbers and combining marks (Hindi vowel signs and ् are marks; Python's \w drops them).
    text = "".join(c if unicodedata.category(c)[0] in "LNM" else " " for c in text)
    return " ".join(text.split())


def edit_distance(a: str, b: str, limit: int) -> int:
    """Levenshtein distance, stopping early once it's surely above `limit`."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        if min(cur) > limit:
            return limit + 1
        prev = cur
    return prev[-1]


def letters(text: str) -> int:
    """Letters as a reader counts them: ट्रैक्टर is 5 (ट र क ट र), not 8 code points. Vowel signs and ् don't count."""
    return sum(1 for c in text if unicodedata.category(c)[0] != "M" and c != " ")


def allowed_mistakes(typed: str) -> int:
    n = letters(typed)
    return 0 if n <= 3 else 1 if n <= 6 else 2


def close_enough(typed: str, name: str) -> bool:
    if typed == name:
        return True
    if letters(typed) >= 3 and name.startswith(typed):
        return True
    limit = allowed_mistakes(typed)
    return limit > 0 and edit_distance(typed, name, limit) <= limit


@dataclass
class Understood:
    machine_codes: set[str] = field(default_factory=set)
    rest: list[str] = field(default_factory=list)  # words that are not a machine: brand, model, typed names

    @property
    def empty(self) -> bool:
        return not self.machine_codes and not self.rest


def catalogue_names() -> list[tuple[str, frozenset[str]]]:
    """(normalised name or alias, machine codes it means). One query for types, one for jobs."""
    names: list[tuple[str, frozenset[str]]] = []
    for m in MachineType.objects.filter(is_active=True).exclude(code="other"):
        for n in {m.name_hi, m.name_en, m.code.replace("_", " "), *m.search_aliases}:
            if normalise(n):
                names.append((normalise(n), frozenset({m.code})))
    for job in JobCategory.objects.filter(is_active=True).exclude(code="other").prefetch_related("machine_types"):
        codes = frozenset(m.code for m in job.machine_types.all() if m.is_active)
        for n in {job.name_hi, job.name_en}:
            if normalise(n) and codes:
                names.append((normalise(n), codes))
    return names


def understand(q: str, names: list[tuple[str, frozenset[str]]] | None = None) -> Understood:
    words = [w for w in normalise(q).split() if w not in FILLER_WORDS]
    names = catalogue_names() if names is None else names
    out, used = Understood(), [False] * len(words)
    for size in (3, 2, 1):  # longest phrase first
        for start in range(len(words) - size + 1):
            if any(used[start:start + size]):
                continue
            phrase = " ".join(words[start:start + size])
            # A phrase of several words must match a name of several words (no prefix tricks across words).
            hits = [codes for name, codes in names
                    if (size == 1 or " " in name) and close_enough(phrase, name)
                    or size == 1 and " " in name and phrase in name.split()]
            if hits:
                for codes in hits:
                    out.machine_codes |= codes
                used[start:start + size] = [True] * size
    out.rest = [w for w, u in zip(words, used) if not u]
    return out
