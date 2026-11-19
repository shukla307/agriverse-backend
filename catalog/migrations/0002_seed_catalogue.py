"""
Starting catalogue. Names and aliases: docs/hindi-glossary.md §7 (all approved).
Jobs per machine: decision §4. Hours per acre: docs/05-data-model.md §3.4a.
Admin can change everything later; this only fills an empty database.
"""

from django.db import migrations

JOBS = [
    # code, name_hi, sort_order
    ("land_prep", "जुताई", 1),
    ("sowing", "बुवाई", 2),
    ("spraying", "छिड़काव", 3),
    ("harvesting", "कटाई", 4),
    ("threshing", "गहाई", 5),
    ("transport", "ढुलाई", 6),
    ("other", "अन्य", 99),
]

P, I = "power", "implement"
MACHINES = [
    # code, name_hi, kind, jobs, hours/acre, aliases, active
    ("tractor", "ट्रैक्टर", P, ["transport", "land_prep"], None, ["tractor", "tracter", "trektar", "ट्रेक्टर"], True),
    ("mini_tractor", "छोटा ट्रैक्टर", P, ["transport", "land_prep"], None, ["mini tractor", "chota tractor"], True),
    ("combine_harvester", "कंबाइन हार्वेस्टर", P, ["harvesting"], "0.5", ["combine", "harvester", "kambain", "कंबाइन"], True),
    ("power_tiller", "पावर टिलर", P, ["land_prep"], "3.0", ["power tiller", "tiller", "टिलर"], True),
    ("paddy_transplanter", "धान रोपाई मशीन", P, ["sowing"], "2.0", ["transplanter", "dhan ropai", "रोपाई"], True),
    ("drone_sprayer", "ड्रोन (दवा छिड़काव)", P, ["spraying"], "0.2", ["drone", "dron", "ड्रोन"], True),
    ("jcb", "जेसीबी", P, [], None, ["jcb", "जेसीबी"], False),  # "later" (decision §4)
    ("rotavator", "रोटावेटर", I, ["land_prep"], "1.5", ["rotavator", "rotaveter", "rotavetor", "rotawator"], True),
    ("cultivator", "कल्टीवेटर", I, ["land_prep"], "1.0", ["cultivator", "kaltivator", "kultivator"], True),
    ("plough_mb", "एमबी हल", I, ["land_prep"], "2.0", ["mb plough", "mb hal", "hal", "plow"], True),
    ("plough_disc", "डिस्क हल", I, ["land_prep"], "2.0", ["disc plough", "disk hal"], True),
    ("harrow", "हैरो", I, ["land_prep"], "1.0", ["harrow", "haro"], True),
    ("laser_leveller", "लेज़र लेवलर", I, ["land_prep"], "2.0", ["laser leveller", "leveler", "लेवलर"], True),
    ("seed_drill", "सीड ड्रिल", I, ["sowing"], "1.0", ["seed drill", "sidril"], True),
    ("super_seeder", "सुपर सीडर", I, ["sowing"], "1.5", ["super seeder", "super sidar"], True),
    ("happy_seeder", "हैप्पी सीडर", I, ["sowing"], "1.5", ["happy seeder"], True),
    ("potato_planter", "आलू बोने की मशीन", I, ["sowing"], "1.5", ["potato planter", "aalu planter"], True),
    ("boom_sprayer", "बूम स्प्रेयर", I, ["spraying"], "0.5", ["boom sprayer", "sprayer", "स्प्रेयर"], True),
    ("reaper", "रीपर", I, ["harvesting"], "1.0", ["reaper", "riper"], True),
    ("potato_digger", "आलू खोदने की मशीन", I, ["harvesting"], "1.5", ["potato digger", "aalu digger"], True),
    ("thresher", "थ्रेशर", I, ["threshing"], "2.0", ["thresher", "thresar", "थ्रेसर"], True),
    ("baler", "बेलर", I, ["threshing"], "1.0", ["baler", "bailer"], True),
    ("mulcher", "मल्चर", I, ["threshing"], "1.5", ["mulcher", "malchar"], True),
    ("trolley", "ट्रॉली", I, ["transport"], None, ["trolley", "troli", "trali", "ट्राली"], True),
]


def seed(apps, schema_editor):
    JobCategory = apps.get_model("catalog", "JobCategory")
    MachineType = apps.get_model("catalog", "MachineType")
    jobs = {}
    for code, name_hi, order in JOBS:
        jobs[code], _ = JobCategory.objects.get_or_create(
            code=code, defaults={"name_hi": name_hi, "icon": f"ic_{code}", "sort_order": order}
        )
    for order, (code, name_hi, kind, job_codes, hpa, aliases, active) in enumerate(MACHINES, start=1):
        m, created = MachineType.objects.get_or_create(
            code=code,
            defaults={
                "name_hi": name_hi, "kind": kind, "default_hours_per_acre": hpa,
                # (Model.save() isn't used in migrations, so lower-case them here.)
                "search_aliases": [a.lower() for a in aliases],
                "icon": f"ic_{code}", "sort_order": order, "is_active": active,
            },
        )
        if created:
            m.jobs.set([jobs[c] for c in job_codes])
    MachineType.objects.get_or_create(
        code="other",
        defaults={"name_hi": "अन्य", "kind": None, "icon": "ic_other", "sort_order": 99, "search_aliases": []},
    )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0001_jobs_and_machine_types")]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
