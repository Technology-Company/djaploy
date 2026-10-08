"""Tests for the gitops deployment strategy."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from djaploy.config import HostConfig
from djaploy.infra import gitops

IMAGE = "registry.techco.fi:5443/docms-prod/docms"
OLD = "sha256:" + "a" * 64
NEW = "sha256:" + "b" * 64


def host(**overrides):
    kwargs = dict(
        ssh_hostname="203.0.113.10",
        ssh_user="janitor",
        ssh_key="/tmp/key",
        app_name="docms",
        deployment_strategy="gitops",
        manage_py_path="docms/manage.py",
        secret_key="s3cret",
        data={"mistral_api_key": "m-key", "nested": {"skip": True}, "flag": True},
        gitops_conf={
            "namespace": "docms-prod",
            "image": IMAGE,
            "manifest": "kubernetes/apps/docms-prod/kustomization.yaml",
            "infra_repo": "/tmp/infra",
            "settings_module": "docms.settings.production",
        },
    )
    kwargs.update(overrides)
    return HostConfig("extor", **kwargs)[1]


KUSTOMIZATION = f"""# Document Management production.
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization
namespace: docms-prod
resources:
- ../docms-base
images:
- name: docms
  newName: {IMAGE}
  digest: {OLD}  # djaploy/docms:8c8ea61 from Rancor
