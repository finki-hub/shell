"""jupyter-server configuration for the shell-lab user container.

Loaded from /opt/jupyter/etc/jupyter, which JUPYTER_CONFIG_PATH points at. Every
limit that matters is enforced here, inside the container, rather than in the
SPA: the single-user server is reachable from the user's own shell on
127.0.0.1:8888.
"""

c = get_config()  # noqa: F821

c.ServerApp.root_dir = "/home/ubuntu"
c.ServerApp.terminals_enabled = True
c.ServerApp.terminado_settings = {"shell_command": ["/bin/bash", "-l"]}
c.ServerApp.max_body_size = 8 * 1024 * 1024  # one chunk + base64 overhead
c.ServerApp.max_buffer_size = 8 * 1024 * 1024
c.ServerApp.contents_manager_class = "finki_lab.contents.QuotaAwareFileManager"
c.ContentsManager.allow_hidden = True
c.FileContentsManager.delete_to_trash = False
c.TerminalsExtensionApp.terminal_manager_class = "finki_lab.terminals.CappedTerminalManager"
# Safety net for a pty whose browser tab vanished without a DELETE; the SPA
# deletes its own terminal on pagehide, so this rarely fires.
c.TerminalManager.cull_inactive_timeout = 900
c.TerminalManager.cull_interval = 60
c.JupyterArchive.stream_max_buffer_size = 16 * 1024 * 1024
c.JupyterArchive.handler_max_buffer_length = 256
c.JupyterArchive.archive_download_flush_delay = 100
