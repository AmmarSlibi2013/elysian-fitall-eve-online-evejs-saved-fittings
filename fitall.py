#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.dom import minidom
from xml.etree.ElementTree import Element, SubElement, tostring


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


TOOL_ROOT = Path(__file__).resolve().parent
CONFIG_ROOT = TOOL_ROOT / "config"
EVEJS_PATH_FILE = CONFIG_ROOT / "evejs.path"

FITTINGS_OUTPUT_ROOT = TOOL_ROOT / "Fittings"
REPORTS_OUTPUT_ROOT = TOOL_ROOT / "reports"
BACKUPS_OUTPUT_ROOT = TOOL_ROOT / "backups"
CACHE_OUTPUT_ROOT = TOOL_ROOT / "_cache"
BUNDLED_DATA_ROOT = TOOL_ROOT / "data"

LIBRARY_JSON_PATH = FITTINGS_OUTPUT_ROOT / "library.json"
BUNDLED_LIBRARY_JSON_PATH = BUNDLED_DATA_ROOT / "fitall-library.json"
ALL_SHIPS_XML_PATH = FITTINGS_OUTPUT_ROOT / "all-ships.xml"
PER_SHIP_XML_ROOT = FITTINGS_OUTPUT_ROOT / "xml"
CHECKLIST_JSON_PATH = REPORTS_OUTPUT_ROOT / "checklist.json"
CHECKLIST_MD_PATH = REPORTS_OUTPUT_ROOT / "checklist.md"
SEED_SUMMARY_JSON_PATH = REPORTS_OUTPUT_ROOT / "seed-summary.json"
BENCHMARK_SUMMARY_JSON_PATH = REPORTS_OUTPUT_ROOT / "benchmark-summary.json"

TOOL_DESCRIPTION_PREFIX = "FitALL:"
TOOL_NAME_PREFIX = "FitALL | "
MAX_CHAR_FITTINGS = 500
USER_AGENT = "FitALL/1.0 (EvEJS local fitting harvester)"
NETWORK_MIN_INTERVAL_SECONDS = 0.18
DEFAULT_THREAD_COUNT = 6
DEFAULT_MAX_PAGES = 4
DEFAULT_MAX_KILLMAILS = 4

CARGO_FLAG = 5
DRONE_BAY_FLAG = 87
FIGHTER_BAY_FLAG = 158
FIGHTER_TUBE_FLAGS = {159, 160, 161, 162, 163}
ALLOWED_NON_SLOT_FLAGS = {CARGO_FLAG, DRONE_BAY_FLAG, FIGHTER_BAY_FLAG}
FITTING_FLAG_RANGES = ((11, 34), (92, 99), (125, 132))

ZKILLBOARD_BASE = "https://zkillboard.com"
ESI_BASE = "https://esi.evetech.net/latest"
FALLBACK_DONORS_BY_SHIP_NAME = {
    "Anhinga": ["Naga", "Tornado", "Oracle", "Talos"],
    "Apocalypse Imperial Issue": ["Apocalypse Navy Issue", "Apocalypse"],
    "Armageddon Imperial Issue": ["Armageddon Navy Issue", "Armageddon"],
    "Megathron Federate Issue": ["Megathron Navy Issue", "Megathron"],
    "Raven State Issue": ["Raven Navy Issue", "Raven"],
    "Tempest Tribal Issue": ["Tempest Fleet Issue", "Tempest"],
    "Capsule": ["Caldari Shuttle", "InterBus Shuttle"],
    "Capsule - Genolution 'Auroral' 197-variant": ["Caldari Shuttle", "InterBus Shuttle"],
    "Guardian-Vexor": ["Vexor", "Vexor Navy Issue"],
    "Stratios Emergency Responder": ["Stratios"],
    "Boobook": ["Caldari Shuttle", "InterBus Shuttle"],
    "Opux Luxury Yacht": ["Victorieux Luxury Yacht"],
}
EventCallback = Callable[[dict[str, Any]], None]
LogCallback = Callable[[str], None]


def _expand_path(value: str) -> Path:
    return Path(value.strip().strip('"')).expanduser().resolve()


def looks_like_evejs_root(path: Path) -> bool:
    return (
        (path / "server" / "src" / "newDatabase" / "data" / "characters" / "data.json").exists()
        and (path / "server" / "src" / "newDatabase" / "data" / "savedFittings").exists()
    )


def _configured_evejs_candidates() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("EVEJS_REPO_ROOT", "EVEJS_PATH"):
        env_value = str(os.environ.get(env_name) or "").strip()
        if env_value:
            candidates.append(_expand_path(env_value))

    if EVEJS_PATH_FILE.exists():
        configured = EVEJS_PATH_FILE.read_text(encoding="utf-8").strip()
        if configured:
            candidates.append(_expand_path(configured))

    try:
        candidates.append(Path.cwd().resolve())
    except OSError:
        pass

    candidates.append(TOOL_ROOT.parent.parent.resolve())

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            deduped.append(candidate)
            seen.add(key)
    return deduped


def resolve_evejs_root() -> Path:
    candidates = _configured_evejs_candidates()
    for candidate in candidates:
        if looks_like_evejs_root(candidate):
            return candidate
    return candidates[0]


def configure_evejs_root(path: Path, *, persist: bool = True) -> Path:
    root = Path(path).expanduser().resolve()
    if persist:
        ensure_dir(CONFIG_ROOT)
        EVEJS_PATH_FILE.write_text(str(root), encoding="utf-8", newline="\n")
    set_evejs_root(root)
    return root


def set_evejs_root(path: Path) -> None:
    global REPO_ROOT, CLIENT_SDE_EXPORTS_ROOT, CHARACTERS_DATA_PATH, SAVED_FITTINGS_DATA_PATH
    REPO_ROOT = Path(path).expanduser().resolve()
    CLIENT_SDE_EXPORTS_ROOT = REPO_ROOT / "tools" / "ClientSDE" / "exports"
    CHARACTERS_DATA_PATH = REPO_ROOT / "server" / "src" / "newDatabase" / "data" / "characters" / "data.json"
    SAVED_FITTINGS_DATA_PATH = (
        REPO_ROOT / "server" / "src" / "newDatabase" / "data" / "savedFittings" / "data.json"
    )


def ensure_evejs_runtime_ready(*, require_sde: bool = False) -> None:
    if not looks_like_evejs_root(REPO_ROOT):
        raise FileNotFoundError(
            "EVE JS folder is not configured. Run Install.bat / Install.sh, or set EVEJS_REPO_ROOT "
            f"to your EVE JS checkout. Current path: {REPO_ROOT}"
        )
    if not CHARACTERS_DATA_PATH.exists():
        raise FileNotFoundError(f"characters/data.json was not found: {CHARACTERS_DATA_PATH}")
    ensure_dir(SAVED_FITTINGS_DATA_PATH.parent)
    if require_sde:
        find_latest_sde_root()