patches:
- path: environment.yaml
"""


class ConfigTests(unittest.TestCase):
    def test_gitops_is_a_valid_strategy(self):
        self.assertEqual(host()["deployment_strategy"], "gitops")

    def test_required_keys(self):
        with self.assertRaisesRegex(ValueError, "image, manifest"):
            gitops.conf_of(host(gitops_conf={}))

    def test_names_default_from_app_name(self):
        h = host(gitops_conf={"image": IMAGE, "manifest": "m.yaml"})
        self.assertEqual(gitops.namespace(h), "docms")
        self.assertEqual(gitops.argocd_app(h), "docms")
        self.assertEqual(gitops.env_secret_name(h), "docms-env")

    def test_namespace_and_overrides(self):
        h = host(gitops_conf={"image": IMAGE, "manifest": "m.yaml", "namespace": "docms-dev",
                              "argocd_app": "dev-app", "env_secret": "docms-env"})
        self.assertEqual(gitops.namespace(h), "docms-dev")
        self.assertEqual(gitops.argocd_app(h), "dev-app")
        self.assertEqual(gitops.env_secret_name(h), "docms-env")

    def test_env_secret_can_be_disabled(self):
        h = host(gitops_conf={"image": IMAGE, "manifest": "m.yaml", "env_secret": None})
        self.assertIsNone(gitops.env_secret_name(h))

    def test_infra_repo_from_environment(self):
        h = host(gitops_conf={"image": IMAGE, "manifest": "m.yaml"})
        with patch.dict("os.environ", {"DJAPLOY_INFRA_REPO": "~/infra"}):
            self.assertEqual(gitops.infra_repo(h), Path("~/infra").expanduser())
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                gitops.infra_repo(h)

    def test_kubeconfig(self):
        self.assertEqual(gitops.kubectl(host()), ["kubectl"])
        h = host(gitops_conf={"image": IMAGE, "manifest": "m.yaml", "kubeconfig": "/k/config"})
        self.assertEqual(gitops.kubectl(h), ["kubectl", "--kubeconfig", "/k/config"])


class ImageTagTests(unittest.TestCase):
    def test_commit_tag(self):
        self.assertEqual(gitops.image_tag("8c8ea61", "latest"), "8c8ea61")

    def test_local_tag(self):
        self.assertRegex(gitops.image_tag("8c8ea61", "local"), r"^8c8ea61-local-\d+$")

    def test_rejects_unsafe_commit(self):
        for bad in ("", "unknown", "8c8ea61; rm -rf /", "../x"):
            with self.assertRaises(ValueError):
                gitops.image_tag(bad, "latest")


class EnvSecretTests(unittest.TestCase):
    def test_secret_key_and_scalar_data(self):
        secret = gitops.build_env_secret(host())
        self.assertEqual(secret["metadata"], {"name": "docms-env", "namespace": "docms-prod"})
        self.assertEqual(secret["stringData"], {"SECRET_KEY": "s3cret", "MISTRAL_API_KEY": "m-key"})


class PinDigestTests(unittest.TestCase):
    def test_kustomize_digest_replaced_with_note(self):
        out = gitops.pin_digest(KUSTOMIZATION, IMAGE, NEW, note="abc1234")
        self.assertIn(f"  digest: {NEW}  # abc1234\n", out)
        self.assertNotIn(OLD, out)
        self.assertEqual(out.replace(f"{NEW}  # abc1234", f"{OLD}  # djaploy/docms:8c8ea61 from Rancor"), KUSTOMIZATION)

    def test_kustomize_new_tag_becomes_digest(self):
        text = KUSTOMIZATION.replace(f"digest: {OLD}  # djaploy/docms:8c8ea61 from Rancor", "newTag: 8c8ea61")
        out = gitops.pin_digest(text, IMAGE, NEW)
        self.assertIn(f"  digest: {NEW}\n", out)
        self.assertNotIn("newTag", out)

    def test_kustomize_pin_inserted_when_missing(self):
        text = KUSTOMIZATION.replace(f"  digest: {OLD}  # djaploy/docms:8c8ea61 from Rancor\n", "")
        out = gitops.pin_digest(text, IMAGE, NEW)
        self.assertIn(f"  newName: {IMAGE}\n  digest: {NEW}\n", out)
        self.assertIn("patches:\n- path: environment.yaml", out)

    def test_other_images_untouched(self):
        text = KUSTOMIZATION + f"- name: other\n  newName: registry.example/other\n  digest: {OLD}\n"
        out = gitops.pin_digest(text, IMAGE, NEW)
        self.assertIn(f"newName: registry.example/other\n  digest: {OLD}", out)

    def test_inline_references(self):
        image = "registry.techco.fi:5443/contentsystem/contentsystem"
        text = (f"        image: {image}@{OLD}\n"
                f"            image: {image}:53683b7\n"
                f"        image: {image}-worker@{OLD}\n")
        out = gitops.pin_digest(text, image, NEW)
        self.assertEqual(out.count(f"{image}@{NEW}"), 2)
        self.assertIn(f"{image}-worker@{OLD}", out)

    def test_missing_pin_raises(self):
        with self.assertRaisesRegex(ValueError, "No pin"):
            gitops.pin_digest("kind: Kustomization\n", IMAGE, NEW)

    def test_rejects_non_digest(self):
        with self.assertRaises(ValueError):
            gitops.pin_digest(KUSTOMIZATION, IMAGE, "latest")


class RealManifestTests(unittest.TestCase):
    """The shapes used in hetzner-management/kubernetes/apps."""

    INFRA = Path(__file__).resolve().parents[2] / "hetzner-management" / "kubernetes" / "apps"

    def test_real_manifests(self):
        cases = [("docms-prod/kustomization.yaml", IMAGE),
                 ("docms-dev/kustomization.yaml", "registry.techco.fi:5443/docms-dev/docms"),
                 ("contentsystem/application.yaml", "registry.techco.fi:5443/contentsystem/contentsystem")]
        for manifest, image in cases:
            path = self.INFRA / manifest
            if not path.exists():
                self.skipTest("hetzner-management checkout not next to djaploy")
            with self.subTest(manifest=manifest):
                out = gitops.pin_digest(path.read_text(), image, NEW, note="t")
                self.assertIn(NEW, out)
                self.assertEqual(len(out.splitlines()), len(path.read_text().splitlines()))


class SyncOperationTests(unittest.TestCase):
    def test_operation_pins_revision(self):
        op = gitops.sync_operation("f" * 40)["operation"]
        self.assertEqual(op["sync"]["revision"], "f" * 40)
        self.assertIn("ServerSideApply=true", op["sync"]["syncOptions"])


class BuildNodeTests(unittest.TestCase):
    def test_ssh_options_from_inventory(self):
        node = gitops.BuildNode(host(ssh_port=2222, ssh_known_hosts_file="/tmp/kh"))
        self.assertEqual(node.target, "janitor@203.0.113.10")
        self.assertIn("StrictHostKeyChecking=yes", node.opts)
        self.assertIn("UserKnownHostsFile=/tmp/kh", node.opts)
        self.assertEqual(node.opts[node.opts.index("-p") + 1], "2222")
        self.assertEqual(node.opts[node.opts.index("-i") + 1], "/tmp/key")


def git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


class PublishPinTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "--quiet", "--bare", "-b", "main", str(self.origin)], check=True)
        self.repo = root / "infra"
        subprocess.run(["git", "clone", "--quiet", str(self.origin), str(self.repo)], check=True,
                       capture_output=True)
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")
        manifest = self.repo / "kubernetes/apps/docms-prod/kustomization.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(KUSTOMIZATION)
        git(self.repo, "add", ".")
        git(self.repo, "commit", "--quiet", "-m", "init")
        git(self.repo, "push", "--quiet", "origin", "HEAD:main")
        self.host = host(gitops_conf={**host()["gitops_conf"], "infra_repo": str(self.repo)})

    def test_commits_and_pushes_pin(self):
        branch = gitops.check_infra_repo(self.repo, self.host["gitops_conf"]["manifest"])
        revision = gitops.publish_pin(self.host, NEW, "abc1234", branch)
        self.assertEqual(git(self.origin, "rev-parse", "main"), revision)
        self.assertIn("Deploy docms abc1234 to docms-prod", git(self.origin, "log", "-1", "--format=%B", "main"))
        self.assertIn(NEW, git(self.origin, "show", "main:kubernetes/apps/docms-prod/kustomization.yaml"))

    def test_same_digest_makes_no_commit(self):
        branch = gitops.check_infra_repo(self.repo, self.host["gitops_conf"]["manifest"])
        first = gitops.publish_pin(self.host, NEW, "abc1234", branch)
        second = gitops.publish_pin(self.host, NEW, "abc1234", branch)
        self.assertEqual(first, second)

    def test_dirty_manifest_refused(self):
        (self.repo / self.host["gitops_conf"]["manifest"]).write_text(KUSTOMIZATION + "# edit\n")
        with self.assertRaisesRegex(RuntimeError, "uncommitted"):
            gitops.check_infra_repo(self.repo, self.host["gitops_conf"]["manifest"])


class HookTests(unittest.TestCase):
    def context(self, hosts):
        return {"_hosts": hosts, "artifact_path": "/tmp/a.tar.gz", "mode": "latest",
                "pyinfra_data": {"commit": "abc1234"}}

    def test_runs_steps_in_order(self):
        calls = []
        with patch.object(gitops, "check_infra_repo", side_effect=lambda *a: calls.append("check") or "main"), \
             patch.object(gitops, "build_and_push", side_effect=lambda *a: calls.append("build") or NEW), \
             patch.object(gitops, "apply_env_secret", side_effect=lambda *a: calls.append("secret")), \
             patch.object(gitops, "publish_pin", side_effect=lambda *a: calls.append("pin") or "f" * 40), \
             patch.object(gitops, "sync_and_wait", side_effect=lambda *a: calls.append("sync")):
            context = self.context([("extor", host())])
            gitops._gitops_deploy(context)
        self.assertEqual(calls, ["check", "build", "secret", "pin", "sync"])
        self.assertEqual(context["gitops"], {"image": f"{IMAGE}@{NEW}", "revision": "f" * 40})

    def test_build_failure_publishes_nothing(self):
        with patch.object(gitops, "check_infra_repo", return_value="main"), \
             patch.object(gitops, "build_and_push", side_effect=RuntimeError("build failed")), \
             patch.object(gitops, "publish_pin") as publish, patch.object(gitops, "sync_and_wait") as sync:
            with self.assertRaises(RuntimeError):
                gitops._gitops_deploy(self.context([("extor", host())]))
        publish.assert_not_called()
        sync.assert_not_called()

    def test_ignores_other_strategies(self):
        with patch.object(gitops, "check_infra_repo") as check:
            gitops._gitops_deploy(self.context([("web", host(deployment_strategy="zero_downtime"))]))
        check.assert_not_called()

    def test_one_host_only(self):
        with self.assertRaisesRegex(ValueError, "one host"):
            gitops._gitops_deploy(self.context([("a", host()), ("b", host())]))


if __name__ == "__main__":
    unittest.main()
