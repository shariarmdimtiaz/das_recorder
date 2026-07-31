from __future__ import annotations

import ctypes
import platform
import subprocess
from dataclasses import dataclass
from typing import List, Tuple


@dataclass(frozen=True)
class FirewallRule:
    name: str
    direction: str
    port_kind: str
    port: int


DUNAY_FIREWALL_RULES: List[FirewallRule] = [
    FirewallRule("DASRecorder_Dunay_Command_Responses_UDP_8211", "in", "localport", 8211),
    FirewallRule("DASRecorder_Dunay_Phase_Data_UDP_8227", "in", "localport", 8227),
    FirewallRule("DASRecorder_Dunay_Commands_UDP_8201", "out", "remoteport", 8201),
]


def is_windows() -> bool:
    return platform.system().lower() == "windows"


def is_admin() -> bool:
    if not is_windows():
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _netsh_command(rule: FirewallRule) -> List[str]:
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "add",
        "rule",
        f"name={rule.name}",
        f"dir={rule.direction}",
        "action=allow",
        "protocol=UDP",
        f"{rule.port_kind}={rule.port}",
        "profile=any",
        "enable=yes",
    ]


def _delete_command(rule: FirewallRule) -> List[str]:
    return [
        "netsh",
        "advfirewall",
        "firewall",
        "delete",
        "rule",
        f"name={rule.name}",
    ]


def configure_dunay_firewall_rules(elevate: bool = True) -> Tuple[bool, str]:
    """Add Windows Firewall rules needed by the Dunay UDP protocol.

    Returns (success, message). If called without Administrator rights and
    elevate=True, opens a UAC prompt in a separate command window.
    """
    if not is_windows():
        return False, "Windows Firewall rules can only be configured on Windows."

    if not is_admin():
        if elevate:
            return _open_elevated_firewall_setup()
        return False, "Administrator permission is required to change Windows Firewall."

    messages = []
    for rule in DUNAY_FIREWALL_RULES:
        # Delete first so repeated setup updates the existing rule cleanly.
        subprocess.run(_delete_command(rule), text=True, capture_output=True, check=False)
        result = subprocess.run(_netsh_command(rule), text=True, capture_output=True, check=False)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            return False, f"Failed to add firewall rule '{rule.name}': {detail}"
        messages.append(f"{rule.name} ({rule.direction} UDP {rule.port})")

    return True, "Firewall rules added:\n" + "\n".join(messages)


def _open_elevated_firewall_setup() -> Tuple[bool, str]:
    commands = []
    for rule in DUNAY_FIREWALL_RULES:
        delete_cmd = " ".join(_quote_arg(part) for part in _delete_command(rule))
        add_cmd = " ".join(_quote_arg(part) for part in _netsh_command(rule))
        commands.append(delete_cmd)
        commands.append(add_cmd)

    command_text = " & ".join(commands)
    command_text += " & echo. & echo DASRecorder firewall rules configured. & pause"

    try:
        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            "cmd.exe",
            f'/c "{command_text}"',
            None,
            1,
        )
    except Exception as exc:
        return False, f"Could not open Administrator prompt: {exc}"

    if int(rc) <= 32:
        return False, f"Administrator prompt failed to open. ShellExecute code: {rc}"

    return True, "Administrator permission prompt opened to configure Windows Firewall."


def _quote_arg(value: str) -> str:
    if not value:
        return '""'
    if any(ch.isspace() for ch in value) or '"' in value:
        return '"' + value.replace('"', '\\"') + '"'
    return value
