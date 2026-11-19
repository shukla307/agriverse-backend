"""catalog/matching.py: any spelling → machine types (decision §13, 05 §6)."""

import json
from pathlib import Path

import pytest

from catalog.matching import close_enough, edit_distance, normalise, understand
from catalog.models import JobCategory, MachineType

CASES = json.loads((Path(__file__).parent / "spelling_cases.json").read_text())


@pytest.fixture
def names(db):
    from catalog.matching import catalogue_names

    return catalogue_names()


@pytest.mark.parametrize("typed, codes", CASES["finds"], ids=[c[0] for c in CASES["finds"]])
def test_finds(names, typed, codes):
    u = understand(typed, names)
    assert u.machine_codes == set(codes)
    assert u.rest == []


@pytest.mark.parametrize("typed, job", CASES["job_names"])
def test_job_name_means_all_its_machines(names, typed, job):
    expected = set(JobCategory.objects.get(code=job).machine_types.filter(is_active=True).values_list("code", flat=True))
    assert expected and understand(typed, names).machine_codes == expected


@pytest.mark.parametrize("typed", CASES["finds_nothing"])
def test_finds_nothing(names, typed):
    assert understand(typed, names).machine_codes == set()


def test_other_words_are_left_for_brand_and_model(names):
    u = understand("Mahindra 575 tractor chahiye", names)
    assert u.machine_codes == {"tractor", "mini_tractor"}
    assert u.rest == ["mahindra", "575"]


def test_inactive_and_other_types_are_never_matched(names):
    assert understand("jcb", names).machine_codes == set()  # JCB is "later" (inactive)
    assert understand("अन्य", names).machine_codes == set()
    MachineType.objects.filter(code="jcb").update(is_active=True)
    from catalog.matching import catalogue_names

    assert understand("jcb", catalogue_names()).machine_codes == {"jcb"}


def test_normalise():
    assert normalise("  ROTAVATOR,  Mahindra! ") == "rotavator mahindra"
    assert normalise("फ़सल") == normalise("फसल")  # nukta, precomposed or combining
    assert normalise("फ़सल") == normalise("फसल")
    assert normalise("गाँव") == normalise("गांव")  # chandrabindu
    assert normalise("ट्र‍ैक्टर") == normalise("ट्रैक्टर")  # zero-width joiner


@pytest.mark.parametrize(
    "typed, name, ok",
    [
        ("hal", "hal", True), ("hat", "hal", False),  # 3 letters: exact only
        ("rot", "rotavator", True), ("ro", "rotavator", False),  # start: from 3 letters
        ("trolly", "troli", False), ("tralee", "trali", False),
        ("tracter", "tractor", True), ("trektor", "tractor", True),  # 7 letters: 2 mistakes
        ("traktor", "tractor", True), ("traktr", "tractor", False),  # 6 letters: 1 mistake
        ("tirktaar", "tractor", False), ("ट्रेक्टर", "ट्रैक्टर", True), ("रोटाव", "रोटावेटर", True), ("रोटा", "रोटावेटर", False),
    ],
)
def test_close_enough(typed, name, ok):
    assert close_enough(typed, name) is ok


def test_hindi_letters_are_counted_like_a_reader_counts():
    from catalog.matching import letters

    assert letters("ट्रैक्टर") == 5 and letters("हल") == 2 and letters("tractor") == 7


def test_edit_distance():
    assert edit_distance("kitten", "sitting", 5) == 3
    assert edit_distance("abc", "abcdefgh", 2) == 3  # stops early: "more than the limit"
