from __future__ import annotations

import os
import re
import time
from typing import Optional

import paramiko

from app.infrastructure.ssh_jump import find_jump_key


_IAC = 255
_DONT = 254
_DO = 253
_WONT = 252
_WILL = 251
_SB = 250
_SE = 240


class TelnetJumpSession:
    """
    Sesion Telnet hacia una OLT ZTE/ZAC usando un jump host configurado por el consumidor.

    Flujo:
        NTTSERVER -> SSH noc_cable -> direct-tcpip -> OLT:23 -> Telnet

    Las credenciales de la OLT permanecen en el proceso local.
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        port: int = 23,
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
        self.channel = None

    @staticmethod
    def _strip_telnet(data: bytes) -> bytes:
        out = bytearray()
        i = 0
        n = len(data)

        while i < n:
            b = data[i]

            if b != _IAC:
                out.append(b)
                i += 1
                continue

            if i + 1 >= n:
                break

            cmd = data[i + 1]

            if cmd == _IAC:
                out.append(_IAC)
                i += 2
                continue

            if cmd in (_DO, _DONT, _WILL, _WONT):
                i += 3
                continue

            if cmd == _SB:
                i += 2
                while i + 1 < n:
                    if data[i] == _IAC and data[i + 1] == _SE:
                        i += 2
                        break
                    i += 1
                continue

            i += 2

        return bytes(out)

    def _negotiate(self, data: bytes) -> None:
        if self.channel is None:
            return

        replies = bytearray()
        i = 0
        n = len(data)

        while i < n:
            if data[i] != _IAC or i + 2 >= n:
                i += 1
                continue

            cmd = data[i + 1]
            opt = data[i + 2]

            if cmd == _WILL:
                replies.extend([_IAC, _DONT, opt])
                i += 3
            elif cmd == _DO:
                replies.extend([_IAC, _WONT, opt])
                i += 3
            elif cmd in (_WONT, _DONT):
                i += 3
            elif cmd == _SB:
                i += 2
                while i + 1 < n:
                    if data[i] == _IAC and data[i + 1] == _SE:
                        i += 2
                        break
                    i += 1
            else:
                i += 2

        if replies:
            self.channel.send(bytes(replies))

    def _recv(
        self,
        timeout: float = 8.0,
        idle_timeout: float = 0.7,
    ) -> str:
        if self.channel is None:
            raise RuntimeError("Sesion Telnet jump no iniciada.")

        chunks: list[bytes] = []
        started = time.monotonic()
        last_data = started

        while time.monotonic() - started < float(timeout):
            if self.channel.recv_ready():
                raw = self.channel.recv(65535)
                if not raw:
                    break

                self._negotiate(raw)
                clean = self._strip_telnet(raw)

                if clean:
                    chunks.append(clean)
                    last_data = time.monotonic()

                continue

            if chunks and time.monotonic() - last_data >= float(idle_timeout):
                break

            time.sleep(0.05)

        return b"".join(chunks).decode("utf-8", errors="replace")

    def _send_line(self, value: str) -> None:
        if self.channel is None:
            raise RuntimeError("Sesion Telnet jump no iniciada.")

        self.channel.send((str(value).rstrip() + "\r\n").encode("utf-8"))

    @staticmethod
    def _last_nonempty(text: str) -> str:
        for line in reversed(str(text or "").splitlines()):
            if line.strip():
                return line.strip()
        return ""

    def __enter__(self) -> "TelnetJumpSession":
        if not self.host:
            raise RuntimeError("OLT host/IP vacio.")

        if not self.username or not self.password:
            raise RuntimeError("Credenciales Telnet OLT incompletas.")

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

        try:
            channel = transport.open_channel(
                kind="direct-tcpip",
                dest_addr=(self.host, self.port),
                src_addr=("127.0.0.1", 0),
                timeout=self.connect_timeout,
            )
        except Exception:
            jump.close()
            raise

        self.jump_client = jump
        self.channel = channel

        text = self._recv(timeout=5.0, idle_timeout=0.8)

        if not re.search(r"(username|login|user\s*:)", text, re.IGNORECASE):
            self._send_line("")
            text += self._recv(timeout=3.0, idle_timeout=0.6)

        if re.search(r"(username|login|user\s*:)", text, re.IGNORECASE):
            self._send_line(self.username)
            text += self._recv(timeout=5.0, idle_timeout=0.8)

        if re.search(r"(password|passwd|pass\s*word)", text, re.IGNORECASE):
            self._send_line(self.password)
            text += self._recv(timeout=7.0, idle_timeout=1.0)

        final_line = self._last_nonempty(text)

        if not re.search(r"[>#]\s*$", final_line):
            self._send_line("")
            text += self._recv(timeout=4.0, idle_timeout=0.7)
            final_line = self._last_nonempty(text)

        if not re.search(r"[>#]\s*$", final_line):
            self.close()
            raise RuntimeError(
                "Login Telnet ZTE no confirmado. "
                f"Ultima linea: {final_line[-120:]}"
            )

        return self

    def execute(
        self,
        command: str,
        timeout: int = 35,
        idle_timeout: float = 1.0,
    ) -> str:
        if self.channel is None:
            raise RuntimeError("Sesion Telnet jump no iniciada.")

        while self.channel.recv_ready():
            self.channel.recv(65535)

        self._send_line(command)

        output = self._recv(
            timeout=float(timeout),
            idle_timeout=float(idle_timeout),
        )

        if not output and float(timeout) > 0:
            raise TimeoutError(f"Comando Telnet sin respuesta: {command}")

        return output

    def enable(self, secret: str = "") -> None:
        if not secret:
            return

        self.execute("enable", timeout=10, idle_timeout=0.4)
        self.execute(secret, timeout=10, idle_timeout=0.4)

    def close(self) -> None:
        try:
            if self.channel is not None:
                self.channel.close()
        except Exception:
            pass
        finally:
            self.channel = None

        try:
            if self.jump_client is not None:
                self.jump_client.close()
        except Exception:
            pass
        finally:
            self.jump_client = None

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()