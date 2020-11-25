# Copyright (c) 2017 LINE Corporation
# These sources are released under the terms of the MIT license: see LICENSE
import collections
import logging
import os
from urllib.parse import urljoin

import yaml
from atomicwrites import atomic_write
from celery import shared_task
from kubernetes import client, config
from kubernetes.client.rest import ApiException

from promgen import models, prometheus, util, notification

logger = logging.getLogger(__name__)

@shared_task
def index_alert(alert_pk):
    alert = models.Alert.objects.get(pk=alert_pk)
    labels = alert.json.get("commonLabels")
    for name, value in labels.items():
        models.AlertLabel.objects.create(alert=alert, name=name, value=value)

@shared_task
def process_alert(alert_pk):
    """
    Process alert for routing and notifications

    We load our Alert from the database and expand it to determine which labels are routable

    Next we loop through all senders configured and de-duplicate sender:target pairs before
    queing the notification to actually be sent
    """
    alert = models.Alert.objects.get(pk=alert_pk)
    routable, data = alert.expand()

    # For any blacklisted label patterns, we delete them from the queue
    # and consider it done (do not send any notification)
    blacklist = util.setting("alertmanager:blacklist", {})
    for key in blacklist:
        logger.debug("Checking key %s", key)
        if key in data["commonLabels"]:
            if data["commonLabels"][key] in blacklist[key]:
                logger.debug("Blacklisted label %s", blacklist[key])
                alert.delete()
                return

    # After processing our blacklist, it should be safe to queue our
    # alert to also index the labels
    index_alert.delay(alert.pk)

    # Now that we have our routable items, we want to check which senders are
    # configured and expand those as needed
    senders = collections.defaultdict(set)
    for label, obj in routable.items():
        logger.debug("Processing %s %s", label, obj)
        for sender in models.Sender.objects.filter(obj=obj, enabled=True):
            if sender.filtered(data):
                logger.debug("Filtered out sender %s", sender)
                continue
            if hasattr(sender.driver, "splay"):
                for splay in sender.driver.splay(sender.value):
                    senders[splay.sender].add(splay.value)
            else:
                senders[sender.sender].add(sender.value)

    for driver in senders:
        for target in senders[driver]:
            send_alert.delay(driver, target, data, alert.pk)


@shared_task
def send_alert(sender, target, data, alert_pk=None):
    """
    Send alert to specific target

    alert_pk is used when quering our alert normally and is missing
    when we send a test message. In the case we send a test message
    we want to raise any exceptions so that the test function can
    handle it
    """
    logger.debug("Sending %s %s", sender, target)
    try:
        notifier = notification.load(sender)
        notifier._send(target, data)
    except ImportError:
        logging.exception("Error loading plugin %s", sender)
        if alert_pk is None:
            raise
    except Exception as e:
        logging.exception("Error sending notification")
        if alert_pk:
            util.inc_for_pk(models.Alert, pk=alert_pk, error_count=1)
            models.AlertError.objects.create(alert_id=alert_pk, message=str(e))
        else:
            raise
    else:
        if alert_pk:
            util.inc_for_pk(models.Alert, pk=alert_pk, sent_count=1)


@shared_task
def reload_prometheus():
    from promgen import signals

    target = urljoin(util.setting("prometheus:url"), "/-/reload")
    response = util.post(target)
    signals.post_reload.send(response)


@shared_task
def write_urls(path=None, reload=True, chmod=0o644):
    if path is None:
        path = util.setting("prometheus:blackbox")
    with atomic_write(path, overwrite=True) as fp:
        # Set mode on our temporary file before we write and move it
        os.chmod(fp.name, chmod)
        fp.write(prometheus.render_urls())
    if reload:
        reload_prometheus()


@shared_task
def write_config(path=None, reload=True, chmod=0o644):
    if path is None:
        path = util.setting("prometheus:targets")
    with atomic_write(path, overwrite=True) as fp:
        # Set mode on our temporary file before we write and move it
        os.chmod(fp.name, chmod)
        fp.write(prometheus.render_config())
    if reload:
        reload_prometheus()


@shared_task
def write_rules(path=None, reload=True, chmod=0o644):
    if path is None:
        path = util.setting("prometheus:rules")
    config_type = util.setting("prometheus:config.type") or "yaml"

    if config_type == "configmap":
        rendered_rules = yaml.load(prometheus.render_rules().decode(), Loader=yaml.FullLoader)

        namespace = util.setting("prometheus:kubernetes:namespace")

        k8s_config_type = util.setting("prometheus:kubernetes:config.type") or "kube"

        if k8s_config_type == "incluster":
            config.load_incluster_config()
        else:
            config.load_kube_config()

        api_instance = client.CustomObjectsApi(client.ApiClient())

        for group in rendered_rules['groups']:

            ruleset_name = "promgen-rules-%s" % group['name'].lower()

            yaml_data = {
                "apiVersion": "monitoring.coreos.com/v1",
                "kind": "PrometheusRule",
                "metadata": {
                    "name": ruleset_name,
                    "namespace": namespace,
                    "labels": {
                        "app.kubernetes.io/managed-by": "promgen"
                    }
                },
                "spec": {'groups': [group]}
            }

            try:
                crd = api_instance.get_namespaced_custom_object(
                    group="monitoring.coreos.com",
                    version="v1",
                    plural="prometheusrules",
                    name=ruleset_name,
                    namespace=namespace,
                    async_req=False
                )

                yaml_data["metadata"]["resourceVersion"] = "%s" % crd.get("metadata")['resourceVersion']

                api_instance.replace_namespaced_custom_object(
                    group="monitoring.coreos.com",
                    version="v1",
                    plural="prometheusrules",
                    name=ruleset_name,
                    namespace=namespace,
                    body=yaml_data
                )

            except ApiException as e:
                print("Exception when calling CoreV1Api->read/replace_namespaced_custom_object: %s\n" % e)
                if e.status == 404:
                    api_instance.create_namespaced_custom_object(
                        group="monitoring.coreos.com",
                        version="v1",
                        plural="prometheusrules",
                        namespace=namespace,
                        body=yaml_data
                    )
    else:
        rendered_rules = prometheus.render_rules()
        with atomic_write(path, mode="wb", overwrite=True) as fp:
            # Set mode on our temporary file before we write and move it
            os.chmod(fp.name, chmod)
            fp.write(rendered_rules)

    if reload:
        reload_prometheus()
