from django.db import migrations, models


def fill_prefix(apps, schema_editor):
    Office = apps.get_model("organization", "Office")
    for office in Office.objects.all():
        office.doc_prefix = office.code
        office.save(update_fields=["doc_prefix"])


class Migration(migrations.Migration):
    dependencies = [("organization", "0001_initial")]
    operations = [
        migrations.AddField("office", "doc_prefix", models.CharField(max_length=10, null=True, editable=False)),
        migrations.RunPython(fill_prefix, migrations.RunPython.noop),
        migrations.AlterField("office", "doc_prefix", models.CharField(max_length=10, unique=True, editable=False)),
    ]
