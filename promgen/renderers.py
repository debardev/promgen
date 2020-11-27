# Copyright (c) 2019 LINE Corporation
# These sources are released under the terms of the MIT license: see LICENSE

import yaml
from rest_framework import renderers
from promgen import models


# https://www.django-rest-framework.org/api-guide/renderers/#custom-renderers
class RuleRenderer(renderers.BaseRenderer):
    format = "yaml"
    media_type = "application/x-yaml"
    charset = "utf-8"

    def render(self, data, media_type=None, renderer_context=None):
        return yaml.safe_dump(
            {"groups": [{"name": name,
                         "rules": [rule for rule in data[name] if models.Rule.objects.filter(name=rule['alert'],
                                                                                             enabled=True)]}
                        for name in data]},
            default_flow_style=False,
            allow_unicode=True,
            encoding=self.charset,
        )
