import os
import time
from pathlib import Path

import paramiko


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "si", "sí", "on"}


class SSHSession:
    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        *,
        port: int = 22,
        connect_timeout: int = 15,
    ) -> None:
        self.host = host
        self.username = username
        self.password = password
        self.port = port
        self.connect_timeout = connect_timeout
        self.client: paramiko.SSHClient | None = None
        self.channel: paramiko.Channel | None = None

    def __enter__(self) -> "SSHSession":
        client = paramiko.SSHClient()
        client.load_system_host_keys()

        known_hosts = os.getenv("ATLAS_SSH_KNOWN_HOSTS", "").strip()
        if known_hosts and Path(known_hosts).is_file():
            client.load_host_keys(known_hosts)

        if _as_bool(os.getenv("ATLAS_SSH_STRICT_HOST_KEY"), default=False):
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            timeout=self.connect_timeout,
            auth_timeout=self.connect_timeout,
            banner_timeout=self.connect_timeout,
            look_for_keys=False,
            allow_agent=False,
        )
        self.client = client
        self.channel = client.invoke_shell(width=240, height=1000)
        self._read_until_idle(timeout=5, idle_timeout=0.5)
        return self

    def __exit__(self, *_: object) -> None:
        if self.channel is not None:
            self.channel.close()
        if self.client is not None:
            self.client.close()

    def execute(self, command: str, *, timeout: int = 35, idle_timeout: float = 1.0) -> str:
        if self.channel is None:
            raise RuntimeError("La sesión SSH no está conectada.")

        self._drain()
        self.channel.send(command.rstrip("\r\n") + "\n")
        return self._read_until_idle(timeout=timeout, idle_timeout=idle_timeout)

    def enable(self, secret: str = "") -> str:
        output = self.execute("enable", timeout=10, idle_timeout=0.7)
        if "password" in output.lower():
            if not secret:
                raise RuntimeError("El equipo solicitó clave de enable y no está configurada.")
            output += self.execute(secret, timeout=10, idle_timeout=0.7)
        return output

    def _drain(self) -> None:
        if self.channel is None:
            return
        while self.channel.recv_ready():
            self.channel.recv(65535)

    def _read_until_idle(self, *, timeout: int, idle_timeout: float) -> str:
        if self.channel is None:
            return ""

        started = time.monotonic()
        last_data = started
        chunks: list[str] = []

        while time.monotonic() - started < timeout:
            if self.channel.recv_ready():
                chunk = self.channel.recv(65535).decode("utf-8", errors="replace")
                chunks.append(chunk)
                last_data = time.monotonic()
                continue

            if chunks and time.monotonic() - last_data >= idle_timeout:
                break

            if self.channel.closed:
                break

            time.sleep(0.1)

        return "".join(chunks)
