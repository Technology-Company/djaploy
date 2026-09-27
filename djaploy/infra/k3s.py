"""
k3s deployment strategy: ``deployment_strategy="k3s"``.

The app runs as a container on a k3s node, rendered from the bundled
``charts/django-app`` Helm chart. Server access (SSH key, sudo password) comes
from the inventory exactly as for the other strategies.

    deploy:precommand (local)  build the image from the git artifact, save it
    deploy:upload              import the image into k3s over SSH (skipped if
                               the node already has it), upload the chart
    deploy:configure           write the release's values and its env Secret
                               (secret_key + data, e.g. from OpSecret)
    deploy:start               helm upgrade --install, wait for the rollout
    configure                  install helm, give the SSH user a kubeconfig
    rollback                   helm rollback

HostConfig fields used: app_name, app_hostname, manage_py_path, gunicorn_conf
(workers, timeout, wsgi_module), nginx_conf (client_max_body_size), secret_key,
data, and ``k3s_conf``::

    k3s_conf={
        "namespace": "docms",                     # default: app_name
        "image": "djaploy/docms",                 # default: djaploy/<git dir name>
        "platform": "linux/amd64",                # node architecture
        "settings_module": "docms.settings.production",
        "static_root": "/app/docms/public/static",
        "extra_commands": ["seed_realty --org-slug bo"],
        "hostnames": ["old.example.com"],         # extra, after app_hostname
        "storage_size": "20Gi",
        "suspended": False,
        "env": {"FOO": "bar"},                    # non-secret env vars
        "values": {...},                          # raw chart values, merged last
    }
"""

import io
import json
import subprocess
import tarfile
from pathlib import Path

from djaploy.hooks import deploy_hook, hook

K3S = ("k3s",)
HELM_VERSION = "v4.3.0"
CHART_DIR = Path(__file__).parent / "charts" / "django-app"
KUBECONFIG = "export KUBECONFIG=$HOME/.kube/config"


def _get(data, key, default=None):
    value = data.get(key, default) if isinstance(data, dict) else getattr(data, key, default)
    return default if value is None else value


def remote_root(host_data) -> str:
    return f"/home/{_get(host_data, 'ssh_user')}/.djaploy/k3s"


def chart_version() -> str:
    for line in (CHART_DIR / "Chart.yaml").read_text().splitlines():
        if line.startswith("version:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("chart version not found")


def release_name(host_data) -> str:
    return _get(_get(host_data, "k3s_conf", {}), "namespace") or _get(host_data, "app_name")


def _wsgi_module(host_data) -> str:
    module = _get(_get(host_data, "gunicorn_conf", {}), "wsgi_module")
    if module:
        return module
    from django.conf import settings
    app = getattr(settings, "WSGI_APPLICATION", "")  # "docms.wsgi.application"
    return app.rsplit(".", 1)[0] + ":" + app.rsplit(".", 1)[1] if app else ""


def _merge(base: dict, extra: dict) -> dict:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value
    return base


def build_values(host_data, image_ref: str) -> dict:
    """Chart values for a host (no secrets: those go in the env Secret)."""
    conf = _get(host_data, "k3s_conf", {})
    gunicorn = _get(host_data, "gunicorn_conf", {})
    nginx = _get(host_data, "nginx_conf", {})
    release = release_name(host_data)
    repository, tag = image_ref.rsplit(":", 1)

    values = {
        "image": {"repository": repository, "tag": tag, "pullPolicy": "IfNotPresent"},
        "hostnames": [h for h in [_get(host_data, "app_hostname"), *_get(conf, "hostnames", [])] if h],
        "suspended": bool(_get(conf, "suspended", False)),
        "django": {
            "managePy": _get(host_data, "manage_py_path", "manage.py"),
            "wsgiModule": _wsgi_module(host_data),
            "settingsModule": _get(conf, "settings_module", ""),
            "env": dict(_get(conf, "env", {})),
        },
        "gunicorn": {k: gunicorn[k] for k in ("workers", "timeout") if k in gunicorn},
        "envFromSecret": f"{release}-env",
        "onePassword": {"env": {}, "pullSecretItem": ""},
    }
    if _get(conf, "static_root"):
        values["staticRoot"] = conf["static_root"]
    if _get(conf, "storage_size"):
        values["storage"] = {"size": conf["storage_size"]}
    if _get(conf, "extra_commands"):
        values["migrations"] = {"extraCommands": list(conf["extra_commands"])}
    if _get(nginx, "client_max_body_size"):
        values["nginx"] = {"clientMaxBodySize": nginx["client_max_body_size"]}
    return _merge(values, dict(_get(conf, "values", {})))


def build_env_secret(host_data) -> dict:
    """The release's env Secret: SECRET_KEY + every scalar in `data`, upper-cased."""
    env = {}
    secret_key = _get(host_data, "secret_key")
    if secret_key:
        env["SECRET_KEY"] = str(secret_key)
    for key, value in _get(host_data, "data", {}).items():
        if isinstance(value, (dict, list, tuple, set, bool)) or value is None:
            continue
        env[str(key).upper()] = str(value)
    release = release_name(host_data)
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "type": "Opaque",
        "metadata": {"name": f"{release}-env", "namespace": release},
        "stringData": env,
    }


