
"""
Logger utilities used by the Traceability application.

Ten moduł dostarcza prosty system zapisu zdarzeń do plików dziennika
(`logs/`) oraz mechanizm buforowania i synchronizacji zapisów na
udostępniony folder sieciowy. Główne elementy:
- `_BufferedSink` – buforowany zapis plikowy, który kolejkowuje wpisy gdy
    katalog docelowy jest chwilowo niedostępny
- funkcje `init_logging`, `log_event`, `flush_pending_events`, `set_extra_log_dir`
    – API do inicjalizacji i wysyłania zdarzeń

Moduł jest zaprojektowany tak, by obsługiwać zarówno lokalne logi,
jak i opcjonalny katalog `extra_dir` (np. udział sieciowy) z buforowaniem
w przypadku braku dostępu.
"""

import atexit
import json
import logging
import os
import threading
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, Optional


class _BufferedSink:
    """Append-only writer that buffers records when the destination is unavailable."""

    def __init__(self, root: Optional[str], app_name: str, suffix: str = "", max_buffer: int = 20000):
        self._lock = threading.Lock()
        self.suffix = suffix
        self.max_buffer = max_buffer
        self.buffer = []  # type: list[tuple[str, datetime]]
        self.offline = False
        self.last_error: Optional[str] = None
        self.last_path: Optional[str] = None
        self.just_recovered = False
        self.configure(root, app_name)

    def configure(self, root: Optional[str], app_name: str) -> None:
        with self._lock:
            self.root = root
            self.app_name = app_name
            self.just_recovered = False
            if root is None:
                self.buffer.clear()
                self.offline = False
                self.last_error = None
                self.last_path = None

    def _logs_dir(self) -> Optional[str]:
        if not self.root:
            return None
        return os.path.join(self.root, "logs")

    def _filename(self, when: datetime) -> str:
        date_str = when.strftime("%Y-%m-%d")
        if self.suffix:
            return f"{self.app_name}{self.suffix}_{date_str}.log"
        return f"{self.app_name}_{date_str}.log"

    def _write_direct(self, line: str, when: datetime) -> None:
        logs_dir = self._logs_dir()
        if logs_dir is None:
            raise FileNotFoundError("Logs directory not configured")
        os.makedirs(logs_dir, exist_ok=True)
        path = os.path.join(logs_dir, self._filename(when))
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.write("\n")
        self.last_path = path

    def _append_buffer(self, line: str, when: datetime) -> None:
        self.buffer.append((line, when))
        if len(self.buffer) > self.max_buffer:
            # keep the most recent records if buffer grows too large
            self.buffer = self.buffer[-self.max_buffer :]

    def _flush_buffer_locked(self) -> bool:
        if not self.buffer:
            self.offline = False
            self.last_error = None
            return True
        pending = list(self.buffer)
        self.buffer.clear()
        for idx, (line, when) in enumerate(pending):
            try:
                self._write_direct(line, when)
            except Exception as exc:  # pragma: no cover - defensive
                self.buffer = pending[idx:] + self.buffer
                self.offline = True
                self.last_error = str(exc)
                return False
        self.offline = False
        self.last_error = None
        return True

    def write(self, line: str, when: Optional[datetime] = None) -> bool:
        if self.root is None:
            return True
        moment = when or datetime.now()
        with self._lock:
            self.just_recovered = False
            previously_offline = self.offline or bool(self.buffer)
            try:
                self._write_direct(line, moment)
            except Exception as exc:  # pragma: no cover - defensive
                self.offline = True
                self.last_error = str(exc)
                self._append_buffer(line, moment)
                return False
            success = self._flush_buffer_locked()
            if success:
                self.just_recovered = previously_offline
            return success

    def try_flush(self) -> bool:
        if self.root is None:
            return True
        with self._lock:
            self.just_recovered = False
            previously_offline = self.offline or bool(self.buffer)
            success = self._flush_buffer_locked()
            if success:
                self.just_recovered = previously_offline
            return success

    def path_hint(self) -> Optional[str]:
        with self._lock:
            if self.last_path:
                return self.last_path
            logs_dir = self._logs_dir()
            if logs_dir is None:
                return None
            return os.path.join(logs_dir, self._filename(datetime.now()))


