c = get_config()  # noqa: F821

c.ServerApp.root_dir = "/home/ubuntu"
c.ServerApp.terminals_enabled = True
c.ServerApp.terminado_settings = {"shell_command": ["/bin/bash", "-l"]}
c.ServerApp.max_body_size = 8 * 1024 * 1024  # one chunk plus base64 overhead
c.ServerApp.max_buffer_size = 8 * 1024 * 1024
c.ServerApp.contents_manager_class = "shell_lab_extension.contents.QuotaAwareFileManager"
c.ContentsManager.allow_hidden = True
c.FileContentsManager.delete_to_trash = False
c.TerminalsExtensionApp.terminal_manager_class = "shell_lab_extension.terminals.CappedTerminalManager"
# Cull abandoned PTYs.
c.TerminalManager.cull_inactive_timeout = 900
c.TerminalManager.cull_interval = 60
c.JupyterArchive.stream_max_buffer_size = 16 * 1024 * 1024
c.JupyterArchive.handler_max_buffer_length = 256
c.JupyterArchive.archive_download_flush_delay = 100
