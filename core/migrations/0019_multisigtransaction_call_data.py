# Generated by Django 4.1.7 on 2023-08-29 12:49

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_denormalizations"),
    ]

    operations = [
        migrations.AddField(
            model_name="multisigtransaction",
            name="call_data",
            field=models.CharField(default="empty", max_length=1024),
            preserve_default=False,
        ),
    ]