set_evejs_root(resolve_evejs_root())


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def emit_event(event_callback: EventCallback | None, payload: dict[str, Any]) -> None:
    if event_callback:
        event_callback({
            "timestamp": now_iso(),
            **payload,
        })


def emit_log(log_callback: LogCallback | None, message: str) -> None:
    if log_callback:
        log_callback(message)


def current_filetime_str() -> str:
    return str(int(time.time() * 10_000_000) + 116444736000000000)


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, payload: Any) -> None:
    ensure_dir(path.parent)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    temp_path.replace(path)


def write_text(path: Path, payload: str) -> None:
    ensure_dir(path.parent)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(payload, encoding="utf-8", newline="\n")
    temp_path.replace(path)


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized or "unnamed"


def to_int(value: Any, fallback: int = 0) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return fallback
    return numeric


def is_ship_fitting_flag(flag_id: int) -> bool:
    return any(lo <= flag_id <= hi for lo, hi in FITTING_FLAG_RANGES)


def get_quantity(item: dict[str, Any]) -> int:
    quantity = to_int(item.get("quantity_dropped"), 0) + to_int(item.get("quantity_destroyed"), 0)
    if quantity > 0:
        return quantity
    if to_int(item.get("singleton"), 0) >= 0 and to_int(item.get("item_type_id"), 0) > 0:
        return 1
    return 0


def fitting_name_for_ship(ship_name: str) -> str:
    name = f"{TOOL_NAME_PREFIX}{ship_name}".strip()
    return name[:50]


def description_for_candidate(candidate: "FitCandidate") -> str:
    source = candidate.source
    parts = [
        TOOL_DESCRIPTION_PREFIX,
        f"ship={candidate.ship_name}",
        f"group={candidate.group_name}",
        f"provider={source.get('provider', 'zkillboard+esi')}",
        f"killmail={source.get('killmail_id', 0)}",
        f"zkill={source.get('zkill_url', '')}",
        f"score={candidate.score}",
        f"modules={candidate.module_count}",
    ]
    return " ".join(part for part in parts if part).strip()[:500]


def flag_to_slot(flag_id: int) -> str | None:
    if 27 <= flag_id <= 34:
        return f"hi slot {flag_id - 27}"
    if 19 <= flag_id <= 26:
        return f"med slot {flag_id - 19}"
    if 11 <= flag_id <= 18:
        return f"low slot {flag_id - 11}"
    if 92 <= flag_id <= 99:
        return f"rig slot {flag_id - 92}"
    if 125 <= flag_id <= 132:
        return f"subsystem slot {flag_id - 125}"
    if flag_id == CARGO_FLAG:
        return "cargo"
    if flag_id == DRONE_BAY_FLAG:
        return "drone bay"
    if flag_id == FIGHTER_BAY_FLAG:
        return "fighter bay"
    return None


def prettify_xml(element: Element) -> str:
    rough = tostring(element, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="\t")
    lines = [line for line in pretty.splitlines() if line.strip()]
    return "\n".join(lines) + "\n"


class NetworkLimiter:
    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._min_interval_seconds - (now - self._last_request_at)
            if delay > 0:
                time.sleep(delay)
            self._last_request_at = time.monotonic()


NETWORK_LIMITER = NetworkLimiter(NETWORK_MIN_INTERVAL_SECONDS)


@dataclass(slots=True)
class ShipHull:
    type_id: int
    name: str
    group_id: int
    group_name: str


@dataclass(slots=True)
class FitCandidate:
    ship_type_id: int
    ship_name: str
    group_name: str
    fit_data: list[list[int]]
    source: dict[str, Any]
    module_count: int
    aux_count: int
    score: int


@dataclass(slots=True)
class BuildContext:
    types_by_id: dict[int, dict[str, Any]]
    hulls: list[ShipHull]
    max_pages: int
    max_killmails: int
    cache_root: Path


def find_latest_sde_root() -> Path:
    if not CLIENT_SDE_EXPORTS_ROOT.exists():
        raise FileNotFoundError(f"ClientSDE exports root not found: {CLIENT_SDE_EXPORTS_ROOT}")

    candidates: list[Path] = []
    for candidate in CLIENT_SDE_EXPORTS_ROOT.glob("*/eve-online-static-data-*-jsonl"):
        if (candidate / "types.jsonl").exists() and (candidate / "groups.jsonl").exists():
            candidates.append(candidate)

    if not candidates:
        raise FileNotFoundError(
            "No ClientSDE jsonl bundle was found under tools/ClientSDE/exports."
        )

    return max(candidates, key=lambda path: path.stat().st_mtime)


