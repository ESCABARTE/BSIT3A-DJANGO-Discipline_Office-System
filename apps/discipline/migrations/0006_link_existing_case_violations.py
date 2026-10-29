from django.db import migrations


def link_existing_case_violations(apps, schema_editor):
    DisciplinaryCase = apps.get_model('discipline', 'DisciplinaryCase')
    for case in DisciplinaryCase.objects.filter(violation__isnull=False).select_related('violation').iterator():
        case.violations.add(case.violation)


class Migration(migrations.Migration):

    dependencies = [
        ('discipline', '0005_integrated_case_intake'),
    ]

    operations = [
        migrations.RunPython(link_existing_case_violations, migrations.RunPython.noop),
    ]
