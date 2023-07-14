# Generated by Django 4.1.7 on 2023-07-13 16:26

import django.contrib.postgres.fields
import django.db.models.deletion
from django.db import migrations, models

import core.models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_multisignature"),
    ]

    operations = [
        migrations.CreateModel(
            name="TransactionCallHash",
            fields=[
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "call_hash",
                    models.CharField(editable=False, max_length=250, primary_key=True, serialize=False, unique=True),
                ),
                ("call_params", models.JSONField(default=dict)),
                (
                    "multisig",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name="hashes", to="core.multisignature"
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="MultisigTransaction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("PENDING", "pending"),
                            ("APPROVED", "approved"),
                            ("CANCELLED", "cancelled"),
                            ("EXECUTED", "executed"),
                        ],
                        default=core.models.TransactionStatus["PENDING"],
                        max_length=16,
                    ),
                ),
                ("executed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "approver",
                    django.contrib.postgres.fields.ArrayField(base_field=models.CharField(max_length=250), size=None),
                ),
                ("last_approver", models.CharField(max_length=250)),
                ("cancelled_by", models.CharField(max_length=150, null=True)),
                (
                    "dao",
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="daos", to="core.dao"),
                ),
                (
                    "multisig",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transactions",
                        to="core.multisignature",
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
    ]