def load_reference_data(sde_root: Path) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], list[ShipHull]]:
    types_by_id: dict[int, dict[str, Any]] = {}
    groups_by_id: dict[int, dict[str, Any]] = {}

    with (sde_root / "groups.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            group_id = to_int(row.get("_key") or row.get("groupID"), 0)
            if group_id > 0:
                groups_by_id[group_id] = row

    with (sde_root / "types.jsonl").open("r", encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            type_id = to_int(row.get("_key") or row.get("typeID"), 0)
            if type_id > 0:
                types_by_id[type_id] = row

    hulls: list[ShipHull] = []
    for type_id, row in types_by_id.items():
        group_id = to_int(row.get("groupID"), 0)
        group = groups_by_id.get(group_id) or {}
        if not bool(row.get("published")):
            continue
        if to_int(group.get("categoryID"), 0) != 6:
            continue

        name = ((row.get("name") or {}).get("en") or "").strip()
        group_name = ((group.get("name") or {}).get("en") or "").strip()
        if not name:
            continue
        hulls.append(
            ShipHull(
                type_id=type_id,
                name=name,
                group_id=group_id,
                group_name=group_name or "Ship",
            )
        )

    hulls.sort(key=lambda item: (item.group_name.lower(), item.name.lower(), item.type_id))
    return types_by_id, groups_by_id, hulls


def cache_read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError:
        return None


def fetch_json(url: str, cache_path: Path, timeout_seconds: int = 30) -> Any:
    cached = cache_read_json(cache_path)
    if cached is not None:
        return cached

    ensure_dir(cache_path.parent)
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            NETWORK_LIMITER.wait()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
            write_json(cache_path, payload)
            return payload
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            status = getattr(exc, "code", None)
            if status and status not in {420, 429, 500, 502, 503, 504}:
                break
            time.sleep(min(8.0, 0.75 * (attempt + 1)))

    raise RuntimeError(f"Failed to fetch JSON from {url}: {last_error}")


def zkill_losses_cache_path(cache_root: Path, ship_type_id: int, page: int) -> Path:
    return cache_root / "zkillboard" / f"losses-ship-{ship_type_id}-page-{page}.json"


def killmail_cache_path(cache_root: Path, killmail_id: int) -> Path:
    return cache_root / "esi" / f"killmail-{killmail_id}.json"


def fetch_zkill_losses(cache_root: Path, ship_type_id: int, page: int) -> list[dict[str, Any]]:
    url = f"{ZKILLBOARD_BASE}/api/losses/shipTypeID/{ship_type_id}/page/{page}/"
    payload = fetch_json(url, zkill_losses_cache_path(cache_root, ship_type_id, page))
    return payload if isinstance(payload, list) else []


def fetch_esi_killmail(cache_root: Path, killmail_id: int, killmail_hash: str) -> dict[str, Any]:
    url = f"{ESI_BASE}/killmails/{killmail_id}/{killmail_hash}/?datasource=tranquility"
    payload = fetch_json(url, killmail_cache_path(cache_root, killmail_id))
    return payload if isinstance(payload, dict) else {}


def normalize_fit_data(raw_fit_data: list[list[int]], types_by_id: dict[int, dict[str, Any]]) -> list[list[int]]:
    merged: dict[tuple[int, int], int] = {}
    slotted: list[list[int]] = []

    for type_id, flag_id, quantity in raw_fit_data:
        numeric_type_id = to_int(type_id, 0)
        numeric_flag_id = to_int(flag_id, 0)
        numeric_quantity = to_int(quantity, 0)
        if numeric_type_id <= 0 or numeric_flag_id <= 0 or numeric_quantity <= 0:
            continue
        if numeric_type_id not in types_by_id:
            continue

        if is_ship_fitting_flag(numeric_flag_id):
            slotted.append([numeric_type_id, numeric_flag_id, 1])
            continue

        if numeric_flag_id not in ALLOWED_NON_SLOT_FLAGS:
            continue

        key = (numeric_type_id, numeric_flag_id)
        merged[key] = merged.get(key, 0) + numeric_quantity

    slotted.sort(key=lambda item: (item[1], item[0], item[2]))
    merged_items = [[type_id, flag_id, quantity] for (type_id, flag_id), quantity in sorted(merged.items())]
    return slotted + merged_items


def extract_fit_data_from_items(items: list[dict[str, Any]], types_by_id: dict[int, dict[str, Any]]) -> list[list[int]]:
    raw_fit_data: list[list[int]] = []

    def walk(item: dict[str, Any], parent_flag: int | None = None) -> None:
        if not isinstance(item, dict):
            return

        flag_id = to_int(item.get("flag"), 0)
        type_id = to_int(item.get("item_type_id"), 0)
        quantity = get_quantity(item)
        nested_items = item.get("items") if isinstance(item.get("items"), list) else []

        if parent_flag is not None:
            raw_fit_data.append([type_id, parent_flag, quantity])
        elif is_ship_fitting_flag(flag_id):
            raw_fit_data.append([type_id, flag_id, 1])
            for nested in nested_items:
                walk(nested, CARGO_FLAG)
            return
        elif flag_id in ALLOWED_NON_SLOT_FLAGS:
            raw_fit_data.append([type_id, flag_id, quantity])
            for nested in nested_items:
                walk(nested, flag_id)
            return
        elif flag_id in FIGHTER_TUBE_FLAGS:
            raw_fit_data.append([type_id, FIGHTER_BAY_FLAG, quantity])
            for nested in nested_items:
                walk(nested, FIGHTER_BAY_FLAG)
            return
        else:
            for nested in nested_items:
                walk(nested, CARGO_FLAG)
            return

    for entry in items or []:
        walk(entry)

    return normalize_fit_data(raw_fit_data, types_by_id)


def score_fit_data(fit_data: list[list[int]]) -> tuple[int, int, int]:
    module_count = sum(1 for _, flag_id, _ in fit_data if is_ship_fitting_flag(flag_id))
    aux_count = len(fit_data) - module_count
    score = module_count * 100 + aux_count * 10 + len({type_id for type_id, _, _ in fit_data})
    return module_count, aux_count, score


def candidate_from_killmail(
    ship: ShipHull,
    killmail: dict[str, Any],
    summary: dict[str, Any],
    types_by_id: dict[int, dict[str, Any]],
) -> FitCandidate | None:
    victim = killmail.get("victim") if isinstance(killmail.get("victim"), dict) else {}
    if to_int(victim.get("ship_type_id"), 0) != ship.type_id:
        return None

    fit_data = extract_fit_data_from_items(victim.get("items") or [], types_by_id)
    if not fit_data:
        return None

    module_count, aux_count, score = score_fit_data(fit_data)
    source = {
        "provider": "zkillboard+esi",
        "killmail_id": to_int(summary.get("killmail_id"), 0),
        "killmail_hash": str((summary.get("zkb") or {}).get("hash") or ""),
        "zkill_url": f"{ZKILLBOARD_BASE}/kill/{to_int(summary.get('killmail_id'), 0)}/",
        "esi_url": (
            f"{ESI_BASE}/killmails/"
            f"{to_int(summary.get('killmail_id'), 0)}/"
            f"{str((summary.get('zkb') or {}).get('hash') or '')}/?datasource=tranquility"
        ),
        "fitted_value": float((summary.get("zkb") or {}).get("fittedValue") or 0),
    }
    return FitCandidate(
        ship_type_id=ship.type_id,
        ship_name=ship.name,
        group_name=ship.group_name,
        fit_data=fit_data,
        source=source,
        module_count=module_count,
        aux_count=aux_count,
        score=score,
    )


def pick_ranked_summaries(summaries: list[dict[str, Any]], max_killmails: int) -> list[dict[str, Any]]:
    ranked = sorted(
        summaries,
        key=lambda entry: (
            -float((entry.get("zkb") or {}).get("fittedValue") or 0),
            -float((entry.get("zkb") or {}).get("totalValue") or 0),
            to_int(entry.get("killmail_id"), 0),
        ),
    )
    return ranked[: max(1, max_killmails)]


def clone_fit_data(fit_data: list[list[int]]) -> list[list[int]]:
    return [
        [to_int(item[0], 0), to_int(item[1], 0), to_int(item[2], 0)]
        for item in fit_data or []
        if isinstance(item, list) and len(item) >= 3
    ]


def build_fallback_record(ship: ShipHull, donor_record: dict[str, Any]) -> dict[str, Any]:
    fit_data = clone_fit_data(list(donor_record.get("fitData") or []))
    module_count, aux_count, score = score_fit_data(fit_data)
    donor_source = donor_record.get("source") if isinstance(donor_record.get("source"), dict) else {}
    source = {
        "provider": "fallback-donor",
        "donorShipName": str(donor_record.get("shipName") or ""),
        "donorShipTypeID": to_int(donor_record.get("shipTypeID"), 0),
        "killmail_id": to_int(donor_source.get("killmail_id"), 0),
        "killmail_hash": str(donor_source.get("killmail_hash") or ""),
        "zkill_url": str(donor_source.get("zkill_url") or ""),
        "esi_url": str(donor_source.get("esi_url") or ""),
        "fallbackReason": "No valid public fitting could be harvested from zKillboard/ESI.",
    }
    description = (
        f"{TOOL_DESCRIPTION_PREFIX} ship={ship.name} group={ship.group_name} "
        f"provider=fallback-donor donor={source['donorShipName']} "
        f"killmail={source['killmail_id']} zkill={source['zkill_url']} "
        f"score={score} modules={module_count}"
    )[:500]
    return {
        "shipTypeID": ship.type_id,
        "shipName": ship.name,
        "groupID": ship.group_id,
        "groupName": ship.group_name,
        "status": "ok",
        "fallback": True,
        "name": fitting_name_for_ship(ship.name),
        "description": description,
        "fitData": fit_data,
        "score": score,
        "moduleCount": module_count,
        "auxCount": aux_count,
        "source": source,
    }


def harvest_ship(ship: ShipHull, context: BuildContext) -> dict[str, Any]:
    best_candidate: FitCandidate | None = None
    seen_killmail_ids: set[int] = set()

    for page in range(1, context.max_pages + 1):
        summaries = fetch_zkill_losses(context.cache_root, ship.type_id, page)
        if not summaries:
            break

        for summary in pick_ranked_summaries(summaries, context.max_killmails):
            killmail_id = to_int(summary.get("killmail_id"), 0)
            killmail_hash = str((summary.get("zkb") or {}).get("hash") or "")
            if killmail_id <= 0 or not killmail_hash or killmail_id in seen_killmail_ids:
                continue

            seen_killmail_ids.add(killmail_id)
            try:
                killmail = fetch_esi_killmail(context.cache_root, killmail_id, killmail_hash)
            except Exception as exc:  # noqa: BLE001
                if best_candidate is None:
                    error = str(exc)
                else:
                    error = ""
                continue

            candidate = candidate_from_killmail(ship, killmail, summary, context.types_by_id)
            if not candidate:
                continue

            if best_candidate is None or candidate.score > best_candidate.score:
                best_candidate = candidate

            if candidate.module_count >= 3 or candidate.score >= 220:
                break

        if best_candidate and (best_candidate.module_count >= 3 or best_candidate.score >= 220):
            break

    if not best_candidate:
        return {
            "shipTypeID": ship.type_id,
            "shipName": ship.name,
            "groupID": ship.group_id,
            "groupName": ship.group_name,
            "status": "missing",
            "reason": "No valid public fitting could be harvested from zKillboard/ESI.",
        }

    record = {
        "shipTypeID": best_candidate.ship_type_id,
        "shipName": best_candidate.ship_name,
        "groupID": ship.group_id,
        "groupName": best_candidate.group_name,
        "status": "ok",
        "name": fitting_name_for_ship(best_candidate.ship_name),
        "description": description_for_candidate(best_candidate),
        "fitData": best_candidate.fit_data,
        "score": best_candidate.score,
        "moduleCount": best_candidate.module_count,
        "auxCount": best_candidate.aux_count,
        "source": best_candidate.source,
    }
    return record


def load_existing_library_records() -> list[dict[str, Any]]:
    library = load_library_payload(required=False)
    if not isinstance(library, dict):
        return []
    records = library.get("records")
    return list(records) if isinstance(records, list) else []


def load_library_payload(*, required: bool = True) -> dict[str, Any]:
    for path in (LIBRARY_JSON_PATH, BUNDLED_LIBRARY_JSON_PATH):
        library = read_json(path, {})
        if isinstance(library, dict) and isinstance(library.get("records"), list):
            return library
    if required:
        raise RuntimeError(
            "No FitALL fitting library was found. Re-run the installer or use build-library "
            "from an EVE JS checkout with ClientSDE exports."
        )
    return {}


def index_records_by_type_and_name(
    records: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_type_id: dict[int, dict[str, Any]] = {}
    by_name: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        ship_type_id = to_int(record.get("shipTypeID"), 0)
        ship_name = str(record.get("shipName") or "").strip()
        if ship_type_id > 0:
            by_type_id[ship_type_id] = record
        if ship_name:
            by_name[ship_name] = record
    return by_type_id, by_name


def select_hulls_for_build(
    hulls: list[ShipHull],
    *,
    selected_ship_names: list[str],
    only_missing: bool,
    existing_records: list[dict[str, Any]],
) -> list[ShipHull]:
    if selected_ship_names:
        wanted = {name.strip().lower() for name in selected_ship_names if name and name.strip()}
        return [ship for ship in hulls if ship.name.lower() in wanted]

    if not only_missing:
        return hulls

    missing_ids = {
        to_int(record.get("shipTypeID"), 0)
        for record in existing_records
        if isinstance(record, dict) and record.get("status") != "ok"
    }
    if not missing_ids:
        return []
    return [ship for ship in hulls if ship.type_id in missing_ids]


def merge_records_for_all_hulls(
    hulls: list[ShipHull],
    fresh_records: list[dict[str, Any]],
    existing_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fresh_by_type_id, _fresh_by_name = index_records_by_type_and_name(fresh_records)
    existing_by_type_id, _existing_by_name = index_records_by_type_and_name(existing_records)
    merged: list[dict[str, Any]] = []
    for ship in hulls:
        record = fresh_by_type_id.get(ship.type_id) or existing_by_type_id.get(ship.type_id)
        if record:
            record.pop("xmlPath", None)
            merged.append(record)
            continue
        merged.append(
            {
                "shipTypeID": ship.type_id,
                "shipName": ship.name,
                "groupID": ship.group_id,
                "groupName": ship.group_name,
                "status": "missing",
                "reason": "No fitting record has been generated yet.",
            }
        )
    return merged


def apply_missing_record_fallbacks(
    records: list[dict[str, Any]],
    hulls_by_type_id: dict[int, ShipHull],
    donor_by_name: dict[str, dict[str, Any]],
    *,
    event_callback: EventCallback | None = None,
    log_callback: LogCallback | None = None,
) -> list[dict[str, Any]]:
    updated_records: list[dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict) or record.get("status") == "ok":
            updated_records.append(record)
            continue

        ship_name = str(record.get("shipName") or "").strip()
        donor_names = FALLBACK_DONORS_BY_SHIP_NAME.get(ship_name) or []
        fallback_record = None
        for donor_name in donor_names:
            donor_record = donor_by_name.get(donor_name)
            if donor_record and donor_record.get("status") == "ok":
                ship = hulls_by_type_id.get(to_int(record.get("shipTypeID"), 0))
                if ship:
                    fallback_record = build_fallback_record(ship, donor_record)
                    emit_log(log_callback, f"[FBK] {ship.group_name} :: {ship.name} <- {donor_name}")
                    emit_event(
                        event_callback,
                        {
                            "kind": "build-fallback",
                            "shipName": ship.name,
                            "groupName": ship.group_name,
                            "donorShipName": donor_name,
                            "shipTypeID": ship.type_id,
                        },
                    )
                    break

        updated_records.append(fallback_record or record)
    return updated_records


def build_fitting_xml(record: dict[str, Any], types_by_id: dict[int, dict[str, Any]]) -> str:
    root = Element("fittings")
    fitting_element = SubElement(root, "fitting")
    fitting_element.set("name", str(record.get("name") or record.get("shipName") or "FitALL"))

    description_element = SubElement(fitting_element, "description")
    description_element.set("value", str(record.get("description") or ""))

    ship_element = SubElement(fitting_element, "shipType")
    ship_element.set("value", str(record.get("shipName") or "Unknown Ship"))

    fit_data = list(record.get("fitData") or [])
    fit_data.sort(key=lambda item: (to_int(item[1], 0), to_int(item[0], 0), to_int(item[2], 0)))
    for type_id, flag_id, quantity in fit_data:
        slot = flag_to_slot(to_int(flag_id, 0))
        type_name = ((types_by_id.get(to_int(type_id, 0)) or {}).get("name") or {}).get("en") or str(type_id)
        if not slot:
            continue
        hardware = SubElement(fitting_element, "hardware")
        hardware.set("slot", slot)
        hardware.set("type", type_name)
        if slot in {"cargo", "drone bay", "fighter bay"}:
            hardware.set("qty", str(max(1, to_int(quantity, 1))))

    return prettify_xml(root)


def build_combined_xml(records: list[dict[str, Any]], types_by_id: dict[int, dict[str, Any]]) -> str:
    root = Element("fittings")
    for record in records:
        fitting_element = SubElement(root, "fitting")
        fitting_element.set("name", str(record.get("name") or record.get("shipName") or "FitALL"))

        description_element = SubElement(fitting_element, "description")
        description_element.set("value", str(record.get("description") or ""))

        ship_element = SubElement(fitting_element, "shipType")
        ship_element.set("value", str(record.get("shipName") or "Unknown Ship"))

        fit_data = list(record.get("fitData") or [])
        fit_data.sort(key=lambda item: (to_int(item[1], 0), to_int(item[0], 0), to_int(item[2], 0)))
        for type_id, flag_id, quantity in fit_data:
            slot = flag_to_slot(to_int(flag_id, 0))
            type_name = ((types_by_id.get(to_int(type_id, 0)) or {}).get("name") or {}).get("en") or str(type_id)
            if not slot:
                continue
            hardware = SubElement(fitting_element, "hardware")
            hardware.set("slot", slot)
            hardware.set("type", type_name)
            if slot in {"cargo", "drone bay", "fighter bay"}:
                hardware.set("qty", str(max(1, to_int(quantity, 1))))

    return prettify_xml(root)


def build_checklist(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(str(record.get("groupName") or "Ship"), []).append(record)

    checklist_groups: list[dict[str, Any]] = []
    for group_name in sorted(grouped.keys(), key=str.lower):
        entries = sorted(grouped[group_name], key=lambda item: str(item.get("shipName") or "").lower())
        ok_entries = [entry for entry in entries if entry.get("status") == "ok"]
        missing_entries = [entry for entry in entries if entry.get("status") != "ok"]
        checklist_groups.append(
            {
                "groupName": group_name,
                "total": len(entries),
                "ok": len(ok_entries),
                "missing": len(missing_entries),
                "ships": entries,
            }
        )

    summary = {
        "generatedAt": now_iso(),
        "totalShips": len(records),
        "harvestedShips": sum(1 for record in records if record.get("status") == "ok"),
        "missingShips": sum(1 for record in records if record.get("status") != "ok"),
        "groups": checklist_groups,
    }
    return summary


def checklist_markdown(summary: dict[str, Any]) -> str:
    lines = [
        "# FitALL Checklist",
        "",
        f"- Generated: `{summary.get('generatedAt', '')}`",
        f"- Total ships: `{summary.get('totalShips', 0)}`",
        f"- Harvested: `{summary.get('harvestedShips', 0)}`",
        f"- Missing: `{summary.get('missingShips', 0)}`",
        "",
    ]

    for group in summary.get("groups", []):
        lines.append(f"## {group.get('groupName', 'Ship')}")
        lines.append("")
        lines.append(
            f"- Harvested: `{group.get('ok', 0)}/{group.get('total', 0)}`"
        )
        lines.append("")
        for ship in group.get("ships", []):
            if ship.get("status") == "ok":
                source = ship.get("source") or {}
                lines.append(
                    f"- [x] `{ship.get('shipName', '')}`"
                    f" via killmail `{source.get('killmail_id', 0)}`"
                    f" score `{ship.get('score', 0)}`"
                )
            else:
                lines.append(
                    f"- [ ] `{ship.get('shipName', '')}`"
                    f" ({ship.get('reason', 'missing source')})"
                )
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def load_characters() -> dict[str, Any]:
    payload = read_json(CHARACTERS_DATA_PATH, {})
    return payload if isinstance(payload, dict) else {}


def load_saved_fittings_root() -> dict[str, Any]:
    payload = read_json(SAVED_FITTINGS_DATA_PATH, {})
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("_meta", {})
    payload["_meta"].setdefault("version", 1)
    payload["_meta"].setdefault("nextFittingID", 1)
    payload.setdefault("owners", {})
    return payload


def backup_saved_fittings() -> Path:
    ensure_dir(BACKUPS_OUTPUT_ROOT)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUPS_OUTPUT_ROOT / f"savedFittings-{timestamp}.json"
    if SAVED_FITTINGS_DATA_PATH.exists():
        backup_text = SAVED_FITTINGS_DATA_PATH.read_text(encoding="utf-8")
    else:
        backup_text = "{}\n"
    backup_path.write_text(backup_text, encoding="utf-8")
    return backup_path


def is_tool_managed_fitting(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    name = str(record.get("name") or "")
    description = str(record.get("description") or "")
    return name.startswith(TOOL_NAME_PREFIX) or description.startswith(TOOL_DESCRIPTION_PREFIX)


def collect_next_fitting_id(root: dict[str, Any]) -> int:
    next_id = max(1, to_int(root.get("_meta", {}).get("nextFittingID"), 1))
    owners = root.get("owners") if isinstance(root.get("owners"), dict) else {}
    for owner_record in owners.values():
        fittings = owner_record.get("fittings") if isinstance(owner_record, dict) else {}
        if not isinstance(fittings, dict):
            continue
        for fitting_id in fittings.keys():
            next_id = max(next_id, to_int(fitting_id, 0) + 1)
    return next_id


def seed_saved_fittings(
    records: list[dict[str, Any]],
    dry_run: bool = False,
    *,
    event_callback: EventCallback | None = None,
) -> dict[str, Any]:
    ok_records = [record for record in records if record.get("status") == "ok"]
    if len(ok_records) > MAX_CHAR_FITTINGS:
        raise RuntimeError(
            f"Cannot seed {len(ok_records)} ship fits into character saved fittings because the "
            f"client/server character limit is {MAX_CHAR_FITTINGS}."
        )

    characters = load_characters()
    root = load_saved_fittings_root()
    owners = root["owners"] if isinstance(root.get("owners"), dict) else {}
    root["owners"] = owners

    next_fitting_id = collect_next_fitting_id(root)
    saved_date = current_filetime_str()
    fit_templates = [
        (
            to_int(record.get("shipTypeID"), 0),
            str(record.get("name") or ""),
            str(record.get("description") or ""),
            record.get("fitData") or [],
        )
        for record in ok_records
    ]
    tool_records_removed = 0
    tool_records_added = 0
    characters_seeded = 0
    skipped_characters: list[dict[str, Any]] = []
    candidate_characters: list[tuple[int, str, Any]] = []
    for character_id_text, character_record in characters.items():
        character_id = to_int(character_id_text, 0)
        if character_id > 0:
            candidate_characters.append((character_id, character_id_text, character_record))
    candidate_characters.sort(key=lambda item: item[0])
    total_characters = len(candidate_characters)

    emit_event(
        event_callback,
        {
            "kind": "seed-start",
            "totalCharacters": total_characters,
            "fitsPerCharacter": len(ok_records),
            "dryRun": bool(dry_run),
        },
    )

    processed_characters = 0
    for character_id, character_id_text, character_record in candidate_characters:
        owner_record = owners.get(character_id_text)
        if not isinstance(owner_record, dict):
            owner_record = {
                "ownerID": character_id,
                "scope": "character",
                "fittings": {},
            }
            owners[character_id_text] = owner_record

        owner_record["ownerID"] = character_id
        owner_record["scope"] = "character"
        fittings = owner_record.get("fittings")
        if not isinstance(fittings, dict):
            fittings = {}
            owner_record["fittings"] = fittings

        non_tool_fittings = {
            key: value
            for key, value in fittings.items()
            if not is_tool_managed_fitting(value)
        }
        existing_non_tool_count = len(non_tool_fittings)
        if existing_non_tool_count + len(ok_records) > MAX_CHAR_FITTINGS:
            skipped_entry = {
                "characterID": character_id,
                "name": str((character_record or {}).get("characterName") or ""),
                "existingNonToolFittings": existing_non_tool_count,
                "requiredFitCount": len(ok_records),
                "limit": MAX_CHAR_FITTINGS,
            }
            skipped_characters.append(skipped_entry)
            processed_characters += 1
            emit_event(
                event_callback,
                {
                    "kind": "seed-progress",
                    "current": processed_characters,
                    "total": total_characters,
                    "characterID": character_id,
                    "characterName": skipped_entry["name"],
                    "status": "skipped",
                    "existingNonToolFittings": existing_non_tool_count,
                },
            )
            continue

        removed_for_character = len(fittings) - len(non_tool_fittings)
        tool_records_removed += removed_for_character
        owner_record["fittings"] = dict(non_tool_fittings)
        fittings = owner_record["fittings"]

        for ship_type_id, name, description, fit_data in fit_templates:
            stored = {
                "fittingID": next_fitting_id,
                "ownerID": character_id,
                "shipTypeID": ship_type_id,
                "name": name,
                "description": description,
                "fitData": fit_data,
                "savedDate": saved_date,
            }
            fittings[str(next_fitting_id)] = stored
            next_fitting_id += 1
            tool_records_added += 1

        characters_seeded += 1
        processed_characters += 1
        emit_event(
            event_callback,
            {
                "kind": "seed-progress",
                "current": processed_characters,
                "total": total_characters,
                "characterID": character_id,
                "characterName": str((character_record or {}).get("characterName") or ""),
                "status": "seeded",
                "fittingsAdded": len(ok_records),
                "replacedFitALL": removed_for_character,
            },
        )

    root["_meta"]["nextFittingID"] = next_fitting_id
    summary = {
        "generatedAt": now_iso(),
        "toolLibraryFits": len(ok_records),
        "charactersSeeded": characters_seeded,
        "toolRecordsAdded": tool_records_added,
        "toolRecordsRemoved": tool_records_removed,
        "skippedCharacters": skipped_characters,
        "savedFittingsPath": str(SAVED_FITTINGS_DATA_PATH),
    }

    if not dry_run:
        backup_path = backup_saved_fittings()
        write_json(SAVED_FITTINGS_DATA_PATH, root)
        summary["backupPath"] = str(backup_path)

    emit_event(
        event_callback,
        {
            "kind": "seed-complete",
            "summary": summary,
        },
    )
    return summary


def build_library(
    *,
    sde_root: Path,
    cache_root: Path,
    max_pages: int,
    max_killmails: int,
    threads: int,
    limit_hulls: int | None = None,
    only_missing: bool = False,
    selected_ship_names: list[str] | None = None,
    event_callback: EventCallback | None = None,
    log_callback: LogCallback | None = print,
) -> dict[str, Any]:
    types_by_id, _groups_by_id, hulls = load_reference_data(sde_root)
    if limit_hulls and limit_hulls > 0:
        hulls = hulls[:limit_hulls]

    existing_records = load_existing_library_records()
    selected_hulls = select_hulls_for_build(
        hulls,
        selected_ship_names=list(selected_ship_names or []),
        only_missing=only_missing,
        existing_records=existing_records,
    )

    context = BuildContext(
        types_by_id=types_by_id,
        hulls=selected_hulls,
        max_pages=max_pages,
        max_killmails=max_killmails,
        cache_root=cache_root,
    )

    ensure_dir(FITTINGS_OUTPUT_ROOT)
    ensure_dir(REPORTS_OUTPUT_ROOT)
    ensure_dir(PER_SHIP_XML_ROOT)
    ensure_dir(cache_root)

    total = len(selected_hulls)
    emit_event(
        event_callback,
        {
            "kind": "build-start",
            "total": total,
            "shipNames": [ship.name for ship in selected_hulls[:24]],
            "onlyMissing": bool(only_missing),
            "selectedShipNames": list(selected_ship_names or []),
            "threads": max(1, threads),
            "maxPages": max_pages,
            "maxKillmails": max_killmails,
        },
    )
    if total <= 0:
        records = merge_records_for_all_hulls(hulls, [], existing_records)
    else:
        emit_log(log_callback, f"[FitALL] Building {total} selected hull(s)")
        records = []

        with ThreadPoolExecutor(max_workers=max(1, threads)) as executor:
            futures = {executor.submit(harvest_ship, ship, context): ship for ship in selected_hulls}
            completed = 0
            for future in as_completed(futures):
                ship = futures[future]
                completed += 1
                try:
                    record = future.result()
                except Exception as exc:  # noqa: BLE001
                    record = {
                        "shipTypeID": ship.type_id,
                        "shipName": ship.name,
                        "groupID": ship.group_id,
                        "groupName": ship.group_name,
                        "status": "missing",
                        "reason": str(exc),
                    }

                records.append(record)
                status = "OK" if record.get("status") == "ok" else "MISS"
                emit_log(log_callback, f"[{completed:03d}/{total:03d}] {status} {ship.group_name} :: {ship.name}")
                emit_event(
                    event_callback,
                    {
                        "kind": "build-progress",
                        "current": completed,
                        "total": total,
                        "shipTypeID": ship.type_id,
                        "shipName": ship.name,
                        "groupName": ship.group_name,
                        "status": record.get("status"),
                        "provider": ((record.get("source") or {}).get("provider") if isinstance(record, dict) else None),
                        "score": to_int(record.get("score"), 0) if isinstance(record, dict) else 0,
                        "reason": (record.get("reason") if isinstance(record, dict) else None),
                    },
                )

        _, existing_by_name = index_records_by_type_and_name(existing_records)
        _, fresh_by_name = index_records_by_type_and_name(records)
        donor_by_name = dict(existing_by_name)
        donor_by_name.update({name: record for name, record in fresh_by_name.items() if record.get("status") == "ok"})
        hulls_by_type_id = {ship.type_id: ship for ship in hulls}
        records = apply_missing_record_fallbacks(
            records,
            hulls_by_type_id,
            donor_by_name,
            event_callback=event_callback,
            log_callback=log_callback,
        )
        records = merge_records_for_all_hulls(hulls, records, existing_records)

    records.sort(key=lambda item: (str(item.get("groupName") or "").lower(), str(item.get("shipName") or "").lower()))

    ok_records = [record for record in records if record.get("status") == "ok"]
    library = {
        "generatedAt": now_iso(),
        "tool": "FitALL",
        "sourceBundle": str(sde_root),
        "shipCount": len(records),
        "harvestedCount": len(ok_records),
        "missingCount": len(records) - len(ok_records),
        "records": records,
    }

    write_json(LIBRARY_JSON_PATH, library)

    for record in ok_records:
        xml_text = build_fitting_xml(record, types_by_id)
        file_name = f"{to_int(record.get('shipTypeID'), 0)}-{slugify(str(record.get('shipName') or 'ship'))}.xml"
        write_text(PER_SHIP_XML_ROOT / file_name, xml_text)
        record["xmlPath"] = str((PER_SHIP_XML_ROOT / file_name).relative_to(TOOL_ROOT))

    write_text(ALL_SHIPS_XML_PATH, build_combined_xml(ok_records, types_by_id))

    summary = build_checklist(records)
    write_json(CHECKLIST_JSON_PATH, summary)
    write_text(CHECKLIST_MD_PATH, checklist_markdown(summary))
    write_json(LIBRARY_JSON_PATH, library)
    emit_event(
        event_callback,
        {
            "kind": "build-complete",
            "summary": {
                "shipCount": library["shipCount"],
                "harvestedCount": library["harvestedCount"],
                "missingCount": library["missingCount"],
            },
        },
    )

    return library


def summarize_library_payload(library: dict[str, Any] | None) -> dict[str, Any]:
    payload = library if isinstance(library, dict) else {}
    records = list(payload.get("records") or [])
    fallback_count = sum(
        1
        for record in records
        if isinstance(record, dict) and ((record.get("source") or {}).get("provider") == "fallback-donor")
    )
    public_count = sum(
        1
        for record in records
        if isinstance(record, dict) and ((record.get("source") or {}).get("provider") == "zkillboard+esi")
    )
    return {
        "generatedAt": payload.get("generatedAt"),
        "shipCount": to_int(payload.get("shipCount"), len(records)),
        "harvestedCount": to_int(payload.get("harvestedCount"), 0),
        "missingCount": to_int(payload.get("missingCount"), 0),
        "fallbackCount": fallback_count,
        "publicCount": public_count,
        "sourceBundle": payload.get("sourceBundle"),
        "sampleMissing": [
            {
                "shipTypeID": to_int(record.get("shipTypeID"), 0),
                "shipName": str(record.get("shipName") or ""),
                "groupName": str(record.get("groupName") or ""),
                "reason": str(record.get("reason") or ""),
            }
            for record in records
            if isinstance(record, dict) and record.get("status") != "ok"
        ][:8],
        "sampleFallbacks": [
            {
                "shipTypeID": to_int(record.get("shipTypeID"), 0),
                "shipName": str(record.get("shipName") or ""),
                "groupName": str(record.get("groupName") or ""),
                "donorShipName": str(((record.get("source") or {}).get("donorShipName") or "")),
            }
            for record in records
            if isinstance(record, dict) and ((record.get("source") or {}).get("provider") == "fallback-donor")
        ][:8],
    }


def load_tool_snapshot() -> dict[str, Any]:
    library = load_library_payload(required=False)
    checklist = read_json(CHECKLIST_JSON_PATH, {})
    seed_summary = read_json(SEED_SUMMARY_JSON_PATH, {})
    benchmark_summary = read_json(BENCHMARK_SUMMARY_JSON_PATH, {})
    return {
        "generatedAt": now_iso(),
        "paths": {
            "toolRoot": str(TOOL_ROOT),
            "evejsRoot": str(REPO_ROOT),
            "fittingsRoot": str(FITTINGS_OUTPUT_ROOT),
            "reportsRoot": str(REPORTS_OUTPUT_ROOT),
            "libraryJson": str(LIBRARY_JSON_PATH),
            "bundledLibraryJson": str(BUNDLED_LIBRARY_JSON_PATH),
            "allShipsXml": str(ALL_SHIPS_XML_PATH),
            "savedFittingsData": str(SAVED_FITTINGS_DATA_PATH),
        },
        "library": library if isinstance(library, dict) else {},
        "librarySummary": summarize_library_payload(library if isinstance(library, dict) else {}),
        "checklist": checklist if isinstance(checklist, dict) else {},
        "seedSummary": seed_summary if isinstance(seed_summary, dict) else {},
        "benchmarkSummary": benchmark_summary if isinstance(benchmark_summary, dict) else {},
    }


def benchmark_seed(iterations: int = 5, *, event_callback: EventCallback | None = None) -> dict[str, Any]:
    ensure_evejs_runtime_ready(require_sde=False)
    library = load_library_payload(required=True)
    records = list(library.get("records") or [])
    timings: list[float] = []
    last_summary: dict[str, Any] = {}
    total = max(1, iterations)
    for index in range(total):
        start = time.perf_counter()
        last_summary = seed_saved_fittings(records, dry_run=True, event_callback=None)
        timings.append(time.perf_counter() - start)
        emit_event(
            event_callback,
            {
                "kind": "benchmark-progress",
                "current": index + 1,
                "total": total,
                "seconds": timings[-1],
            },
        )

    timings_sorted = sorted(timings)
    summary = {
        "generatedAt": now_iso(),
        "iterations": total,
        "bestSeconds": min(timings),
        "averageSeconds": sum(timings) / len(timings),
        "p50Seconds": timings_sorted[len(timings_sorted) // 2],
        "worstSeconds": max(timings),
        "toolLibraryFits": last_summary.get("toolLibraryFits", 0),
        "charactersSeeded": last_summary.get("charactersSeeded", 0),
        "toolRecordsAdded": last_summary.get("toolRecordsAdded", 0),
        "savedFittingsPath": str(SAVED_FITTINGS_DATA_PATH),
        "evejsRoot": str(REPO_ROOT),
    }
    write_json(BENCHMARK_SUMMARY_JSON_PATH, summary)
    emit_event(event_callback, {"kind": "benchmark-complete", "summary": summary})
    return summary


def run_fitall_command(
    *,
    command: str,
    threads: int = DEFAULT_THREAD_COUNT,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_killmails: int = DEFAULT_MAX_KILLMAILS,
    limit_hulls: int | None = None,
    only_missing: bool = False,
    selected_ship_names: list[str] | None = None,
    dry_run: bool = False,
    event_callback: EventCallback | None = None,
    log_callback: LogCallback | None = print,
) -> dict[str, Any]:
    needs_build = command in {"build-library", "build-and-seed"}
    if needs_build:
        ensure_evejs_runtime_ready(require_sde=True)
        sde_root: Path | None = find_latest_sde_root()
        source_bundle = str(sde_root)
        emit_log(log_callback, f"[FitALL] Using ClientSDE bundle: {sde_root}")
    else:
        ensure_evejs_runtime_ready(require_sde=False)
        sde_root = None
        source_bundle = str(BUNDLED_LIBRARY_JSON_PATH if BUNDLED_LIBRARY_JSON_PATH.exists() else LIBRARY_JSON_PATH)
        emit_log(log_callback, f"[FitALL] Using EVE JS folder: {REPO_ROOT}")
        emit_log(log_callback, f"[FitALL] Using fitting library: {source_bundle}")

    emit_event(
        event_callback,
        {
            "kind": "job-start",
            "command": command,
            "sourceBundle": source_bundle,
            "threads": max(1, threads),
            "maxPages": max(1, max_pages),
            "maxKillmails": max(1, max_killmails),
            "limitHulls": max(0, limit_hulls or 0),
            "onlyMissing": bool(only_missing),
            "selectedShipNames": list(selected_ship_names or []),
            "dryRun": bool(dry_run),
        },
    )

    library: dict[str, Any] | None = None
    seed_summary: dict[str, Any] | None = None
    benchmark_summary: dict[str, Any] | None = None

    if command in {"build-library", "build-and-seed"}:
        library = build_library(
            sde_root=sde_root or find_latest_sde_root(),
            cache_root=CACHE_OUTPUT_ROOT,
            max_pages=max(1, max_pages),
            max_killmails=max(1, max_killmails),
            threads=max(1, threads),
            limit_hulls=limit_hulls or None,
            only_missing=bool(only_missing),
            selected_ship_names=list(selected_ship_names or []),
            event_callback=event_callback,
            log_callback=log_callback,
        )
        emit_log(
            log_callback,
            f"[FitALL] Library complete: harvested {library.get('harvestedCount', 0)} / {library.get('shipCount', 0)} ships",
        )

    if command in {"seed-saved-fittings", "build-and-seed"}:
        if library is None:
            library = load_library_payload(required=True)

        seed_summary = seed_saved_fittings(
            list(library.get("records") or []),
            dry_run=bool(dry_run),
            event_callback=event_callback,
        )
        write_json(SEED_SUMMARY_JSON_PATH, seed_summary)
        emit_log(
            log_callback,
            f"[FitALL] Seed summary: characters={seed_summary.get('charactersSeeded', 0)} "
            f"added={seed_summary.get('toolRecordsAdded', 0)} skipped={len(seed_summary.get('skippedCharacters', []))}",
        )

    if command == "benchmark-seed":
        benchmark_summary = benchmark_seed(iterations=5, event_callback=event_callback)
        seed_summary = {
            "toolLibraryFits": benchmark_summary.get("toolLibraryFits", 0),
            "charactersSeeded": benchmark_summary.get("charactersSeeded", 0),
            "toolRecordsAdded": benchmark_summary.get("toolRecordsAdded", 0),
            "dryRun": True,
        }
        emit_log(
            log_callback,
            f"[FitALL] Benchmark best={benchmark_summary.get('bestSeconds', 0):.4f}s "
            f"avg={benchmark_summary.get('averageSeconds', 0):.4f}s",
        )

    snapshot = load_tool_snapshot()
    result = {
        "generatedAt": now_iso(),
        "command": command,
        "sourceBundle": source_bundle,
        "library": snapshot.get("library"),
        "librarySummary": snapshot.get("librarySummary"),
        "seedSummary": seed_summary or snapshot.get("seedSummary"),
        "benchmarkSummary": benchmark_summary or snapshot.get("benchmarkSummary"),
        "snapshot": snapshot,
    }
    emit_event(
        event_callback,
        {
            "kind": "job-complete",
            "result": {
                "command": command,
                "librarySummary": result.get("librarySummary"),
                "seedSummary": result.get("seedSummary"),
            },
        },
    )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Harvest one saved fitting per published ship hull and optionally seed savedFittings."
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="build-and-seed",
        choices=["build-library", "seed-saved-fittings", "build-and-seed", "benchmark-seed"],
    )
    parser.add_argument("--threads", type=int, default=DEFAULT_THREAD_COUNT)
    parser.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PAGES)
    parser.add_argument("--max-killmails", type=int, default=DEFAULT_MAX_KILLMAILS)
    parser.add_argument("--limit-hulls", type=int, default=0)
    parser.add_argument("--only-missing", action="store_true")
    parser.add_argument("--ship-name", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_fitall_command(
        command=args.command,
        threads=max(1, args.threads),
        max_pages=max(1, args.max_pages),
        max_killmails=max(1, args.max_killmails),
        limit_hulls=args.limit_hulls or None,
        only_missing=bool(args.only_missing),
        selected_ship_names=list(args.ship_name or []),
        dry_run=bool(args.dry_run),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
