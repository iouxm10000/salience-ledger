from __future__ import annotations

import hashlib
import json
import os
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .models import utc_now
from .store import Ledger, LedgerError


RUN_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ACTORS = {"owner", "executor", "supervisor"}
EVENT_ACTORS = {
    "item_registered": ACTORS,
    "round_closed": {"executor"},
    "directive_added": {"owner", "supervisor"},
    "directive_resolved": {"executor", "supervisor"},
    "owner_blocked": {"executor", "supervisor"},
    "owner_decision": {"owner"},
    "completion_claimed": {"executor"},
    "completion_accepted": {"owner", "supervisor"},
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _require_nonempty_string(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")


def _require_evidence(value: Any, field: str = "evidence") -> None:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ValueError(f"{field} must be a non-empty list of non-empty strings")


class TaskRun:
    """Tamper-evident long-task handoff state for executor/supervisor separation."""

    def __init__(self, ledger: Ledger, run_id: str):
        if not RUN_ID_RE.fullmatch(run_id):
            raise ValueError("run_id must use lowercase letters, digits and hyphens")
        self.ledger = ledger
        self.run_id = run_id
        self.root = ledger.state / "task-runs" / run_id
        self.contract_path = self.root / "contract.json"
        self.events_path = self.root / "events.jsonl"
        self.lock_path = self.root / ".events.lock"

    def initialize(
        self,
        *,
        goal: str,
        gates: list[str],
        completion_criteria: list[str],
        scope_paths: list[str] | None = None,
        protected_paths: list[str] | None = None,
        red_lines: list[str] | None = None,
        commit_authorized: bool = False,
        push_authorized: bool = False,
        converge_every: int = 5,
        large_change_lines: int = 400,
        max_rounds: int = 50,
    ) -> dict[str, Any]:
        if self.contract_path.exists() or self.events_path.exists():
            raise LedgerError(f"task run already exists: {self.run_id}")
        if not goal.strip() or not gates or not completion_criteria:
            raise ValueError("goal, at least one gate, and at least one completion criterion are required")
        if converge_every < 2 or large_change_lines < 1 or max_rounds < 1:
            raise ValueError(
                "converge_every must be >= 2; large_change_lines and max_rounds must be >= 1"
            )
        if push_authorized and not commit_authorized:
            raise ValueError("push authorization requires commit authorization")
        self.root.mkdir(parents=True, exist_ok=False)
        contract = {
            "schema_version": "salience_task_contract.v1",
            "run_id": self.run_id,
            "created_at": utc_now(),
            "goal": goal.strip(),
            "gates": list(dict.fromkeys(gates)),
            "completion_criteria": list(dict.fromkeys(completion_criteria)),
            "scope_paths": list(dict.fromkeys(scope_paths or [])),
            "protected_paths": list(dict.fromkeys(protected_paths or [])),
            "red_lines": list(dict.fromkeys(red_lines or [])),
            "commit_authorized": commit_authorized,
            "push_authorized": push_authorized,
            "converge_every": converge_every,
            "large_change_lines": large_change_lines,
            "max_rounds": max_rounds,
        }
        contract["contract_sha256"] = _sha(contract)
        self._write_json_atomic(self.contract_path, contract)
        self.events_path.touch()
        self.lock_path.write_bytes(b"0")
        return contract

    def contract(self) -> dict[str, Any]:
        if not self.contract_path.exists():
            raise LedgerError(f"unknown task run: {self.run_id}")
        contract = json.loads(self.contract_path.read_text(encoding="utf-8"))
        expected = contract.pop("contract_sha256", None)
        if expected != _sha(contract):
            raise LedgerError("task contract hash mismatch")
        contract["contract_sha256"] = expected
        return contract

    def events(self) -> list[dict[str, Any]]:
        self.contract()
        result: list[dict[str, Any]] = []
        previous = "0" * 64
        with self.events_path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                if not raw.strip():
                    continue
                try:
                    event = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise LedgerError(f"invalid task event at line {line_number}: {exc}") from exc
                event_hash = event.pop("event_sha256", None)
                if event.get("sequence") != len(result) + 1:
                    raise LedgerError(f"task event sequence mismatch at line {line_number}")
                if event.get("previous_sha256") != previous:
                    raise LedgerError(f"task event chain mismatch at line {line_number}")
                if event_hash != _sha(event):
                    raise LedgerError(f"task event hash mismatch at line {line_number}")
                event["event_sha256"] = event_hash
                result.append(event)
                previous = str(event_hash)
        return result

    def append(self, *, actor: str, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if actor not in ACTORS:
            raise ValueError(f"invalid actor: {actor}")
        if event_type not in EVENT_ACTORS:
            raise ValueError(f"invalid event_type: {event_type}")
        if actor not in EVENT_ACTORS[event_type]:
            raise LedgerError(f"actor {actor} cannot append {event_type}")
        with self._exclusive_lock():
            events = self.events()
            if events and self.project()["completion"] == "accepted":
                raise LedgerError("accepted task run is closed to further events")
            self._validate_event(event_type, payload, events)
            event = {
                "schema_version": "salience_task_event.v1",
                "run_id": self.run_id,
                "sequence": len(events) + 1,
                "previous_sha256": events[-1]["event_sha256"] if events else "0" * 64,
                "recorded_at": utc_now(),
                "actor": actor,
                "event_type": event_type,
                "payload": payload,
            }
            event["event_sha256"] = _sha(event)
            with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(_canonical(event) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return event

    def project(self) -> dict[str, Any]:
        contract = self.contract()
        events = self.events()
        items: dict[str, dict[str, Any]] = {}
        directives: dict[str, dict[str, Any]] = {}
        blockers: dict[str, dict[str, Any]] = {}
        rounds: list[dict[str, Any]] = []
        completion = "active"
        for event in events:
            payload = event["payload"]
            kind = event["event_type"]
            if kind == "item_registered":
                items[payload["item_id"]] = {**payload, "status": "open"}
            elif kind == "round_closed":
                rounds.append(payload)
                if payload["result"] == "completed":
                    items[payload["item_id"]]["status"] = "completed"
                    items[payload["item_id"]]["round"] = payload["round"]
                    items[payload["item_id"]]["evidence"] = payload["evidence"]
            elif kind == "directive_added":
                directives[payload["directive_id"]] = {**payload, "status": "open"}
            elif kind == "directive_resolved":
                directives[payload["directive_id"]]["status"] = "resolved"
                directives[payload["directive_id"]]["resolution_evidence"] = payload["evidence"]
            elif kind == "owner_blocked":
                blockers[payload["blocker_id"]] = {**payload, "status": "open"}
            elif kind == "owner_decision":
                blockers[payload["blocker_id"]]["status"] = "resolved"
                blockers[payload["blocker_id"]]["decision"] = payload["decision"]
            elif kind == "completion_claimed":
                completion = "claimed"
            elif kind == "completion_accepted":
                completion = "accepted"
        return {
            "contract": contract,
            "events": events,
            "items": items,
            "directives": directives,
            "blockers": blockers,
            "rounds": rounds,
            "completion": completion,
        }

    def audit(self, *, completion: bool = False) -> list[str]:
        state = self.project()
        issues = [f"memory: {issue}" for issue in self.ledger.audit(completion=completion)]
        packets = self.ledger.clarification_packets()
        if packets:
            issues.append(f"memory: {len(packets)} USER_CONFIRMATION_REQUIRED packet(s)")
        if completion:
            issues.extend(
                f"open item: {key}" for key, value in state["items"].items()
                if value["status"] == "open"
            )
            issues.extend(
                f"open directive: {key}" for key, value in state["directives"].items()
                if value["status"] == "open"
            )
            issues.extend(
                f"owner decision required: {key}" for key, value in state["blockers"].items()
                if value["status"] == "open"
            )
            if not state["rounds"]:
                issues.append("no verified rounds")
            elif any(value != "pass" for value in state["rounds"][-1]["gates"].values()):
                issues.append("latest round has a failing or unresolved gate")
        return sorted(set(issues))

    def render_context(self, query: str = "") -> str:
        issues = self.audit()
        if issues:
            raise LedgerError("task context refused:\n- " + "\n- ".join(issues))
        state = self.project()
        contract = state["contract"]
        memory = self.ledger.recovery_context(query)
        lines = [
            memory.rstrip(),
            "",
            "# Salience Task Run",
            "",
            f"- run_id: `{self.run_id}`",
            f"- goal: {contract['goal']}",
            f"- completion: `{state['completion']}`",
            f"- contract_sha256: `{contract['contract_sha256']}`",
            f"- event_sequence: `{len(state['events'])}`",
            f"- commit_authorized: `{str(contract['commit_authorized']).lower()}`",
            f"- push_authorized: `{str(contract['push_authorized']).lower()}`",
            "",
            "## Gates",
            "",
            *[f"- {gate}" for gate in contract["gates"]],
            "",
            "## Completion criteria",
            "",
            *[f"- {item}" for item in contract["completion_criteria"]],
        ]
        if contract["scope_paths"]:
            lines.extend(["", "## In scope", "", *[f"- {item}" for item in contract["scope_paths"]]])
        if contract["protected_paths"]:
            lines.extend([
                "", "## Protected paths", "", *[f"- {item}" for item in contract["protected_paths"]]
            ])
        if contract["red_lines"]:
            lines.extend(["", "## Red lines", "", *[f"- {item}" for item in contract["red_lines"]]])
        open_items = [item for item in state["items"].values() if item["status"] == "open"]
        lines.extend(["", "## Open items", ""])
        lines.extend(
            [f"- [{item['item_id']}] {item['title']} — verify: {item['acceptance']}" for item in open_items]
            or ["- none"]
        )
        completed_items = [item for item in state["items"].values() if item["status"] == "completed"]
        lines.extend(["", "## Completed items", ""])
        lines.extend(
            [
                f"- [{item['item_id']}] round {item['round']}: {item['title']}; "
                f"evidence={'; '.join(item['evidence'])}"
                for item in completed_items
            ]
            or ["- none"]
        )
        open_directives = [item for item in state["directives"].values() if item["status"] == "open"]
        lines.extend(["", "## Open supervisor directives", ""])
        lines.extend(
            [f"- [{item['directive_id']}] {item['problem']} → {item['required_action']}" for item in open_directives]
            or ["- none"]
        )
        open_blockers = [item for item in state["blockers"].values() if item["status"] == "open"]
        lines.extend(["", "## Owner-blocked", ""])
        lines.extend(
            [f"- [{item['blocker_id']}] {item['question']}" for item in open_blockers]
            or ["- none"]
        )
        lines.extend(["", "## Latest verified rounds", ""])
        for item in state["rounds"][-2:]:
            lines.append(
                f"- round {item['round']}: {item['item_id']} = {item['result']}; "
                f"gates={_canonical(item['gates'])}; evidence={'; '.join(item['evidence'])}"
            )
        if not state["rounds"]:
            lines.append("- none")
        lines.extend([
            "",
            "> Work on exactly one open item. Verify it in the same round. "
            "Do not claim completion while any item, directive, owner decision, memory conflict, or gate is open.",
            "",
        ])
        return "\n".join(lines)

    def _validate_event(
        self, event_type: str, payload: dict[str, Any], events: list[dict[str, Any]]
    ) -> None:
        if not isinstance(payload, dict):
            raise ValueError("payload must be a JSON object")
        state = self.project() if events else {
            "items": {}, "directives": {}, "blockers": {}, "rounds": [], "completion": "active"
        }
        required = {
            "item_registered": {"item_id", "title", "acceptance"},
            "round_closed": {"round", "item_id", "result", "evidence", "gates", "mode", "net_lines"},
            "directive_added": {"directive_id", "problem", "required_action", "priority"},
            "directive_resolved": {"directive_id", "evidence"},
            "owner_blocked": {"blocker_id", "question", "options"},
            "owner_decision": {"blocker_id", "decision", "source_episode_ids"},
            "completion_claimed": {"evidence", "criteria"},
            "completion_accepted": {"evidence", "criteria"},
        }[event_type]
        missing = sorted(required - set(payload))
        if missing:
            raise ValueError(f"{event_type} missing fields: {', '.join(missing)}")
        if event_type == "item_registered":
            _require_nonempty_string(payload["item_id"], "item_id")
            _require_nonempty_string(payload["title"], "title")
            _require_nonempty_string(payload["acceptance"], "acceptance")
            if payload["item_id"] in state["items"]:
                raise LedgerError(f"duplicate item_id: {payload['item_id']}")
        elif event_type == "round_closed":
            expected_round = len(state["rounds"]) + 1
            if payload["round"] != expected_round:
                raise LedgerError(f"round must be {expected_round}")
            item = state["items"].get(payload["item_id"])
            if not item or item["status"] != "open":
                raise LedgerError("round must target one open item")
            if payload["result"] not in {"completed", "blocked"}:
                raise ValueError("round result must be completed or blocked")
            _require_evidence(payload["evidence"], "round evidence")
            contract = self.contract()
            if payload["round"] > contract["max_rounds"]:
                raise LedgerError(f"round limit exceeded: {contract['max_rounds']}")
            if not isinstance(payload["gates"], dict):
                raise ValueError("round gates must be a JSON object")
            if not isinstance(payload["net_lines"], int):
                raise ValueError("net_lines must be an integer")
            expected_gates = set(contract["gates"])
            if set(payload["gates"]) != expected_gates:
                raise LedgerError("round gates must exactly match the contract")
            if any(value not in {"pass", "fail", "not_run"} for value in payload["gates"].values()):
                raise ValueError("gate values must be pass, fail, or not_run")
            if payload["mode"] not in {"implement", "converge"}:
                raise ValueError("round mode must be implement or converge")
            if payload["mode"] == "converge" and payload["net_lines"] > 0:
                raise LedgerError("convergence rounds require net_lines<=0")
            if payload["result"] == "completed" and any(
                value != "pass" for value in payload["gates"].values()
            ):
                raise LedgerError("a completed item requires every contract gate to pass")
            forced = payload["round"] % contract["converge_every"] == 0
            if state["rounds"] and state["rounds"][-1]["net_lines"] > contract["large_change_lines"]:
                forced = True
            if forced and (payload["mode"] != "converge" or payload["net_lines"] > 0):
                raise LedgerError("this round is forced convergence: mode=converge and net_lines<=0")
        elif event_type == "directive_added":
            _require_nonempty_string(payload["directive_id"], "directive_id")
            _require_nonempty_string(payload["problem"], "problem")
            _require_nonempty_string(payload["required_action"], "required_action")
            if payload["directive_id"] in state["directives"]:
                raise LedgerError(f"duplicate directive_id: {payload['directive_id']}")
        elif event_type == "directive_resolved":
            item = state["directives"].get(payload["directive_id"])
            _require_evidence(payload["evidence"], "directive evidence")
            if not item or item["status"] != "open":
                raise LedgerError("directive resolution requires an open directive and evidence")
        elif event_type == "owner_blocked":
            _require_nonempty_string(payload["blocker_id"], "blocker_id")
            _require_nonempty_string(payload["question"], "question")
            if (
                not isinstance(payload["options"], list)
                or len(payload["options"]) < 2
                or any(not isinstance(item, str) or not item.strip() for item in payload["options"])
            ):
                raise LedgerError("owner blocker must present at least two non-empty options")
            if payload["blocker_id"] in state["blockers"]:
                raise LedgerError("owner blocker must be new and present at least two options")
        elif event_type == "owner_decision":
            item = state["blockers"].get(payload["blocker_id"])
            if not item or item["status"] != "open":
                raise LedgerError("owner decision requires an open blocker")
            _require_nonempty_string(payload["decision"], "decision")
            _require_evidence(payload["source_episode_ids"], "source_episode_ids")
            observations = self.ledger.observations()
            sources = [observations.get(item) for item in payload["source_episode_ids"]]
            expected_ref = f"task-run/{self.run_id}/blocker/{payload['blocker_id']}"
            if not any(
                source
                and source["source_type"] == "user_confirmation"
                and source.get("source_ref") == expected_ref
                for source in sources
            ):
                raise LedgerError(
                    "owner decision requires a blocker-specific user_confirmation source episode "
                    f"with source_ref={expected_ref}"
                )
        elif event_type == "completion_claimed":
            _require_evidence(payload["evidence"], "completion evidence")
            self._validate_completion_criteria(payload["criteria"])
            if state["completion"] != "active":
                raise LedgerError("completion has already been claimed or accepted")
            issues = self.audit(completion=True)
            if issues:
                raise LedgerError("completion claim refused:\n- " + "\n- ".join(issues))
        elif event_type == "completion_accepted":
            _require_evidence(payload["evidence"], "completion acceptance evidence")
            self._validate_completion_criteria(payload["criteria"])
            if state["completion"] != "claimed":
                raise LedgerError("completion must be claimed before it is accepted")
            issues = self.audit(completion=True)
            if issues:
                raise LedgerError("completion acceptance refused:\n- " + "\n- ".join(issues))

    def _validate_completion_criteria(self, criteria: Any) -> None:
        contract_criteria = set(self.contract()["completion_criteria"])
        if not isinstance(criteria, dict) or set(criteria) != contract_criteria:
            raise LedgerError("completion criteria evidence must exactly match the contract")
        for criterion, evidence in criteria.items():
            _require_evidence(evidence, f"completion criterion {criterion!r}")

    @contextmanager
    def _exclusive_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.lock_path.exists():
            self.lock_path.write_bytes(b"0")
        with self.lock_path.open("r+b") as handle:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()
