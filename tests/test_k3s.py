"""Tests for the k3s deployment strategy's pure helpers."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import django.conf

from djaploy.config import HostConfig
from djaploy.infra import k3s


def host(**overrides):
    kwargs = dict(
        ssh_hostname="203.0.113.10",
        ssh_user="janitor",
        app_name="docms",
        app_hostname="rancor.techco.fi",
        deployment_strategy="k3s",
        manage_py_path="docms/manage.py",
        gunicorn_conf={"workers": 3, "timeout": 120, "wsgi_module": "docms.wsgi:application"},
        nginx_conf={"client_max_body_size": "512M"},
        secret_key="s3cret",
        data={"mistral_api_key": "m-key", "nested": {"skip": True}, "flag": True},
        k3s_conf={
            "settings_module": "docms.settings.production",
            "static_root": "/app/docms/public/static",
            "extra_commands": ["seed_realty --org-slug bo"],
            "storage_size": "20Gi",
        },
    )
    kwargs.update(overrides)
    return HostConfig("rancor", **kwargs)[1]


class HostConfigTests(unittest.TestCase):
    def test_k3s_is_a_valid_strategy(self):
        self.assertEqual(host()["deployment_strategy"], "k3s")


class BuildValuesTests(unittest.TestCase):
    def test_maps_host_config_onto_chart_values(self):
        values = k3s.build_values(host(), "djaploy/docms:abc1234")

        self.assertEqual(values["image"], {"repository": "djaploy/docms", "tag": "abc1234", "pullPolicy": "Never"})
        self.assertEqual(values["hostnames"], ["rancor.techco.fi"])
        self.assertEqual(values["django"]["managePy"], "docms/manage.py")
        self.assertEqual(values["django"]["wsgiModule"], "docms.wsgi:application")
        self.assertEqual(values["django"]["settingsModule"], "docms.settings.production")
        self.assertEqual(values["gunicorn"], {"workers": 3, "timeout": 120})
        self.assertEqual(values["staticRoot"], "/app/docms/public/static")
        self.assertEqual(values["storage"], {"size": "20Gi"})
        self.assertEqual(values["migrations"], {"extraCommands": ["seed_realty --org-slug bo"]})
        self.assertEqual(values["nginx"], {"clientMaxBodySize": "512M"})
        self.assertEqual(values["envFromSecret"], "docms-env")
        self.assertEqual(values["onePassword"], {"env": {}, "pullSecretItem": ""})
        self.assertFalse(values["suspended"])

    def test_namespace_overrides_app_name(self):
        values = k3s.build_values(host(k3s_conf={"namespace": "docms-dev"}), "djaploy/docms:t")
        self.assertEqual(values["envFromSecret"], "docms-dev-env")

    def test_extra_hostnames_follow_app_hostname(self):
        values = k3s.build_values(host(k3s_conf={"hostnames": ["groundhog.techco.fi"]}), "djaploy/docms:t")
        self.assertEqual(values["hostnames"], ["rancor.techco.fi", "groundhog.techco.fi"])

    def test_raw_values_merge_last(self):
        values = k3s.build_values(
            host(k3s_conf={"storage_size": "5Gi", "values": {"storage": {"storageClassName": "local-path"}, "replicas": 2}}),
            "djaploy/docms:t",
        )
        self.assertEqual(values["storage"], {"size": "5Gi", "storageClassName": "local-path"})
        self.assertEqual(values["replicas"], 2)

    def test_wsgi_module_falls_back_to_django_setting(self):
        with patch.object(django.conf, "settings", new=SimpleNamespace(WSGI_APPLICATION="docms.wsgi.application")):
            values = k3s.build_values(host(gunicorn_conf={"workers": 2}), "djaploy/docms:t")
        self.assertEqual(values["django"]["wsgiModule"], "docms.wsgi:application")


class BuildEnvSecretTests(unittest.TestCase):
    def test_secret_key_and_scalar_data_become_env(self):
        secret = k3s.build_env_secret(host())

        self.assertEqual(secret["metadata"], {"name": "docms-env", "namespace": "docms"})
        self.assertEqual(secret["stringData"], {"SECRET_KEY": "s3cret", "MISTRAL_API_KEY": "m-key"})

    def test_no_secrets_gives_an_empty_secret(self):
        secret = k3s.build_env_secret(host(secret_key=None, data=None))
        self.assertEqual(secret["stringData"], {})


class ImageRefTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(django.conf, "settings", new=SimpleNamespace(GIT_DIR="/src/Document-Management-System"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_to_the_git_dir_name(self):
        self.assertEqual(k3s.image_ref_for({}, "abc1234", "latest"), "djaploy/document-management-system:abc1234")

    def test_release_mode_uses_the_tag(self):
        self.assertEqual(k3s.image_ref_for({"image": "djaploy/docms"}, "abc", "release", "v1.2.0"), "djaploy/docms:v1.2.0")

    def test_local_mode_is_unique(self):
        self.assertRegex(k3s.image_ref_for({"image": "djaploy/docms"}, "abc", "local"), r"^djaploy/docms:abc-local-\d+$")


class ChartTests(unittest.TestCase):
    def test_bundled_chart_version(self):
        self.assertEqual(k3s.chart_version(), "0.2.2")


class BuildModeTests(unittest.TestCase):
    def test_defaults_to_building_on_the_node(self):
        self.assertEqual(k3s.build_mode({}), "server")

    def test_local_builds_can_be_chosen(self):
        self.assertEqual(k3s.build_mode({"build": "local"}), "local")

    def test_unknown_mode_is_rejected(self):
        with self.assertRaises(ValueError):
            k3s.build_mode({"build": "cloud"})


class ContainerdRefTests(unittest.TestCase):
    def test_docker_hub_names_get_the_registry_prefix(self):
        self.assertEqual(k3s.containerd_ref("djaploy/docms:abc"), "docker.io/djaploy/docms:abc")

    def test_registry_names_are_kept(self):
        self.assertEqual(k3s.containerd_ref("ghcr.io/org/app:abc"), "ghcr.io/org/app:abc")
