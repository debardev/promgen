# Copyright (c) 2017 LINE Corporation
# These sources are released under the terms of the MIT license: see LICENSE

import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate
from promgen import util, settings

logger = logging.getLogger(__name__)


def create_group(apps):
    if not settings.PROMGEN_DEFAULT_GROUP:
        return

    # Create Default Group
    group, created = apps.get_model('auth', 'Group').objects.get_or_create(
        name=settings.PROMGEN_DEFAULT_GROUP
    )

    # Create default permissions. We skip the permissions that are
    # generally for admin rules (Shards, Prometheus, Audit) and skip
    # custom permissions for label/annotations but list everything else
    Permission = apps.get_model('auth', 'Permission')
    group.permissions.set(
        Permission.objects.filter(
            content_type__app_label='promgen',
            content_type__model__in=[
                'exporter',
                'farm',
                'host',
                'project',
                'rule',
                'sender',
                'service',
                'url',
            ],
        )
    )

    # Add users to default group
    User = apps.get_model('auth', 'User')
    for user in User.objects.all():
        user.groups.add(group)


def default_admin(sender, apps, interactive, **kwargs):
    # Have to import here to ensure that the apps are already registered and
    # we get a real model instead of __fake__.User
    from django.contrib.auth.models import User
    create_group(apps)
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
        prometheus_model = apps.get_model('promgen.Queue')
        if interactive:
            print('  Adding default queues')
        prometheus_model.objects.create(
            shard=new_shard,
            name=util.setting("default.shard:queue", 'promgen')
        )


def default_site(sender, apps, interactive, **kwargs):
    Site = apps.get_model('sites.Site')
    site, created = Site.objects.get_or_create(id=settings.SITE_ID)
    site.domain = util.setting("site.domain", 'promgen.service.com')
    site.name = 'promgen'
    site.save()


class PromgenConfig(AppConfig):
    name = "promgen"

    def ready(self):
        from promgen import signals, checks  # NOQA

        post_migrate.connect(default_shard, sender=self)
        post_migrate.connect(default_admin, sender=self)
        post_migrate.connect(default_site, sender=self)
