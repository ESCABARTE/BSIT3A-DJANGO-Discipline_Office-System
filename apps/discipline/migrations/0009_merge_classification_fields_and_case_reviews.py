from django.db import migrations, models


def merge_classification_fields(apps, schema_editor):
    DisciplinaryCase = apps.get_model('discipline', 'DisciplinaryCase')
    for case in DisciplinaryCase.objects.all().iterator():
        parts = []
        for value in (
            case.classification_factual_basis,
            case.classification_basis,
            case.classification_remarks,
        ):
            value = (value or '').strip()
            if value and value not in parts:
                parts.append(value)
        combined = '\n\n'.join(parts)
        if combined != case.classification_factual_basis:
            DisciplinaryCase.objects.filter(pk=case.pk).update(
                classification_factual_basis=combined,
            )


def restore_classification_fields(apps, schema_editor):
    DisciplinaryCase = apps.get_model('discipline', 'DisciplinaryCase')
    DisciplinaryCase.objects.exclude(classification_factual_basis='').update(
        classification_basis=models.F('classification_factual_basis'),
    )


class Migration(migrations.Migration):

    dependencies = [
        ('discipline', '0008_add_case_level_and_combined_classification'),
    ]

    operations = [
        migrations.RunPython(merge_classification_fields, restore_classification_fields),
        migrations.RemoveField(
            model_name='disciplinarycase',
            name='classification_basis',
        ),
        migrations.RemoveField(
            model_name='disciplinarycase',
            name='classification_remarks',
        ),
        migrations.AlterField(
            model_name='caseaction',
            name='action_type',
            field=models.CharField(
                choices=[
                    ('status', 'Workflow status'),
                    ('finding', 'Finding'),
                    ('recommendation', 'Recommendation'),
                    ('decision', 'Final decision'),
                    ('sanction', 'Approved sanction'),
                    ('appeal', 'Appeal / reconsideration'),
                    ('review', 'Case review'),
                    ('note', 'Case note'),
                    ('referral', 'Referral'),
                ],
                max_length=20,
            ),
        ),
    ]
