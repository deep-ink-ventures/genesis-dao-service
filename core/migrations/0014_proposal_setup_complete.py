# Generated by Django 4.1.7 on 2023-07-19 14:03

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_multisignature"),
    ]

    operations = [
        migrations.AddField(
            model_name="proposal",
            name="setup_complete",
            field=models.BooleanField(default=False),
        ),
    ]