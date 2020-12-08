# Copyright (c) 2017 LINE Corporation
# These sources are released under the terms of the MIT license: see LICENSE

from __future__ import absolute_import, unicode_literals

import logging
import socket

import celery
import yaml

from celery.signals import celeryd_after_setup
from django.db import OperationalError

from kubernetes import client, config, watch

logger = logging.getLogger(__name__)


app = celery.Celery("promgen")

# Using a string here means the worker don't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django app configs.
app.autodiscover_tasks()


@app.task(bind=True)
def debug_task(self):
    print("Request: {0!r}".format(self.request))


@app.task(bind=True)
def rules_watch_task(self):
    from promgen import util, models
    from promgen.prometheus import import_rules_v2

    k8s_config_type = util.setting("kubernetes:config.type") or "kube"

    if k8s_config_type == "incluster":
        config.load_incluster_config()
    else:
        config.load_kube_config()

    w = watch.Watch()
    api_instance = client.CustomObjectsApi(client.ApiClient())
    namespace = util.setting("kubernetes:namespace") or "default"
    for event in w.stream(api_instance.list_namespaced_custom_object, group="monitoring.coreos.com", version="v1",
                          plural="prometheusrules", namespace=namespace):
        if event['type'] == 'ADDED':
            spec = event['object']['spec']
            for group in spec['groups']:
                try:
                    service, created = models.Service.objects.get_or_create(
                        name=group['name']
                    )
                    if created:
                        logger.debug('Created service %s', service)

                    counters = import_rules_v2({'groups': [group]}, service)
                except OperationalError as op_err:
                    print("Import failed because of operational db error: %s" % op_err)
                else:
                    print("Imported: %s" % counters)
            try:
                assert event['object']['metadata']['labels']['app.kubernetes.io/managed-by'] == "promgen"
            except (KeyError, AssertionError):
                api_instance.delete_namespaced_custom_object(name=event['object']['metadata']['name'],
                                                             group="monitoring.coreos.com", version="v1",
                                                             plural="prometheusrules", namespace=namespace)


@app.task(bind=True)
def loki_rules_watch_task(self):
    from promgen import util, models
    from promgen.prometheus import import_rules_v2

    k8s_config_type = util.setting("kubernetes:config.type") or "kube"

    if k8s_config_type == "incluster":
        config.load_incluster_config()
    else:
        config.load_kube_config()

    w = watch.Watch()
    api_instance = client.CoreV1Api(client.ApiClient())
    namespace = util.setting("kubernetes:namespace") or "default"
    for event in w.stream(api_instance.list_namespaced_config_map, namespace=namespace, label_selector="loki_rule=1",
                          watch=True):
        if event['type'] == 'ADDED':
            obj = event['object']
            for filename, data in obj.data.items():
                configs = yaml.load(data, Loader=yaml.FullLoader)
                for group in configs['groups']:
                    try:
                        service, created = models.Service.objects.get_or_create(
                            name=group['name']
                        )
                        if created:
                            logger.debug('Created service %s', service)

                        counters = import_rules_v2({'groups': [group]}, service, of_type='loki')
                    except OperationalError as op_err:
                        print("Import failed because of operational db error: %s" % op_err)
                    else:
                        print("Imported: %s" % counters)
                try:
                    assert obj.metadata.labels['app.kubernetes.io/managed-by'] == "promgen"
                except (KeyError, AssertionError):
                    api_instance.delete_namespaced_config_map(name=obj.metadata.name, namespace=namespace)


@celeryd_after_setup.connect
def setup_direct_queue(sender, instance, **kwargs):
    # To enable triggering config writes and reloads on a specific Prometheus server
    # we automatically create a queue for the current server that we can target from
    # our promgen.prometheus functions
    instance.app.amqp.queues.select_add(socket.gethostname())
    debug_task.apply_async(queue=socket.gethostname())
    rules_watch_task.apply_async(queue=socket.gethostname())
    loki_rules_watch_task.apply_async(queue=socket.gethostname())
