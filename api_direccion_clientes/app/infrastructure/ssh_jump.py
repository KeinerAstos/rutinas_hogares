from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

import paramiko


def _candidate_jump_keys() -> list[Path]:
    values: list[Path] = []

    configured = (os.getenv("HFC_REMOTE_KEY") or "").strip()
    if configured:
        values.append(Path(configured))

    configured = (os.getenv("ATLAS_HFC_ANALYTICS_SSH_KEY") or "").strip()
    if configured:
        values.append(Path(configured))

    values.extend(
        [
            Path.home() / ".ssh" / "noc_cable_atlas_ed25519",
            Path(r"C:\Users\NTTSERVER\.ssh\noc_cable_atlas_ed25519"),
            Path(r"C:\Users\NTTSERVER.ssh\noc_cable_atlas_ed25519"),
        ]
    )

    result: list[Path] = []
    seen: set[str] = set()

    for item in values:
        key = str(item).lower()
        if key not in seen:
            result.append(item)
            seen.add(key)

    return result


def find_jump_key() -> Optional[Path]:
    for candidate in _candidate_jump_keys():
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


class SSHJumpSession:
    """
    Sesion SSH hacia una OLT usando un jump host configurado por el consumidor.

    NTTSERVER -> noc_cable -> direct-tcpip -> OLT

    Las credenciales de la OLT se mantienen en el proceso local.
    No se copian al filesystem de noc_cable.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 22,
        connect_timeout: int = 15,
        jump_host: str | None = None,
        jump_port: int | None = None,
        jump_user: str = "noc_cable",
    ):
        self.host = str(host).strip()
        self.username = str(username)
        self.password = str(password)
        self.port = int(port)
        self.connect_timeout = int(connect_timeout)

        self.jump_host = str(jump_host or "").strip()
        self.jump_port = int(jump_port or 0)
        self.jump_user = str(jump_user or "").strip()

        self.jump_client: Optional[paramiko.SSHClient] = None
        self.client: Optional[paramiko.SSHClient] = None
        self.channel = None
        self.shell = None

    def __enter__(self) -> "SSHJumpSession":
        if not self.host:
            raise RuntimeError("OLT host/IP vacio.")
        if not self.username or not self.password:
            raise RuntimeError("Credenciales SSH OLT incompletas.")

        if not self.jump_host:
            raise RuntimeError(
                "Jump host no configurado. "
                "El consumidor debe asignar jump_host antes de abrir la sesion."
            )

        if self.jump_port <= 0:
            raise RuntimeError(
                "Jump port no configurado. "
                "El consumidor debe asignar jump_port antes de abrir la sesion."
            )

        if not self.jump_user:
            raise RuntimeError(
                "Jump user no configurado."
            )

        key_path = find_jump_key()
        if key_path is None:
            raise RuntimeError("No se encontro llave SSH para noc_cable.")

        jump = paramiko.SSHClient()
        jump.load_system_host_keys()
        jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        pkey = paramiko.Ed25519Key.from_private_key_file(str(key_path))

        jump.connect(
            hostname=self.jump_host,
            port=self.jump_port,
            username=self.jump_user,
            pkey=pkey,
            timeout=10,
            auth_timeout=10,
            banner_timeout=10,
            look_for_keys=False,
            allow_agent=False,
        )

        transport = jump.get_transport()
        if transport is None or not transport.is_active():
            jump.close()
            raise RuntimeError("Transport SSH hacia noc_cable no esta activo.")

        channel = transport.open_channel(
            kind="direct-tcpip",
            dest_addr=(self.host, self.port),
            src_addr=("127.0.0.1", 0),
            timeout=self.connect_timeout,
        )

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        client.connect(
            hostname=self.host,
            port=self.port,
            username=self.username,
            password=self.password,
            sock=channel,
            timeout=self.connect_timeout,
            auth_timeout=self.connect_timeout,
            banner_timeout=self.connect_timeout,
            look_for_keys=False,
            allow_agent=False,
        )

        shell = client.invoke_shell(width=240, height=1000)
        time.sleep(0.5)

        while shell.recv_ready():
            shell.recv(65535)

        self.jump_client = jump
        self.channel = channel
        self.client = client
        self.shell = shell
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if self.shell is not None:
                self.shell.close()
        except Exception:
            pass

        try:
            if self.client is not None:
                self.client.close()
        except Exception:
            pass

        try:
            if self.channel is not None:
                self.channel.close()
        except Exception:
            pass

        try:
            if self.jump_client is not None:
                self.jump_client.close()
        except Exception:
            pass

    def execute(
        self,
        command: str,
        timeout: int = 35,
        idle_timeout: float = 1.0,
    ) -> str:
        if self.shell is None:
            raise RuntimeError("Sesion SSH jump no iniciada.")

        while self.shell.recv_ready():
            self.shell.recv(65535)

        self.shell.send(str(command).rstrip() + "\n")

        chunks: list[str] = []
        started = time.monotonic()
        last_data = started

        while time.monotonic() - started < float(timeout):
            if self.shell.recv_ready():
                data = self.shell.recv(65535).decode("utf-8", errors="replace")
                chunks.append(data)
                last_data = time.monotonic()
                continue

            if chunks and time.monotonic() - last_data >= float(idle_timeout):
                break

            time.sleep(0.08)

        if not chunks and time.monotonic() - started >= float(timeout):
            raise TimeoutError(f"Comando SSH excedio {timeout}s: {command}")

        return "".join(chunks)

    def enable(self, secret: str = "") -> None:
        if not secret:
            return
        self.execute("enable", timeout=10, idle_timeout=0.4)
        self.execute(secret, timeout=10, idle_timeout=0.4)