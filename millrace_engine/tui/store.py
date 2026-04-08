"""Deterministic reducer-backed state store for the Millrace TUI."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from .models import (
    ActionResultView,
    CompoundingGovernanceOverviewView,
    ConfigOverviewView,
    DisplayMode,
    EventLogView,
    GatewayFailure,
    NoticeView,
    PanelId,
    PublishOverviewView,
    QueueOverviewView,
    RefreshPayload,
    ResearchOverviewView,
    RunDetailView,
    RunsOverviewView,
    RuntimeEventIdentity,
    RuntimeEventView,
    RuntimeOverviewView,
    runtime_event_identity,
    toggle_display_mode,
)

DEFAULT_NOTICE_LIMIT = 8
DEFAULT_EVENT_LIMIT = 200


@dataclass(frozen=True, slots=True)
class PanelFailureState:
    panel_id: PanelId
    failure: GatewayFailure


@dataclass(frozen=True, slots=True)
class TUIState:
    display_mode: DisplayMode = DisplayMode.OPERATOR
    runtime: RuntimeOverviewView | None = None
    config: ConfigOverviewView | None = None
    queue: QueueOverviewView | None = None
    research: ResearchOverviewView | None = None
    compounding: CompoundingGovernanceOverviewView | None = None
    events: EventLogView | None = None
    publish: PublishOverviewView | None = None
    runs: RunsOverviewView | None = None
    run_detail: RunDetailView | None = None
    notices: tuple[NoticeView, ...] = ()
    last_refreshed_at: datetime | None = None
    last_refresh_failure: GatewayFailure | None = None
    last_action: ActionResultView | None = None
    last_action_failure: GatewayFailure | None = None
    panel_failures: tuple[PanelFailureState, ...] = ()


def _append_notice(
    notices: tuple[NoticeView, ...],
    notice: NoticeView | None,
    *,
    notice_limit: int,
) -> tuple[NoticeView, ...]:
    if notice is None:
        return notices
    if notices:
        latest = notices[-1]
        if latest.level == notice.level and latest.title == notice.title and latest.message == notice.message:
            return notices
    trimmed = notices + (notice,)
    if len(trimmed) <= notice_limit:
        return trimmed
    return trimmed[-notice_limit:]


def _panel_failure_tuple(panel_failures: dict[PanelId, GatewayFailure]) -> tuple[PanelFailureState, ...]:
    return tuple(
        PanelFailureState(panel_id=panel_id, failure=failure)
        for panel_id, failure in sorted(panel_failures.items(), key=lambda item: item[0].value)
    )


def _append_distinct_events(
    existing: tuple[RuntimeEventView, ...],
    incoming: tuple[RuntimeEventView, ...],
) -> tuple[RuntimeEventView, ...]:
    retained: list[RuntimeEventView] = []
    seen_identities: set[RuntimeEventIdentity] = set()
    for event in (*existing, *incoming):
        identity = runtime_event_identity(event)
        if identity in seen_identities:
            continue
        seen_identities.add(identity)
        retained.append(event)
    return tuple(retained)


def _updated_panel_failures(
    existing: tuple[PanelFailureState, ...],
    *,
    clear_panels: tuple[PanelId, ...] = (),
    set_panels: tuple[PanelId, ...] = (),
    failure: GatewayFailure | None = None,
) -> tuple[PanelFailureState, ...]:
    failures = {item.panel_id: item.failure for item in existing}
    for panel_id in clear_panels:
        failures.pop(panel_id, None)
    if failure is not None:
        for panel_id in set_panels:
            failures[panel_id] = failure
    return _panel_failure_tuple(failures)


def reduce_refresh_success(
    state: TUIState,
    payload: RefreshPayload,
    *,
    panels: tuple[PanelId, ...] = (),
    notice: NoticeView | None = None,
    notice_limit: int = DEFAULT_NOTICE_LIMIT,
) -> TUIState:
    return replace(
        state,
        runtime=(payload.runtime if payload.runtime is not None else state.runtime),
        config=(payload.config if payload.config is not None else state.config),
        queue=(payload.queue if payload.queue is not None else state.queue),
        research=(payload.research if payload.research is not None else state.research),
        compounding=(payload.compounding if payload.compounding is not None else state.compounding),
        events=(payload.events if payload.events is not None else state.events),
        publish=(payload.publish if payload.publish is not None else state.publish),
        runs=(payload.runs if payload.runs is not None else state.runs),
        run_detail=(payload.run_detail if payload.run_detail is not None else state.run_detail),
        notices=_append_notice(state.notices, notice, notice_limit=notice_limit),
        last_refreshed_at=payload.refreshed_at,
        last_refresh_failure=None,
        panel_failures=_updated_panel_failures(state.panel_failures, clear_panels=panels),
    )


def reduce_refresh_failure(
    state: TUIState,
    failure: GatewayFailure,
    *,
    panels: tuple[PanelId, ...] = (),
    notice: NoticeView | None = None,
    notice_limit: int = DEFAULT_NOTICE_LIMIT,
) -> TUIState:
    return replace(
        state,
        notices=_append_notice(state.notices, notice, notice_limit=notice_limit),
        last_refresh_failure=failure,
        panel_failures=_updated_panel_failures(state.panel_failures, set_panels=panels, failure=failure),
    )


def reduce_action_success(
    state: TUIState,
    result: ActionResultView,
    *,
    notice: NoticeView | None = None,
    notice_limit: int = DEFAULT_NOTICE_LIMIT,
) -> TUIState:
    return replace(
        state,
        notices=_append_notice(state.notices, notice, notice_limit=notice_limit),
        last_action=result,
        last_action_failure=None,
    )


def reduce_action_failure(
    state: TUIState,
    failure: GatewayFailure,
    *,
    notice: NoticeView | None = None,
    notice_limit: int = DEFAULT_NOTICE_LIMIT,
) -> TUIState:
    return replace(
        state,
        notices=_append_notice(state.notices, notice, notice_limit=notice_limit),
        last_action_failure=failure,
    )


def reduce_events_appended(
    state: TUIState,
    events: tuple[RuntimeEventView, ...],
    *,
    received_at: datetime | None = None,
    clear_panels: tuple[PanelId, ...] = (),
    notice: NoticeView | None = None,
    notice_limit: int = DEFAULT_NOTICE_LIMIT,
    event_limit: int = DEFAULT_EVENT_LIMIT,
) -> TUIState:
    existing = state.events.events if state.events is not None else ()
    combined = _append_distinct_events(existing, tuple(events))
    if len(combined) > event_limit:
        combined = combined[-event_limit:]
    if events:
        last_loaded_at = received_at or events[-1].timestamp
    elif state.events is not None:
        last_loaded_at = state.events.last_loaded_at
    else:
        last_loaded_at = received_at
    return replace(
        state,
        events=EventLogView(events=combined, last_loaded_at=last_loaded_at),
        notices=_append_notice(state.notices, notice, notice_limit=notice_limit),
        panel_failures=_updated_panel_failures(state.panel_failures, clear_panels=clear_panels),
    )


def reduce_panel_failure(
    state: TUIState,
    failure: GatewayFailure,
    *,
    panels: tuple[PanelId, ...],
    notice: NoticeView | None = None,
    notice_limit: int = DEFAULT_NOTICE_LIMIT,
) -> TUIState:
    return replace(
        state,
        notices=_append_notice(state.notices, notice, notice_limit=notice_limit),
        panel_failures=_updated_panel_failures(state.panel_failures, set_panels=panels, failure=failure),
    )


def reduce_display_mode_updated(
    state: TUIState,
    display_mode: DisplayMode,
) -> TUIState:
    return replace(state, display_mode=display_mode)


class TUIStore:
    """Small state container that applies pure reducer helpers."""

    def __init__(
        self,
        initial_state: TUIState | None = None,
        *,
        notice_limit: int = DEFAULT_NOTICE_LIMIT,
        event_limit: int = DEFAULT_EVENT_LIMIT,
    ) -> None:
        self._state = initial_state or TUIState()
        self.notice_limit = notice_limit
        self.event_limit = event_limit

    @property
    def state(self) -> TUIState:
        return self._state

    def apply_refresh_success(
        self,
        payload: RefreshPayload,
        *,
        panels: tuple[PanelId, ...] = (),
        notice: NoticeView | None = None,
    ) -> TUIState:
        self._state = reduce_refresh_success(
            self._state,
            payload,
            panels=panels,
            notice=notice,
            notice_limit=self.notice_limit,
        )
        return self._state

    def apply_refresh_failure(
        self,
        failure: GatewayFailure,
        *,
        panels: tuple[PanelId, ...] = (),
        notice: NoticeView | None = None,
    ) -> TUIState:
        self._state = reduce_refresh_failure(
            self._state,
            failure,
            panels=panels,
            notice=notice,
            notice_limit=self.notice_limit,
        )
        return self._state

    def apply_action_success(self, result: ActionResultView, *, notice: NoticeView | None = None) -> TUIState:
        self._state = reduce_action_success(
            self._state,
            result,
            notice=notice,
            notice_limit=self.notice_limit,
        )
        return self._state

    def apply_action_failure(self, failure: GatewayFailure, *, notice: NoticeView | None = None) -> TUIState:
        self._state = reduce_action_failure(
            self._state,
            failure,
            notice=notice,
            notice_limit=self.notice_limit,
        )
        return self._state

    def append_events(
        self,
        events: tuple[RuntimeEventView, ...],
        *,
        received_at: datetime | None = None,
        clear_panels: tuple[PanelId, ...] = (),
        notice: NoticeView | None = None,
    ) -> TUIState:
        self._state = reduce_events_appended(
            self._state,
            events,
            received_at=received_at,
            clear_panels=clear_panels,
            notice=notice,
            notice_limit=self.notice_limit,
            event_limit=self.event_limit,
        )
        return self._state

    def apply_panel_failure(
        self,
        failure: GatewayFailure,
        *,
        panels: tuple[PanelId, ...],
        notice: NoticeView | None = None,
    ) -> TUIState:
        self._state = reduce_panel_failure(
            self._state,
            failure,
            panels=panels,
            notice=notice,
            notice_limit=self.notice_limit,
        )
        return self._state

    def panel_failure(self, panel_id: PanelId) -> GatewayFailure | None:
        for item in self._state.panel_failures:
            if item.panel_id == panel_id:
                return item.failure
        return None

    def set_display_mode(self, display_mode: DisplayMode) -> TUIState:
        self._state = reduce_display_mode_updated(self._state, display_mode)
        return self._state

    def toggle_display_mode(self) -> TUIState:
        self._state = reduce_display_mode_updated(
            self._state,
            toggle_display_mode(self._state.display_mode),
        )
        return self._state


__all__ = [
    "DEFAULT_EVENT_LIMIT",
    "DEFAULT_NOTICE_LIMIT",
    "PanelFailureState",
    "TUIState",
    "TUIStore",
    "reduce_action_failure",
    "reduce_action_success",
    "reduce_display_mode_updated",
    "reduce_events_appended",
    "reduce_panel_failure",
    "reduce_refresh_failure",
    "reduce_refresh_success",
]
