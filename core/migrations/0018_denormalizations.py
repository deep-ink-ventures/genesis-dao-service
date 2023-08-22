# Generated by Django 4.1.7 on 2023-08-22 13:27

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_proposal_and_transaction_updates"),
    ]

    operations = [
        migrations.AddField(
            model_name="multisigtransaction",
            name="call_function",
            field=models.CharField(max_length=256, null=True),
        ),
        migrations.AddField(
            model_name="proposal",
            name="title",
            field=models.CharField(max_length=128, null=True),
        ),
    ]
