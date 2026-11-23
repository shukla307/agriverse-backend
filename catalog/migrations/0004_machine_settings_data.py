"""
Each machine type gets its own settings, so the wizard asks only what makes sense (owner, 2026-11-23):
fuel question, operator line, transport question, attachments, brand + HP, acre prices.
Also: थ्रेशर (name unchanged) becomes a power machine: many have their own engine/motor; it can still be
added to a tractor listing. 4 new machines and some extra aliases. Names and aliases: docs/hindi-glossary.md §7
(approved 2026-11-23). Default hours per acre of the new machines are to be checked by the owner.
"""

from django.db import migrations

D, DP, B, N, ASK = "diesel", "diesel_or_petrol", "battery", "none", "ask"

# code: (sort, kind, fuel, operator, asks_transport, attachable, takes_attachments, has_brand, acre_price)
SETTINGS = {
    "tractor":            (1, "power", D, "always", True, False, True, True, True),
    "mini_tractor":       (2, "power", D, "always", True, False, True, True, True),
    "combine_harvester":  (3, "power", D, "always", True, False, False, True, True),
    "reaper_binder":      (4, "power", DP, "always", True, False, False, False, True),
    "power_tiller":       (5, "power", DP, "always", True, False, False, True, True),
    "paddy_transplanter": (6, "power", DP, "always", True, False, False, False, True),
    "drone_sprayer":      (7, "power", B, "always", True, False, False, False, True),
    "thresher":           (8, "power", DP, "always", True, True, False, False, True),
    "jcb":                (9, "power", D, "always", True, False, False, False, False),
    "rotavator":          (10, "implement", N, "never", False, True, False, False, True),
    "cultivator":         (11, "implement", N, "never", False, True, False, False, True),
    "plough_mb":          (12, "implement", N, "never", False, True, False, False, True),
    "plough_disc":        (13, "implement", N, "never", False, True, False, False, True),
    "harrow":             (14, "implement", N, "never", False, True, False, False, True),
    "laser_leveller":     (15, "implement", N, "never", False, True, False, False, True),
    "seed_drill":         (16, "implement", N, "never", False, True, False, False, True),
    "super_seeder":       (17, "implement", N, "never", False, True, False, False, True),
    "happy_seeder":       (18, "implement", N, "never", False, True, False, False, True),
    "potato_planter":     (19, "implement", N, "never", False, True, False, False, True),
    "sugarcane_planter":  (20, "implement", N, "never", False, True, False, False, True),
    "boom_sprayer":       (21, "implement", N, "never", False, True, False, False, True),
    "reaper":             (22, "implement", N, "never", False, True, False, False, True),
    "straw_reaper":       (23, "implement", N, "never", False, True, False, False, True),
    "potato_digger":      (24, "implement", N, "never", False, True, False, False, True),
    "maize_sheller":      (25, "implement", N, "never", False, True, False, False, False),
    "baler":              (26, "implement", N, "never", False, True, False, False, True),
    "mulcher":            (27, "implement", N, "never", False, True, False, False, True),
    "trolley":            (28, "implement", N, "never", False, True, False, False, False),
    # अन्य: we don't know the machine, so the owner answers (decision C1).
    "other":              (99, None, ASK, "ask", True, False, False, False, True),
}

# code: (name_hi, jobs, default hours per acre, aliases)
NEW = {
    "reaper_binder": ("रीपर बाइंडर", ["harvesting"], "1.0", ["reaper binder", "binder", "ripar bindar", "बाइंडर"]),
    "sugarcane_planter": ("गन्ना बोने की मशीन", ["sowing"], "2.5",
                          ["sugarcane planter", "cutter planter", "trench planter", "ganna planter", "गन्ना प्लांटर"]),
    "straw_reaper": ("भूसा मशीन (स्ट्रॉ रीपर)", ["harvesting", "threshing"], "1.0",
                     ["straw reaper", "bhusa machine", "bhusa", "bhoosa", "भूसा", "स्ट्रॉ रीपर", "स्ट्रा रीपर"]),
    "maize_sheller": ("मक्का शेलर", ["threshing"], None,
                      ["maize sheller", "makka sheller", "makka thresher", "मक्का थ्रेशर", "मक्का दाना मशीन"]),
}

MORE_ALIASES = {
    "combine_harvester": ["mini combine", "छोटा कंबाइन"],
    "seed_drill": ["zero drill", "zero till", "जीरो ड्रिल"],
    "mulcher": ["straw chopper", "पराली"],
}


def forward(apps, schema_editor):
    JobCategory = apps.get_model("catalog", "JobCategory")
    MachineType = apps.get_model("catalog", "MachineType")
    jobs = {j.code: j for j in JobCategory.objects.all()}
    for code, (name_hi, job_codes, hpa, aliases) in NEW.items():
        m, created = MachineType.objects.get_or_create(
            code=code,
            defaults={"name_hi": name_hi, "default_hours_per_acre": hpa, "icon": f"ic_{code}",
                      # (Model.save() isn't used in migrations, so lower-case them here.)
                      "search_aliases": [a.lower() for a in aliases]},
        )
        if created:
            m.jobs.set([jobs[c] for c in job_codes if c in jobs])
    for code, extra in MORE_ALIASES.items():
        m = MachineType.objects.filter(code=code).first()
        if m:
            have = list(m.search_aliases)
            m.search_aliases = have + [a.lower() for a in extra if a.lower() not in have]
            m.save(update_fields=["search_aliases"])
    for code, (sort, kind, fuel, operator, transport, attachable, takes, brand, acre) in SETTINGS.items():
        MachineType.objects.filter(code=code).update(
            sort_order=sort, kind=kind, fuel=fuel, operator=operator, asks_transport=transport,
            attachable=attachable, takes_attachments=takes, has_brand=brand, acre_price=acre,
        )


class Migration(migrations.Migration):
    dependencies = [("catalog", "0003_machine_settings")]
    operations = [migrations.RunPython(forward, migrations.RunPython.noop)]
