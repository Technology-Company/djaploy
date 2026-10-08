"""
GitOps deployment strategy: ``deployment_strategy="gitops"``.

The app runs in a Kubernetes cluster whose manifests live in a separate GitOps
repository reconciled by Argo CD. A deploy builds the image on a build node,
pins its digest in the GitOps repository and syncs the Argo CD application:

    deploy:precommand (local, after the artifact is created)
      1. check the GitOps checkout is clean and up to date
      2. build the artifact on the build node with a temporary BuildKit daemon
         and push it with a one-hour registry token for the app's namespace
      3. apply the env Secret (SECRET_KEY + data, e.g. from OpSecret)
      4. pin the pushed digest in the GitOps repository, commit and push
      5. sync that commit in Argo CD and wait until Synced and Healthy

The inventory host is the build node: its SSH connection (ssh_hostname,
ssh_user, ssh_key, ssh_known_hosts_file) is used for the build, and no remote
pyinfra hooks run. Kubernetes access uses the local kubeconfig.

HostConfig fields used: app_name, manage_py_path, secret_key, data, and
``gitops_conf``::

    gitops_conf={
        "namespace": "docms-prod",                       # default: app_name
        "image": "registry.techco.fi:5443/docms-prod/docms",
        "infra_repo": "~/src/hetzner-management",        # or $DJAPLOY_INFRA_REPO
        "manifest": "kubernetes/apps/docms-prod/kustomization.yaml",
        "argocd_app": "docms-prod",                      # default: namespace
        "kubeconfig": "~/.kube/techco-me.kubeconfig",    # default: $KUBECONFIG
        "env_secret": "docms-env",                       # default: <app_name>-env; None: leave Secrets alone
        "settings_module": "docms.settings.production",  # Dockerfile build arg
        "builder": {                                     # build node layout (defaults shown)
            "buildkit_dir": "/opt/techco-buildkit",
            "work_dir": "/data/djaploy-build",
        },
        "registry_audience": "registry.techco.fi",
        "sync_timeout": 900,                             # seconds to wait for Argo CD
    }

The manifest pins the image either as a kustomize ``images:`` entry
(``newName: <image>`` plus ``digest:``) or as ``<image>@sha256:...``
references; djaploy rewrites the digest in place.
"""

import base64
import json
import os
import re
import shlex
import subprocess
import time
import uuid
from pathlib import Path

from djaploy.hooks import hook

GITOPS = "gitops"
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
TAG_RE = re.compile(r"[0-9a-f]{4,40}(-local-[0-9]+)?")


def _get(data, key, default=None):
    value = data.get(key, default) if isinstance(data, dict) else getattr(data, key, default)
    return default if value is None else value


# ── pure helpers ──────────────────────────────────────────────────────

def conf_of(host_data) -> dict:
    conf = dict(_get(host_data, "gitops_conf", {}))
    missing = [key for key in ("image", "manifest") if not conf.get(key)]
    if missing:
        raise ValueError(f"gitops_conf is missing {', '.join(missing)}")
    return conf


def namespace(host_data) -> str:
    return _get(conf_of(host_data), "namespace") or _get(host_data, "app_name")


def argocd_app(host_data) -> str:
    return _get(conf_of(host_data), "argocd_app") or namespace(host_data)


def env_secret_name(host_data):
    conf = conf_of(host_data)
    if "env_secret" in conf:
        return conf["env_secret"]
    return f"{_get(host_data, 'app_name')}-env"


def infra_repo(host_data) -> Path:
    path = _get(conf_of(host_data), "infra_repo") or os.environ.get("DJAPLOY_INFRA_REPO")
    if not path:
        raise ValueError('Set gitops_conf["infra_repo"] or $DJAPLOY_INFRA_REPO to the GitOps checkout')
    return Path(path).expanduser()


def kubectl(host_data) -> list:
    kubeconfig = _get(conf_of(host_data), "kubeconfig")
    return ["kubectl"] + (["--kubeconfig", str(Path(kubeconfig).expanduser())] if kubeconfig else [])


def image_tag(commit: str, mode: str) -> str:
    tag = f"{commit}-local-{int(time.time())}" if mode == "local" else commit
    if not TAG_RE.fullmatch(tag):
        raise ValueError(f"Refusing to build: unexpected commit {commit!r}")
    return tag


