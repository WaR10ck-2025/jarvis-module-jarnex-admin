"""
nextdns-allowlist.py - NextDNS-Profile-Allowlist Multi-Domain-Setter.

Generisch, nicht jarnex-spezifisch. Default-Domains: tuyaeu.com + tuyaus.com
(Tuya-Developer-Platform Mirror, fuer Cam-Integration relevant).

Sicherheit:
  - API-Key wird via getpass eingelesen (kein History-Leak)
  - ALTERNATIV: ENV NEXTDNS_API_KEY (z.B. aus secrets-File)
  - Skript validiert Key gegen GET /profiles/<id> BEVOR Domains gepushed werden
    (vermeidet silent-fail wie der erste NextDNS-Skill-Versuch 2026-05-29 mit
    rotiertem Key)

ENV (alle optional):
  NEXTDNS_API_KEY      neuer API-Key aus my.nextdns.io
  NEXTDNS_PROFILE_ID   default 41835f (User-Profil)
  NEXTDNS_DOMAINS      Komma-Liste, ueberschreibt CLI-Args + Defaults

Aufruf:
  python nextdns-allowlist.py
      -> fragt Key + nutzt Default-Domains (tuyaeu.com, tuyaus.com)

  python nextdns-allowlist.py example.com github.com
      -> fragt Key + allowlistet diese Domains statt Defaults

  $env:NEXTDNS_API_KEY="xxx"; python nextdns-allowlist.py
      -> nutzt ENV-Key, keine Eingabe noetig
"""
from __future__ import annotations

import getpass
import json
import os
import re
import sys
import urllib.error
import urllib.request


DEFAULT_DOMAINS = ("tuyaeu.com", "tuyaus.com")
PROFILE_ID = os.environ.get("NEXTDNS_PROFILE_ID", "41835f").strip()
API_BASE = "https://api.nextdns.io"


def info(msg: str) -> None:
    print(f"  {msg}")


def fail(msg: str, code: int = 1) -> "NoReturn":
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(code)


def step(n: int, msg: str) -> None:
    print(f"\n[{n}] {msg}")


def redact_key(k: str) -> str:
    if len(k) < 12:
        return "***"
    return f"{k[:4]}...{k[-4:]} (len={len(k)})"


def http_call(method: str, path: str, key: str, body: dict | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    """Returns (status_code, parsed_body). Wirft NICHT bei 4xx/5xx - Caller entscheidet."""
    url = f"{API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-Api-Key", key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = {"raw": raw[:300]}
        return e.code, parsed
    except urllib.error.URLError as e:
        fail(f"Verbindung zu {API_BASE} fehlgeschlagen: {e.reason}")
    return 0, {}


def prompt_key() -> str:
    env_key = os.environ.get("NEXTDNS_API_KEY", "").strip()
    if env_key:
        return env_key
    print("\nNextDNS-API-Key aus my.nextdns.io kopieren und einfuegen.")
    print("(Eingabe wird ausgeblendet, kein History-Risiko)")
    key = getpass.getpass("API-Key: ").strip()
    if not key:
        fail("kein Key eingegeben")
    return key


def validate_key_format(k: str) -> None:
    # NextDNS-Keys sind 40 Hex-Char (SHA1-Hash-Style)
    if not re.fullmatch(r"[0-9a-fA-F]{32,64}", k):
        fail(
            f"Key-Format ungewoehnlich ({len(k)} Zeichen). "
            "Erwartet 32-64 Hex-Char aus my.nextdns.io -> Account -> API."
        )


def resolve_domains(cli_args: list[str]) -> list[str]:
    env_domains = os.environ.get("NEXTDNS_DOMAINS", "").strip()
    if env_domains:
        out = [d.strip() for d in env_domains.split(",") if d.strip()]
    elif cli_args:
        out = list(cli_args)
    else:
        out = list(DEFAULT_DOMAINS)
    # Validate
    for d in out:
        if not re.fullmatch(r"[a-zA-Z0-9.\-]{3,253}", d):
            fail(f"Ungueltige Domain: {d!r}")
    return out


def main(argv: list[str]) -> int:
    domains = resolve_domains(argv)

    print(f"Profile  : {PROFILE_ID}")
    print(f"Domains  : {', '.join(domains)}")

    step(1, "API-Key holen + Format-Check")
    key = prompt_key()
    validate_key_format(key)
    info(f"key: {redact_key(key)}")

    step(2, "Profile-Reachability + Auth-Validate (GET /profiles/{id})")
    status, body = http_call("GET", f"/profiles/{PROFILE_ID}", key)
    if status == 403:
        err_code = (body.get("errors") or [{}])[0].get("code", "unknown")
        fail(
            f"Auth abgelehnt (HTTP 403, code={err_code}). "
            f"Key vermutlich rotiert. Neu generieren auf my.nextdns.io -> Account -> API."
        )
    if status != 200:
        fail(f"Profile-Probe HTTP {status}: {body}")
    info(f"Profile-Auth OK")

    step(3, "Aktuelle Allowlist lesen (fuer Idempotenz-Check)")
    status, body = http_call("GET", f"/profiles/{PROFILE_ID}/allowlist", key)
    if status != 200:
        fail(f"Allowlist-Read HTTP {status}: {body}")
    existing = set()
    for entry in body.get("data", []):
        if isinstance(entry, dict) and "id" in entry:
            existing.add(entry["id"])
    info(f"Aktuell {len(existing)} Eintraege")
    if existing:
        for d in sorted(existing)[:5]:
            info(f"  - {d}")
        if len(existing) > 5:
            info(f"  ... +{len(existing) - 5} weitere")

    step(4, "Domains pushen (idempotent: skip falls schon drin)")
    pushed: list[str] = []
    skipped: list[str] = []
    failed: list[tuple[str, str]] = []
    for domain in domains:
        if domain in existing:
            info(f"  {domain:30s} -> bereits aktiv, skip")
            skipped.append(domain)
            continue
        status, body = http_call(
            "POST", f"/profiles/{PROFILE_ID}/allowlist",
            key, body={"id": domain, "active": True},
        )
        if status in (200, 201, 204):
            info(f"  {domain:30s} -> hinzugefuegt (HTTP {status})")
            pushed.append(domain)
        else:
            err_code = (body.get("errors") or [{}])[0].get("code", "unknown")
            info(f"  {domain:30s} -> FEHLER HTTP {status} ({err_code})")
            failed.append((domain, f"HTTP {status} {err_code}"))

    step(5, "Verify: Allowlist erneut lesen")
    status, body = http_call("GET", f"/profiles/{PROFILE_ID}/allowlist", key)
    if status == 200:
        now_active = {e["id"] for e in body.get("data", []) if isinstance(e, dict) and "id" in e}
        for domain in domains:
            mark = "OK" if domain in now_active else "FEHLT"
            info(f"  {domain:30s} -> {mark}")

    step(6, "Zusammenfassung")
    info(f"hinzugefuegt : {len(pushed)} ({', '.join(pushed) if pushed else '-'})")
    info(f"schon-aktiv  : {len(skipped)} ({', '.join(skipped) if skipped else '-'})")
    info(f"fehlgeschl.  : {len(failed)} ({', '.join(d for d,_ in failed) if failed else '-'})")
    print("\nHinweis: DNS-Cache wird in ~5 Sekunden aktualisiert.")
    print("Erneuter Test:  curl -s -o nul -w '%{http_code}\\n' https://iot.tuyaeu.com/")

    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        sys.exit(130)
