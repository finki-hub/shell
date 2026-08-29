"""JupyterHub configuration for the FINKI shell hub (image ``shell-hub``).

Deliberately thin: every value comes from :class:`finki_hub.settings.Settings`,
and the boot invariants are asserted here at import time. JupyterHub runs with
``raise_config_file_errors=True``, so anything raised below stops the hub before
it can serve a single request.
"""

import sys

from finki_hub.auth import EnvironmentTokenAuthenticator
from finki_hub.quota import pre_spawn_hook
from finki_hub.readiness import assert_pool
from finki_hub.settings import COOKIE_MAX_AGE_DAYS, get_settings

c = get_config()  # type: ignore[name-defined] # ruff: ignore[undefined-name] - traitlets injects get_config here

# Boot invariants. Settings() raises on an invalid .env (memory headroom, root
# uid, unpaired or dummy Turnstile keys); assert_pool raises unless /srv/pool is
# the XFS filesystem with project quotas that the quota machinery assumes.
settings = get_settings()
pool_id = settings.bind_pool_id()
assert_pool(settings.pool_mount, expected_pool_id=pool_id)

lab_home = f"/home/{settings.lab_user}"
quota_bytes = settings.lab_env_quota_mb * 1024 * 1024

# --- Hub process, proxy and persistence (contract section 4) -----------------
c.JupyterHub.bind_url = "http://127.0.0.1:8000"
c.JupyterHub.hub_bind_url = "http://172.30.0.1:8081"
c.JupyterHub.hub_connect_url = "http://172.30.0.1:8081"
c.JupyterHub.log_level = settings.log_level
c.JupyterHub.db_url = f"sqlite:///{settings.hub_data_dir}/jupyterhub.sqlite"
c.JupyterHub.upgrade_db = True
c.JupyterHub.cookie_secret_file = f"{settings.hub_data_dir}/jupyterhub_cookie_secret"

c.ConfigurableHTTPProxy.should_start = False
c.ConfigurableHTTPProxy.api_url = "http://127.0.0.1:8001"
c.ConfigurableHTTPProxy.auth_token = settings.configproxy_auth_token.get_secret_value()

# A hub restart never stops a running environment.
c.JupyterHub.cleanup_servers = False
c.JupyterHub.cleanup_proxy = False

# --- Identity (contract section 7) -------------------------------------------
c.JupyterHub.authenticator_class = EnvironmentTokenAuthenticator
c.JupyterHub.cookie_max_age_days = COOKIE_MAX_AGE_DAYS
c.JupyterHub.active_server_limit = settings.lab_max_sessions
c.JupyterHub.concurrent_spawn_limit = 10
c.Spawner.start_timeout = 120
c.Spawner.http_timeout = 60
c.Spawner.server_token_scopes = ["users:activity!user", "access:servers!server"]
c.Spawner.pre_spawn_hook = pre_spawn_hook

# --- User containers (contract section 8) ------------------------------------
c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
c.DockerSpawner.image = settings.lab_image
c.DockerSpawner.network_name = "finki-hub-shell-users"
c.DockerSpawner.use_internal_ip = True
c.DockerSpawner.hub_connect_url = "http://172.30.0.1:8081"
c.DockerSpawner.name_template = "lab-{username}"
c.DockerSpawner.port = 8888
c.DockerSpawner.remove = True
c.DockerSpawner.pull_policy = "ifnotpresent"
c.DockerSpawner.volumes = {
    f"{settings.lab_pool_dir}/users/{{username}}": {"bind": lab_home, "mode": "rw"}
}
c.DockerSpawner.environment = {
    "HOME": lab_home,
    "TERM": "xterm-256color",
    "LANG": "C.UTF-8",
    "TMPDIR": f"{lab_home}/.tmp",
    "TZ": settings.tz,
    "JUPYTERHUB_ALLOW_TOKEN_IN_URL": "1",
    "LAB_MAX_TERMINALS": str(settings.lab_max_terminals),
}
c.DockerSpawner.extra_create_kwargs = {
    "user": f"{settings.lab_uid}:{settings.lab_gid}",
    "hostname": "lab",
    "working_dir": lab_home,
    "labels": {"finki.role": "lab", "finki.user": "{username}"},
}
c.DockerSpawner.extra_host_config = {
    "read_only": True,
    "mem_limit": f"{settings.lab_memory_mb}m",
    "memswap_limit": f"{settings.lab_memory_mb}m",
    "nano_cpus": int(settings.lab_cpus * 1_000_000_000),
    "pids_limit": settings.lab_pids,
    "cap_drop": ["ALL"],
    "security_opt": ["no-new-privileges:true"],
    "init": True,
    "tmpfs": {
        "/tmp": f"rw,nosuid,nodev,size={settings.lab_tmp_mb}m,mode=1777",
        "/var/tmp": f"rw,nosuid,nodev,size={settings.lab_vartmp_mb}m,mode=1777",
        "/run": f"rw,nosuid,nodev,size={settings.lab_run_mb}m,mode=0755",
        "/dev/shm": f"rw,nosuid,nodev,size={settings.lab_shm_mb}m,mode=1777",
    },
    "log_config": {
        "Type": "json-file",
        "Config": {
            "max-size": settings.lab_log_max_size,
            "max-file": str(settings.lab_log_max_files),
        },
    },
    "ulimits": [{"Name": "fsize", "Soft": quota_bytes, "Hard": quota_bytes}],
    "oom_score_adj": 500,
}

# --- Managed services and their roles (contract section 9) -------------------
c.JupyterHub.services = [
    # --max-age recreates a container that has run for LAB_CONTAINER_MAX_AGE_H
    # hours however busy it is; the home directory it mounts is untouched.
    {
        "name": "idle-culler-servers",
        "command": [
            sys.executable,
            "-m",
            "jupyterhub_idle_culler",
            f"--timeout={settings.lab_idle_min * 60}",
            f"--max-age={settings.lab_container_max_age_h * 3600}",
            "--cull-every=60",
        ],
    },
    {
        "name": "idle-culler-users",
        "command": [
            sys.executable,
            "-m",
            "jupyterhub_idle_culler",
            "--cull-users=true",
            f"--timeout={settings.lab_retention_h * 3600}",
            f"--max-age={settings.lab_max_age_h * 3600}",
            "--cull-every=3600",
        ],
    },
]
c.JupyterHub.load_roles = [
    {
        "name": "idle-culler-servers",
        "services": ["idle-culler-servers"],
        "scopes": [
            "list:users",
            "read:users:activity",
            "read:servers",
            "delete:servers",
        ],
    },
    {
        "name": "idle-culler-users",
        "services": ["idle-culler-users"],
        "scopes": [
            "list:users",
            "read:users:activity",
            "read:servers",
            "delete:servers",
            "delete:users",
        ],
    },
]