def build_env_secret(host_data) -> dict:
    """The app's env Secret: SECRET_KEY + every scalar in `data`, upper-cased (as for k3s)."""
    env = {}
    secret_key = _get(host_data, "secret_key")
    if secret_key:
        env["SECRET_KEY"] = str(secret_key)
    for key, value in _get(host_data, "data", {}).items():
        if isinstance(value, (dict, list, tuple, set, bool)) or value is None:
            continue
        env[str(key).upper()] = str(value)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": env_secret_name(host_data), "namespace": namespace(host_data)},
        "stringData": env,
    }


def pin_digest(text: str, image: str, digest: str, note: str = "") -> str:
    """Point every pin of *image* in a manifest at *digest*.

    Handles a kustomize ``images:`` entry (``newName: <image>`` with a sibling
    ``digest:`` or ``newTag:``) and inline ``<image>@sha256:...`` or
    ``<image>:<tag>`` references. Raises if the manifest has no pin.
    """
    if not DIGEST_RE.fullmatch(digest):
        raise ValueError(f"Not an image digest: {digest!r}")
    comment = f"  # {note}" if note else ""
    lines = text.splitlines(keepends=True)
    changed = False
    i = 0
    while i < len(lines):
        match = re.match(r"^(\s*)(- )?newName:\s*(\S+)\s*$", lines[i])
        if match and match.group(3) == image:
            indent = len(match.group(1)) + (2 if match.group(2) else 0)
            siblings = range(i + 1, len(lines))
            end = next((j for j in siblings if lines[j].strip() and
                        (len(lines[j]) - len(lines[j].lstrip()) < indent or lines[j].lstrip().startswith("- "))),
                       len(lines))
            pin = " " * indent + f"digest: {digest}{comment}\n"
            keys = [j for j in range(i + 1, end) if re.match(r"^\s*(digest|newTag):", lines[j])]
            for j in reversed(keys[1:]):
                del lines[j]
                end -= 1
            if keys:
                lines[keys[0]] = pin
            else:
                lines.insert(i + 1, pin)
            changed = True
        i += 1
    text = "".join(lines)
    inline = re.compile(re.escape(image) + r"(@sha256:[0-9a-f]{64}|:[A-Za-z0-9_][A-Za-z0-9_.-]*)(?=[\s\"']|$)")
    text, count = inline.subn(f"{image}@{digest}", text)
    if not changed and not count:
        raise ValueError(f"No pin for {image} found in the manifest")
    return text


def sync_operation(revision: str) -> dict:
    return {"operation": {
        "initiatedBy": {"username": "djaploy"},
        "sync": {"revision": revision, "syncOptions": ["ServerSideApply=true", "FailOnSharedResource=true"]},
    }}


# ── side effects ──────────────────────────────────────────────────────

def _run(cmd, **kwargs):
    kwargs.setdefault("check", True)
    return subprocess.run(cmd, **kwargs)


def _git(repo: Path, *args, capture=True) -> str:
    result = _run(["git", "-C", str(repo), *args], capture_output=capture, text=True)
    return (result.stdout or "").strip()


def check_infra_repo(repo: Path, manifest: str) -> str:
    """The GitOps checkout must have no local edits to the manifest and be up to date."""
    if not (repo / manifest).is_file():
        raise RuntimeError(f"{manifest} not found in {repo}")
    if _git(repo, "status", "--porcelain", "--", manifest):
        raise RuntimeError(f"{repo / manifest} has uncommitted changes")
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        raise RuntimeError(f"{repo} is not on a branch")
    _git(repo, "pull", "--ff-only", "--quiet")
    return branch


class BuildNode:
    """SSH to the build node with the inventory's connection settings."""

    def __init__(self, host_data):
        self.target = f"{_get(host_data, 'ssh_user')}@{_get(host_data, 'ssh_hostname')}"
        self.opts = ["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes"]
        if _get(host_data, "ssh_port"):
            self.opts += ["-p", str(_get(host_data, "ssh_port"))]
        if _get(host_data, "ssh_key"):
            self.opts += ["-i", str(_get(host_data, "ssh_key")), "-o", "IdentitiesOnly=yes"]
        if _get(host_data, "ssh_known_hosts_file"):
            self.opts += ["-o", f"UserKnownHostsFile={_get(host_data, 'ssh_known_hosts_file')}"]

    def run(self, command: str, **kwargs):
        return _run(["ssh", *self.opts, self.target, command], **kwargs)


