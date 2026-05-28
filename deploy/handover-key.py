"""
handover-key.py - Sichere Credential-Uebergabe an Claude Code-Sessions.

Pattern:
  1. User startet dieses Skript (oder die .bat)
  2. Skript fragt Key via getpass (Eingabe ausgeblendet, kein History-Leak)
  3. Skript schreibt Key in geschuetzte File:
     C:\\Users\\<User>\\.credentials\\<name>.key
  4. ACL wird via icacls auf USER-only gesetzt
     (Reset + grant <CurrentUser> F + grant SYSTEM F, alle anderen entfernt)
  5. Skript meldet Pfad
  6. Claude liest die File via Subprocess-ENV-Substitution:
       NEXTDNS_API_KEY=$(cat "<pfad>") python deploy/nextdns-allowlist.py
     (Key landet im Subprocess-RAM, NICHT im Tool-Result-stdout)
  7. Nach Use: Skript ueberschreibt File (oder User loescht)

Sicherheits-Begruendung:
  - getpass: kein Console-Echo, kein History-Leak (Powershell-History)
  - icacls-Reset entfernt Vererbung von Eltern-ACLs (sonst koennte
    "Authenticated Users" implizit lesen)
  - %USERPROFILE% statt %PUBLIC%: bleibt im User-Kontext, kein
    Cross-User-Read
  - Keine ENV-Variable im Parent (sonst Process-Inspector sichtbar)

Aufruf:
  python handover-key.py                  -> name=nextdns-api
  python handover-key.py <name>           -> name=<name>
  python handover-key.py <name> <key>     -> Key per CLI (NICHT empfohlen,
                                              Shell-History-Leak)

Default-Name: nextdns-api  -> File: ~/.credentials/nextdns-api.key
"""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from pathlib import Path


CREDENTIALS_DIR = Path.home() / ".credentials"
DEFAULT_NAME = "nextdns-api"


def fail(msg: str, code: int = 1) -> "NoReturn":
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(f"  {msg}")


def step(n: int, msg: str) -> None:
    print(f"\n[{n}] {msg}")


def redact(k: str) -> str:
    if len(k) < 12:
        return "***"
    return f"{k[:4]}...{k[-4:]} (len={len(k)})"


def lock_file_acl(path: Path) -> None:
    """Setze ACL: nur aktueller User + SYSTEM duerfen lesen/schreiben.

    icacls Schritte:
      1. /inheritance:r entfernt Vererbung (sonst implizit Authenticated Users)
      2. /grant fuer aktuelles User (F = Full)
      3. /grant SYSTEM:F (damit Tools weiter funktionieren)
    """
    if os.name != "nt":
        # POSIX: chmod 600
        os.chmod(path, 0o600)
        return

    user = os.environ.get("USERNAME") or os.environ.get("USER")
    if not user:
        info("WARNUNG: USERNAME nicht gesetzt, ACL-Schritt uebersprungen")
        return

    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["icacls", str(path), "/grant:r", f"{user}:F", "SYSTEM:F"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError:
        info("WARNUNG: icacls nicht gefunden, ACL-Schritt uebersprungen")
    except subprocess.CalledProcessError as e:
        info(f"WARNUNG: icacls failed: {e.stderr[:200]}")


def main(argv: list[str]) -> int:
    name = argv[0] if argv else DEFAULT_NAME
    if not re.fullmatch(r"[A-Za-z0-9._\-]{1,64}", name):
        fail(f"Ungueltiger Name {name!r} (nur A-Z, a-z, 0-9, .-_, max 64)")

    print(f"Storage  : {CREDENTIALS_DIR}")
    print(f"Name     : {name}")
    print(f"Use-Case : Uebergabe an Claude via $(cat <pfad>) im Subprocess-Call")

    step(1, "Storage-Verzeichnis sicherstellen")
    CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    # ACL auch auf Verzeichnis
    if os.name == "nt":
        user = os.environ.get("USERNAME", "")
        if user:
            subprocess.run(
                ["icacls", str(CREDENTIALS_DIR), "/inheritance:r"],
                capture_output=True,
            )
            subprocess.run(
                ["icacls", str(CREDENTIALS_DIR), "/grant:r", f"{user}:F", "SYSTEM:F"],
                capture_output=True,
            )
    info(f"OK: {CREDENTIALS_DIR}")

    step(2, "Key erfassen (Eingabe ausgeblendet)")
    if len(argv) >= 2:
        info("WARNUNG: Key via CLI-Arg uebergeben - das kann in Shell-History landen.")
        key = argv[1].strip()
    else:
        try:
            key = getpass.getpass(f"Key fuer '{name}': ").strip()
        except KeyboardInterrupt:
            print("\nAbgebrochen.")
            return 130
    if not key:
        fail("kein Key eingegeben")
    info(f"key: {redact(key)}")

    step(3, "File schreiben")
    target = CREDENTIALS_DIR / f"{name}.key"
    # Atomic write: temp + replace
    tmp = target.with_suffix(".key.tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(key)
    tmp.replace(target)
    info(f"OK: {target}")

    step(4, "ACL setzen (User-only)")
    lock_file_acl(target)
    info("OK")

    step(5, "Verify: File lesbar + Key-Roundtrip")
    with open(target, "r", encoding="utf-8") as f:
        readback = f.read().strip()
    if readback != key:
        fail("Roundtrip-Mismatch (File-Content != geschriebener Key)")
    info(f"Roundtrip OK ({len(readback)} chars)")

    print(f"\nFertig. Pfad fuer Claude:")
    print(f"  {target}")
    print(f"\nClaude-Subprocess-Pattern (Key landet NICHT in Tool-Result-stdout):")
    print(f'  NEXTDNS_API_KEY="$(cat \'{target}\')" python deploy/nextdns-allowlist.py')
    print(f"\nNach Use loeschen:")
    print(f"  Remove-Item '{target}'")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        sys.exit(130)
