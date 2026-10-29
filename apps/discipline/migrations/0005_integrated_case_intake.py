import uuid

import apps.discipline.storage
from django.db import migrations, models


def populate_submission_tokens(apps, schema_editor):
    CaseReport = apps.get_model('discipline', 'CaseReport')
    for report in CaseReport.objects.filter(submission_token__isnull=True).iterator():
        report.submission_token = uuid.uuid4()
        report.save(update_fields=['submission_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('discipline', '0004_violation_applicable_rule_violation_classification_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='disciplinarycase',
            name='violations',
            field=models.ManyToManyField(blank=True, related_name='linked_cases', to='discipline.violation'),
        ),
        migrations.AddField(
            model_name='casereport',
            name='submission_token',
            field=models.UUIDField(editable=False, null=True),
        ),
        migrations.RunPython(populate_submission_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='casereport',
            name='submission_token',
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name='casedocument',
            name='file',
            field=models.FileField(
                storage=apps.discipline.storage.PrivateEvidenceStorage(),
                upload_to='discipline/restricted/',
            ),
        ),
    ]
