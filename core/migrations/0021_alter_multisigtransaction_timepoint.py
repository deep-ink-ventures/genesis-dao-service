# Generated by Django 4.1.7 on 2023-08-31 16:32

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_multisigtransaction_timepoint"),
    ]

    operations = [
        migrations.AlterField(
            model_name="multisigtransaction",
            name="timepoint",
            field=models.JSONField(default=dict),
        ),
    ]