def build_and_push(host_data, artifact_path: Path, image: str, tag: str) -> str:
    """Build on the build node and push; return the pushed manifest digest."""
    conf = conf_of(host_data)
    ns = namespace(host_data)
    builder = dict(_get(conf, "builder", {}))
    buildkit = builder.get("buildkit_dir", "/opt/techco-buildkit")
    run_id = f"{ns}-{tag}-{uuid.uuid4().hex[:8]}"
    work = f"{builder.get('work_dir', '/data/djaploy-build')}/{run_id}"
    cache = f"{builder.get('work_dir', '/data/djaploy-build')}/cache-{ns}"
    unit = f"djaploy-buildkit-{run_id}"
    sock = f"unix:///run/{unit}/buildkitd.sock"
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", run_id):
        raise ValueError(f"Unsafe build id {run_id!r}")
    node = BuildNode(host_data)

    token = _run(kubectl(host_data) + ["-n", ns, "create", "token", "registry-builder",
                                       f"--audience={_get(conf, 'registry_audience', 'registry.techco.fi')}",
                                       "--duration=1h"], capture_output=True, text=True).stdout.strip()
    registry = image.split("/", 1)[0]
    auth = base64.b64encode(f"sa.{ns}.registry-builder:{token}".encode()).decode()
    docker_config = json.dumps({"auths": {registry: {"auth": auth}}})

    args = [f"--opt build-arg:MANAGE_PY={shlex.quote(_get(host_data, 'manage_py_path', 'manage.py'))}"]
    if _get(conf, "settings_module"):
        args.append(f"--opt build-arg:SETTINGS_MODULE={shlex.quote(conf['settings_module'])}")

    print(f"[gitops] Building {image}:{tag} on {node.target}", flush=True)
    node.run(f"sudo install -d -m 700 {work}/source {work}/auth {cache} /run/{unit}")
    try:
        with open(artifact_path, "rb") as archive:
            node.run(f"sudo tar -xzf - -C {work}/source", stdin=archive)
        node.run(f"sudo tee {work}/auth/config.json >/dev/null", input=docker_config, text=True,
                 stdout=subprocess.DEVNULL)
        node.run(f"sudo systemd-run --collect --unit={unit} --property=UMask=0077 {buildkit}/buildkitd "
                 f"--addr {sock} --root {cache} --oci-worker=true --containerd-worker=false "
                 f"--oci-worker-binary {buildkit}/buildkit-runc", stdout=subprocess.DEVNULL)
        for _ in range(60):
            ready = node.run(f"sudo {buildkit}/buildctl --addr {sock} debug workers",
                             check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if ready.returncode == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("BuildKit did not become ready on the build node")
        node.run(f"sudo env DOCKER_CONFIG={work}/auth {buildkit}/buildctl --addr {sock} build --progress=plain "
                 f"--frontend dockerfile.v0 --local context={work}/source --local dockerfile={work}/source "
                 f"{' '.join(args)} --output type=image,name={image}:{tag},push=true "
                 f"--metadata-file {work}/result.json")
        result = json.loads(node.run(f"sudo cat {work}/result.json", capture_output=True, text=True).stdout)
    finally:
        node.run(f"sudo systemctl stop {unit} 2>/dev/null; sudo rm -rf {work}", check=False)
    digest = result.get("containerimage.digest", "")
    if not DIGEST_RE.fullmatch(digest):
        raise RuntimeError(f"Build finished without a pushed digest: {result}")
    print(f"[gitops] Pushed {image}@{digest}", flush=True)
    return digest


def apply_env_secret(host_data):
    if env_secret_name(host_data) is None:
        return
    secret = build_env_secret(host_data)
    _run(kubectl(host_data) + ["apply", "--server-side", "--field-manager=djaploy", "--force-conflicts", "-f", "-"],
         input=json.dumps(secret), text=True, stdout=subprocess.DEVNULL)
    print(f"[gitops] Applied Secret {secret['metadata']['namespace']}/{secret['metadata']['name']}", flush=True)


def publish_pin(host_data, digest: str, tag: str, branch: str) -> str:
    """Pin the digest in the GitOps repository, commit and push; return the commit SHA."""
    conf = conf_of(host_data)
    repo, manifest = infra_repo(host_data), conf["manifest"]
    path = repo / manifest
    text = path.read_text()
    updated = pin_digest(text, conf["image"], digest, note=tag)
    if updated != text:
        path.write_text(updated)
        _git(repo, "add", "--", manifest)
        message = f"Deploy {_get(host_data, 'app_name')} {tag} to {namespace(host_data)}\n\n{conf['image']}@{digest}\n"
        _run(["git", "-C", str(repo), "commit", "--quiet", "-F", "-"], input=message, text=True)
        for attempt in (1, 2):
            pushed = _run(["git", "-C", str(repo), "push", "--quiet", "origin", branch], check=False)
            if pushed.returncode == 0:
                break
            if attempt == 2:
                raise RuntimeError(f"git push of the {manifest} pin failed")
            _git(repo, "pull", "--rebase", "--quiet", "origin", branch)
        print(f"[gitops] Pinned {manifest} → {digest[:19]}… and pushed", flush=True)
    else:
        print(f"[gitops] {manifest} already pins {digest[:19]}…", flush=True)
    return _git(repo, "rev-parse", "HEAD")


def sync_and_wait(host_data, revision: str):
    conf = conf_of(host_data)
    app = argocd_app(host_data)
    k = kubectl(host_data)
    _run(k + ["-n", "argocd", "annotate", "application", app, "argocd.argoproj.io/refresh=normal", "--overwrite"],
         stdout=subprocess.DEVNULL)
    _run(k + ["-n", "argocd", "patch", "application", app, "--type", "merge", "-p", json.dumps(sync_operation(revision))],
         stdout=subprocess.DEVNULL)
    print(f"[gitops] Syncing Argo CD application {app} at {revision[:12]}", flush=True)
    deadline = time.time() + int(_get(conf, "sync_timeout", 900))
    status = {}
    while time.time() < deadline:
        time.sleep(5)
        status = json.loads(_run(k + ["-n", "argocd", "get", "application", app, "-o", "json"],
                                 capture_output=True, text=True).stdout).get("status", {})
        op = status.get("operationState", {})
        if op.get("syncResult", {}).get("revision") != revision:
            continue
        if op.get("phase") in ("Failed", "Error"):
            raise RuntimeError(f"Argo CD sync of {app} failed: {op.get('message', '')}")
        if (op.get("phase") == "Succeeded" and status.get("sync", {}).get("status") == "Synced"
                and status.get("health", {}).get("status") == "Healthy"):
            print(f"[gitops] {app} is Synced and Healthy", flush=True)
            return
    _run(k + ["-n", namespace(host_data), "get", "pods", "-o", "wide"], check=False)
    raise RuntimeError(f"{app} not Synced/Healthy within the timeout: sync={status.get('sync', {}).get('status')} "
                       f"health={status.get('health', {}).get('status')}")


# ── hook ──────────────────────────────────────────────────────────────

@hook("deploy:precommand")
def _gitops_deploy(context):
    """Build, pin and sync for gitops hosts (runs after the artifact is created)."""
    from djaploy.deploy import _load_inventory_hosts

    hosts = context.get("_hosts") or _load_inventory_hosts(context["inventory_file"])
    context["_hosts"] = hosts
    gitops_hosts = [data for _, data in hosts if _get(data, "deployment_strategy") == GITOPS]
    if not gitops_hosts:
        return
    if len(gitops_hosts) > 1:
        raise ValueError("gitops inventories deploy one app per environment; use one host")
    host_data = gitops_hosts[0]
    conf = conf_of(host_data)

    branch = check_infra_repo(infra_repo(host_data), conf["manifest"])
    tag = image_tag(context["pyinfra_data"].get("commit") or "", context.get("mode", "latest"))
    digest = build_and_push(host_data, Path(context["artifact_path"]), conf["image"], tag)
    apply_env_secret(host_data)
    revision = publish_pin(host_data, digest, tag, branch)
    sync_and_wait(host_data, revision)
    context["gitops"] = {"image": f"{conf['image']}@{digest}", "revision": revision}