def _format_line(payload: Dict[str, Any], when: datetime) -> str:
    return f"{when.strftime('%Y-%m-%d %H:%M:%S')} {json.dumps(payload, ensure_ascii=False)}"


_network_status_recursing = False
_disk_status_recursing = False


def _safe_note_network_ok(**details: Any) -> None:
    global _network_status_recursing
    if _network_status_recursing:
        return
    _network_status_recursing = True
    try:
        note_network_ok(**details)
    finally:
        _network_status_recursing = False


def _safe_note_network_error(**details: Any) -> None:
    global _network_status_recursing
    if _network_status_recursing:
        return
    _network_status_recursing = True
    try:
        note_network_error(**details)
    finally:
        _network_status_recursing = False


def _safe_note_disk_ok(**details: Any) -> None:
    global _disk_status_recursing
    if _disk_status_recursing:
        return
    _disk_status_recursing = True
    try:
        note_disk_ok(**details)
    finally:
        _disk_status_recursing = False


def _safe_note_disk_error(**details: Any) -> None:
    global _disk_status_recursing
    if _disk_status_recursing:
        return
    _disk_status_recursing = True
    try:
        note_disk_error(**details)
    finally:
        _disk_status_recursing = False


def _handle_sink_result(sink: Optional[_BufferedSink], success: bool, stage: str, kind: str) -> None:
    if sink is None:
        return
    if kind == "network":
        should_signal_ok = success and (sink.just_recovered or _network_up is not True)
        if should_signal_ok:
            hint = sink.path_hint()
            details: Dict[str, Any] = {"stage": stage}
            if hint:
                details["extra_path"] = hint
            _safe_note_network_ok(**details)
            sink.just_recovered = False
        elif not success:
            details = {"stage": stage}
            hint = sink.path_hint()
            if hint:
                details["extra_path"] = hint
            if sink.last_error:
                details["error"] = sink.last_error
            _safe_note_network_error(**details)
    elif kind == "disk":
        should_signal_ok = success and (sink.just_recovered or _disk_up is not True)
        if should_signal_ok:
            hint = sink.path_hint()
            details = {"stage": stage}
            if hint:
                details["path"] = hint
            _safe_note_disk_ok(**details)
            sink.just_recovered = False
        elif not success:
            details = {"stage": stage}
            hint = sink.path_hint()
            if hint:
                details["path"] = hint
            if sink.last_error:
                details["error"] = sink.last_error
            _safe_note_disk_error(**details)


_logger = None
_base_dir: Optional[str] = None
_app_name: Optional[str] = None
_current_date: Optional[date] = None
_extra_dir: Optional[str] = None
_session_id: Optional[str] = None

_KEY_SEQUENCE_TIMEOUT = timedelta(seconds=1.0)
_key_buffer: Optional[Dict[str, Any]] = None

_network_up: Optional[bool] = None
_disk_up: Optional[bool] = None

_network_main_sink: Optional[_BufferedSink] = None
_network_key_sink: Optional[_BufferedSink] = None
_local_key_sink: Optional[_BufferedSink] = None


def _create_file_handler(base_dir: str, app_name: str):
    logs_dir = os.path.join(base_dir, "logs")
    os.makedirs(logs_dir, exist_ok=True)
    date_str = datetime.now().strftime("%Y-%m-%d")
    fname = f"{app_name}_{date_str}.log"
    path = os.path.join(logs_dir, fname)
    handler = logging.FileHandler(path, encoding="utf-8")
    formatter = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    handler.setFormatter(formatter)
    return handler, path