def image_ref_for(conf: dict, commit: str, mode: str, release: str = None) -> str:
    from django.conf import settings
    repository = _get(conf, "image") or f"djaploy/{Path(settings.GIT_DIR).name.lower()}"
    if mode == "release" and release:
        tag = release
    elif mode == "local":
        import time
        tag = f"{commit}-local-{int(time.time())}"
    else:
        tag = commit
    return f"{repository}:{tag}"


# ── local: build the image ────────────────────────────────────────────

@hook("deploy:precommand")
def _k3s_build_image(context):
    """Build the image from the git artifact and save it for upload."""
    from djaploy.deploy import _load_inventory_hosts

    hosts = context.get("_hosts") or _load_inventory_hosts(context["inventory_file"])
    context["_hosts"] = hosts
    k3s_hosts = [data for _, data in hosts if _get(data, "deployment_strategy") == "k3s"]
    if not k3s_hosts:
        return

    host = k3s_hosts[0]
    conf = _get(host, "k3s_conf", {})
    commit = context["pyinfra_data"].get("commit") or "unknown"
    image = image_ref_for(conf, commit, context.get("mode", "latest"), context.get("release"))
    artifact = Path(context["artifact_path"])
    archive = artifact.parent / f"image.{image.replace('/', '_').replace(':', '.')}.tar.gz"

    have = subprocess.run(["docker", "image", "inspect", image], capture_output=True).returncode == 0
    if not have:
        print(f"Building image {image}...", flush=True)
        cmd = ["docker", "build", "--platform", _get(conf, "platform", "linux/amd64"), "-t", image,
               "--build-arg", f"MANAGE_PY={_get(host, 'manage_py_path', 'manage.py')}"]
        if _get(conf, "settings_module"):
            cmd += ["--build-arg", f"SETTINGS_MODULE={conf['settings_module']}"]
        with open(artifact, "rb") as context_tar:
            subprocess.run(cmd + ["-"], stdin=context_tar, check=True)
    if not archive.exists():
        print(f"Saving {image} → {archive.name}...", flush=True)
        with open(archive, "wb") as out:
            save = subprocess.Popen(["docker", "save", image], stdout=subprocess.PIPE)
            subprocess.run(["gzip", "-1"], stdin=save.stdout, stdout=out, check=True)
            if save.wait() != 0:
                archive.unlink(missing_ok=True)
                raise RuntimeError(f"docker save {image} failed")

    context["k3s_image"] = image
    context["pyinfra_data"]["k3s_image"] = image
    context["pyinfra_data"]["k3s_image_archive"] = str(archive)


# ── remote ────────────────────────────────────────────────────────────

@deploy_hook("configure", strategies=K3S)
def k3s_configure(host_data):
    """Install helm and give the SSH user a kubeconfig."""
    from pyinfra.operations import server

    user = _get(host_data, "ssh_user")
    server.shell(
        name=f"Install helm {HELM_VERSION}",
        commands=[f"""set -e
V={HELM_VERSION}
if [ "$(helm version --short 2>/dev/null | cut -d+ -f1)" != "$V" ]; then
  A=$(uname -m); case $A in x86_64) A=amd64 ;; aarch64) A=arm64 ;; esac
  T=$(mktemp -d); cd "$T"
  curl -fsSLO https://get.helm.sh/helm-$V-linux-$A.tar.gz
  curl -fsSLO https://get.helm.sh/helm-$V-linux-$A.tar.gz.sha256sum
  sha256sum -c helm-$V-linux-$A.tar.gz.sha256sum
  tar -xzf helm-$V-linux-$A.tar.gz
  install -m755 linux-$A/helm /usr/local/bin/helm
  cd /; rm -rf "$T"
fi"""],
        _sudo=True,
    )
    server.shell(
        name=f"kubeconfig for {user}",
        commands=[f"install -d -m700 -o {user} -g {user} /home/{user}/.kube && "
                  f"install -m600 -o {user} -g {user} /etc/rancher/k3s/k3s.yaml /home/{user}/.kube/config"],
        _sudo=True,
    )


