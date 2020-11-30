# Copyright (c) 2017 LINE Corporation
# These sources are released under the terms of the MIT license: see LICENSE

import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate
from promgen import util

logger = logging.getLogger(__name__)


def default_admin(sender, interactive, **kwargs):
    # Have to import here to ensure that the apps are already registered and
    # we get a real model instead of __fake__.User
    from django.contrib.auth.models import User
    if User.objects.filter(is_superuser=True).count() == 0:
        if interactive:
            print('  Adding default admin user')
        User.objects.create_user(
            username=util.setting("superuser:username", 'admin'),
            password=util.setting("superuser:password", 'admin'),
            is_staff=True,
            is_active=True,
            is_superuser=True,
        )
        if interactive:
            print('BE SURE TO UPDATE THE PASSWORD!!!')


def default_shard(sender, apps, interactive, **kwargs):
    shard_model = apps.get_model('promgen.Shard')
    if shard_model.objects.count() == 0:
        if interactive:
            print('  Adding default shard')
        new_shard = shard_model.objects.create(
            name='Default',
            url=util.setting("default.shard:url", 'http://prometheus.example.com'),
            proxy=True,
            enabled=True,
        )
        prometheus_model = apps.get_model('promgen.Prometheus')
        if interactive:
            print('  Adding default prometheus')
        prometheus_model.objects.create(
            shard=new_shard,
            host=util.setting("default.shard:queue", 'promgen'),
            port=80
        )


class PromgenConfig(AppConfig):
    name = "promgen"

    def ready(self):
        from promgen import signals, checks  # NOQA

        post_migrate.connect(default_shard, sender=self)
        post_migrate.connect(default_admin, sender=self)