def init_logging(base_dir: str, app_name: str = "QW2", level=logging.INFO, extra_dir: Optional[str] = None):
    """Initialize logging to daily files locally and optionally on a network share."""

    global _logger, _base_dir, _app_name, _current_date, _extra_dir, _session_id
    global _network_up, _network_main_sink, _network_key_sink, _local_key_sink

    _base_dir = base_dir
    _app_name = app_name
    _current_date = date.today()
    _extra_dir = extra_dir
    _network_up = None

    if _session_id is None:
        _session_id = uuid.uuid4().hex
        session_started = True
    else:
        session_started = False

    if _local_key_sink is None:
        _local_key_sink = _BufferedSink(base_dir, app_name, suffix="_keys", max_buffer=20000)
    else:
        _local_key_sink.configure(base_dir, app_name)

    if extra_dir:
        if _network_main_sink is None:
            _network_main_sink = _BufferedSink(extra_dir, app_name, suffix="", max_buffer=50000)
        else:
            _network_main_sink.configure(extra_dir, app_name)
        if _network_key_sink is None:
            _network_key_sink = _BufferedSink(extra_dir, app_name, suffix="_keys", max_buffer=50000)
        else:
            _network_key_sink.configure(extra_dir, app_name)
    else:
        _network_main_sink = None
        _network_key_sink = None

    try:
        logger = logging.getLogger(app_name)
        logger.setLevel(level)

        for handler in list(logger.handlers):
            logger.removeHandler(handler)

        handler, path = _create_file_handler(base_dir, app_name)
        logger.addHandler(handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(handler.formatter)
        logger.addHandler(stream_handler)

        _logger = logger

        log_event("logger_initialized", base_dir=base_dir, path=path)

        if session_started:
            log_startup(app_name=app_name, base_dir=base_dir, extra_dir=_extra_dir)

    except Exception as exc:  # pragma: no cover - defensive
        logging.getLogger().warning(f"Failed to init logger: {exc}")


def _rotate_if_needed():
    global _current_date

    if _logger is None or _base_dir is None or _app_name is None:
        return

    today = date.today()
    if _current_date != today:
        flush_pending_events(reason="date_change")
        init_logging(
            _base_dir,
            _app_name,
            level=_logger.level if _logger else logging.INFO,
            extra_dir=_extra_dir,
        )


def _ensure():
    global _logger

    if _logger is None:
        logging.basicConfig()
        _logger = logging.getLogger("QW2_fallback")


def _merge_payload(payload: Dict[str, Any], extra: Dict[str, Any]) -> None:
    for key, value in extra.items():
        try:
            json.dumps(value)
            payload[key] = value
        except TypeError:
            payload[key] = str(value)


def _emit(level: str, payload: Dict[str, Any], when: Optional[datetime] = None) -> None:
    _ensure()
    moment = when or datetime.now()
    msg = json.dumps(payload, ensure_ascii=False)
    try:
        if level == "debug":
            _logger.debug(msg)
        elif level in {"warning", "warn"}:
            _logger.warning(msg)
        elif level == "error":
            _logger.error(msg)
        else:
            _logger.info(msg)
    except Exception:  # pragma: no cover - defensive
        logging.getLogger().info(msg)

    line = _format_line(payload, moment)
    if _network_main_sink:
        success = _network_main_sink.write(line, moment)
        _handle_sink_result(_network_main_sink, success, "main_log", "network")


def _flush_key_buffer(reason: str) -> None:
    global _key_buffer

    if not _key_buffer:
        return

    buffer = _key_buffer
    _key_buffer = None

    payload = {
        "ts": datetime.now().isoformat(),
        "event": "key_sequence",
        "text": buffer["text"],
        "length": len(buffer["text"]),
        "user": buffer.get("user"),
        "widget": buffer.get("widget"),
        "started_at": buffer.get("start_ts"),
        "ended_at": buffer.get("last_ts"),
        "keys": buffer.get("keys", []),
        "flush_reason": reason,
    }
    if _session_id:
        payload["session_id"] = _session_id

    level = buffer.get("level", "info")
    when = buffer.get("last_ts")
    moment = datetime.fromisoformat(when) if isinstance(when, str) else datetime.now()
    _emit(level, payload, moment)


def flush_pending_events(reason: str = "manual") -> None:
    _flush_key_buffer(reason)

    if _local_key_sink:
        success = _local_key_sink.try_flush()
        _handle_sink_result(_local_key_sink, success, "flush_local_keys", "disk")

    if _network_key_sink:
        success = _network_key_sink.try_flush()
        _handle_sink_result(_network_key_sink, success, "flush_network_keys", "network")

    if _network_main_sink:
        success = _network_main_sink.try_flush()
        _handle_sink_result(_network_main_sink, success, "flush_network_main", "network")


@atexit.register
def _flush_on_exit() -> None:
    flush_pending_events(reason="atexit")


def _write_keypress_log(raw_kwargs: Dict[str, Any], when: datetime) -> None:
    payload = {"ts": when.isoformat(), "event": "key"}
    if _session_id:
        payload["session_id"] = _session_id
    _merge_payload(payload, raw_kwargs)
    line = _format_line(payload, when)

    if _local_key_sink:
        success_local = _local_key_sink.write(line, when)
        _handle_sink_result(_local_key_sink, success_local, "key_log_local", "disk")

    if _network_key_sink:
        success_network = _network_key_sink.write(line, when)
        _handle_sink_result(_network_key_sink, success_network, "key_log_network", "network")


def log_event(name: str, level: str = "info", **kwargs: Any) -> None:
    try:
        _rotate_if_needed()
    except Exception:
        pass

    _ensure()

    now = datetime.now()
    lvl = (level or "info").lower()

    if name == "key":
        _write_keypress_log(kwargs, now)

    if name != "key":
        flush_pending_events(reason="non_key_event")
        payload = {"ts": now.isoformat(), "event": name}
        if _session_id:
            payload["session_id"] = _session_id
        _merge_payload(payload, kwargs)
        _emit(lvl, payload, now)
        return

    key_text = kwargs.get("text") or ""
    is_textual = bool(key_text) and key_text.isprintable()

    if _key_buffer and now - datetime.fromisoformat(_key_buffer["last_ts"]) > _KEY_SEQUENCE_TIMEOUT:
        _flush_key_buffer("timeout")

    if not is_textual:
        flush_pending_events(reason="non_textual_key")
        payload = {"ts": now.isoformat(), "event": name}
        if _session_id:
            payload["session_id"] = _session_id
        _merge_payload(payload, kwargs)
        _emit(lvl, payload, now)
        return

    entry = {
        "key": kwargs.get("key"),
        "key_name": kwargs.get("key_name"),
        "text": key_text,
        "ts": now.isoformat(),
    }

    user = kwargs.get("user")
    widget = kwargs.get("widget")

    if _key_buffer:
        same_user = _key_buffer.get("user") == user
        same_widget = _key_buffer.get("widget") == widget
        last_ts = datetime.fromisoformat(_key_buffer["last_ts"])
        if same_user and same_widget and now - last_ts <= _KEY_SEQUENCE_TIMEOUT:
            _key_buffer["text"] += key_text
            _key_buffer["last_ts"] = entry["ts"]
            _key_buffer.setdefault("keys", []).append(entry)
            return
        _flush_key_buffer("sequence_break")

    _key_buffer = {
        "text": key_text,
        "user": user,
        "widget": widget,
        "start_ts": entry["ts"],
        "last_ts": entry["ts"],
        "keys": [entry],
        "level": lvl,
    }


def _update_status(kind: str, is_ok: bool, details: Dict[str, Any]) -> None:
    global _network_up, _disk_up

    if kind == "network":
        previous = _network_up
        _network_up = is_ok
    else:
        previous = _disk_up
        _disk_up = is_ok

    if previous is None:
        event_name = f"{kind}_connection_available" if is_ok else f"{kind}_connection_lost"
    elif previous == is_ok:
        return
    else:
        event_name = f"{kind}_connection_restored" if is_ok else f"{kind}_connection_lost"

    log_event(event_name, **details)


def log_startup(**details: Any) -> None:
    log_event("app_startup", **details)


def note_network_ok(**details: Any) -> None:
    _update_status("network", True, details)


def note_network_error(**details: Any) -> None:
    _update_status("network", False, details)


def note_disk_ok(**details: Any) -> None:
    _update_status("disk", True, details)


def note_disk_error(**details: Any) -> None:
    _update_status("disk", False, details)


def get_logger():
    _ensure()
    return _logger


def set_extra_log_dir(path: Optional[str]):
    global _extra_dir, _network_main_sink, _network_key_sink, _network_up
    _extra_dir = path
    _network_up = None
    if path and _app_name:
        if _network_main_sink is None:
            _network_main_sink = _BufferedSink(path, _app_name, suffix="", max_buffer=50000)
        else:
            _network_main_sink.configure(path, _app_name)
        if _network_key_sink is None:
            _network_key_sink = _BufferedSink(path, _app_name, suffix="_keys", max_buffer=50000)
        else:
            _network_key_sink.configure(path, _app_name)
    else:
        _network_main_sink = None
        _network_key_sink = None