@deploy_hook("deploy:upload", strategies=K3S)
def k3s_upload(host_data, artifact_path):
    """Import the image into k3s (unless present) and upload the chart."""
    from pyinfra import host
    from pyinfra.facts.server import Command
    from pyinfra.operations import files, server

    image = _get(host_data, "k3s_image")
    archive = _get(host_data, "k3s_image_archive")
    ref = image if "." in image.split("/")[0] else f"docker.io/{image}"  # how containerd names it
    present = host.get_fact(
        Command, command=f"k3s ctr -n k8s.io images ls -q name=={ref} 2>/dev/null || true", _sudo=True,
    )
    if (present or "").strip() == ref:
        print(f"[k3s] {ref} already on the node; skipping upload", flush=True)
    else:
        remote = f"/tmp/djaploy-{Path(archive).name}"
        files.put(name=f"Upload image {image}", src=archive, dest=remote)
        server.shell(
            name=f"Import image {image} into k3s",
            commands=[f"gunzip -c {remote} | k3s ctr -n k8s.io images import - && rm -f {remote}"],
            _sudo=True,
        )

    root = remote_root(host_data)
    version = chart_version()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(str(CHART_DIR), arcname="django-app")
    buffer.seek(0)
    files.put(name=f"Upload chart django-app {version}", src=buffer, dest="/tmp/djaploy-chart.tar.gz")
    server.shell(
        name="Unpack chart",
        commands=[f"mkdir -p {root}/charts && rm -rf {root}/charts/django-app-{version} && "
                  f"mkdir {root}/charts/django-app-{version} && "
                  f"tar -xzf /tmp/djaploy-chart.tar.gz -C {root}/charts/django-app-{version} --strip-components=1 && "
                  f"rm -f /tmp/djaploy-chart.tar.gz"],
    )


@deploy_hook("deploy:configure", strategies=K3S)
def k3s_configure_release(host_data, artifact_path):
    """Write the release's values and apply its env Secret."""
    from pyinfra.operations import files, server

    release = release_name(host_data)
    folder = f"{remote_root(host_data)}/{release}"
    values = build_values(host_data, _get(host_data, "k3s_image"))
    server.shell(name=f"Folder for {release}", commands=[f"mkdir -p -m700 {folder}"])
    files.put(name=f"Values for {release}", src=io.StringIO(json.dumps(values, indent=2)),
              dest=f"{folder}/values.json", mode="600")
    files.put(name=f"Env secret for {release}", src=io.StringIO(json.dumps(build_env_secret(host_data))),
              dest=f"{folder}/secret.json", mode="600")
    server.shell(
        name=f"Apply {release}-env",
        commands=[f"{KUBECONFIG}; kubectl get namespace {release} >/dev/null 2>&1 || kubectl create namespace {release}; "
                  f"kubectl apply -f {folder}/secret.json"],
    )


@deploy_hook("deploy:start", strategies=K3S)
def k3s_helm_upgrade(host_data, artifact_path):
    """helm upgrade --install, then wait for migrations and the new pod."""
    from pyinfra.operations import server

    release = release_name(host_data)
    suspended = bool(_get(_get(host_data, "k3s_conf", {}), "suspended", False))
    # A suspended release has no pod, so its volume stays unbound: nothing to wait for.
    wait = "" if suspended else "--wait --timeout 15m"
    root = remote_root(host_data)
    chart = f"{root}/charts/django-app-{chart_version()}"
    server.shell(
        name=f"helm upgrade {release}",
        commands=[f"""{KUBECONFIG}
helm upgrade --install {release} {chart} --namespace {release} --create-namespace \\
  -f {root}/{release}/values.json --history-max 10 {wait} || {{
  echo "--- {release} failed; pods, migrate log and events:"
  timeout 20 kubectl -n {release} get pods -o wide
  timeout 20 kubectl -n {release} logs deploy/{release} --all-containers --tail=40 2>&1 | tail -60
  timeout 20 kubectl -n {release} get events --sort-by=.lastTimestamp | tail -15
  exit 1
}}
kubectl -n {release} get pods -o wide"""],
    )


@deploy_hook("rollback", strategies=K3S)
def k3s_helm_rollback(host_data, release=None):
    """helm rollback to the previous revision, or to --release <revision number>."""
    from pyinfra.operations import server

    name = release_name(host_data)
    if release and not str(release).isdigit():
        raise ValueError(f"k3s rollback takes a revision number (see: helm history {name} -n {name}), not {release!r}")
    server.shell(
        name=f"helm rollback {name}",
        commands=[f"{KUBECONFIG}; helm rollback {name} {release or ''} --namespace {name} --wait --timeout 15m && "
                  f"helm history {name} --namespace {name} --max 5"],
    )
