# -*- coding: utf-8 -*-
# Generated by Django 1.10.7 on 2017-06-30 05:24
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('promgen', '0036_auto_20170626_0231'),
    ]

    operations = [
        migrations.AddField(
            model_name='shard',
            name='proxy',
            field=models.BooleanField(default=False),
        ),
    ]
