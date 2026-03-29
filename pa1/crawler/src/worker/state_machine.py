"""Worker state machine for crawler worker lifecycle and state transitions.

Worker states represent discrete stages in the worker's lifecycle:
- IDLE: Awaiting assignment or processing
- ACTIVE: Actively processing a URL
- PAUSED: Temporarily paused by operator/system
- STOPPED: Stopped and not processing

State transitions are always reported to the server.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable


class WorkerState(Enum):
    """Worker lifecycle states."""
    
    IDLE = "idle"              # Initialized, waiting for queue item or previous item processing
    ACTIVE = "active"          # Processing a URL from the queue
    PAUSED = "paused"          # Paused by operator or system condition
    STOPPED = "stopped"        # Stopped and not accepting work


@dataclass
class StateTransition:
    """Record of a state transition with timestamp and reason."""
    
    from_state: WorkerState
    to_state: WorkerState
    timestamp: str              # ISO format
    reason: str                 # Why the transition occurred
    current_url: str | None = None  # URL being processed (for ACTIVE state)


class WorkerStateMachine:
    """Manages worker state transitions and ensures valid state paths."""
    
    # Valid transitions: state -> list of allowed next states
    VALID_TRANSITIONS: dict[WorkerState, set[WorkerState]] = {
        WorkerState.IDLE: {WorkerState.ACTIVE, WorkerState.PAUSED, WorkerState.STOPPED},
        WorkerState.ACTIVE: {WorkerState.IDLE, WorkerState.PAUSED, WorkerState.STOPPED},
        WorkerState.PAUSED: {WorkerState.IDLE, WorkerState.ACTIVE, WorkerState.STOPPED},
        WorkerState.STOPPED: {WorkerState.IDLE},  # Can only restart from STOPPED to IDLE
    }
    
    def __init__(self, worker_id: int, initial_state: WorkerState = WorkerState.IDLE):
        """Initialize state machine for a worker.
        
        Args:
            worker_id: Unique worker identifier
            initial_state: Starting state (default: IDLE)
        """
        self.worker_id = worker_id
        self._current_state = initial_state
        self._state_changed_at = self._now_iso()
        self._transitions: list[StateTransition] = [
            StateTransition(
                from_state=initial_state,  # Conceptually from None, but use initial for clarity
                to_state=initial_state,
                timestamp=self._state_changed_at,
                reason="initialized",
            )
        ]
        self._state_change_callbacks: list[Callable[[StateTransition], None]] = []
    
    def current_state(self) -> WorkerState:
        """Get current state."""
        return self._current_state
    
    def state_name(self) -> str:
        """Get current state name (lowercase)."""
        return self._current_state.value
    
    def state_changed_at(self) -> str:
        """Get ISO timestamp of last state change."""
        return self._state_changed_at
    
    def can_transition(self, to_state: WorkerState) -> bool:
        """Check if transition is valid."""
        return to_state in self.VALID_TRANSITIONS.get(self._current_state, set())
    
    def transition(self, to_state: WorkerState, reason: str, current_url: str | None = None) -> bool:
        """Attempt a state transition.
        
        Args:
            to_state: Target state
            reason: Human-readable reason for transition
            current_url: URL being processed (only used when transitioning to ACTIVE)
        
        Returns:
            True if transition succeeded, False if invalid
        """
        if not self.can_transition(to_state):
            return False
        
        if self._current_state == to_state:
            # Already in target state; not a real transition
            return True
        
        # Record transition
        transition = StateTransition(
            from_state=self._current_state,
            to_state=to_state,
            timestamp=self._now_iso(),
            reason=reason,
            current_url=current_url if to_state == WorkerState.ACTIVE else None,
        )
        
        self._current_state = to_state
        self._state_changed_at = transition.timestamp
        self._transitions.append(transition)
        
        # Trigger callbacks (for server reporting)
        for callback in self._state_change_callbacks:
            try:
                callback(transition)
            except Exception as e:
                print(f"[state-machine] callback error for worker {self.worker_id}: {e}")
        
        return True
    
    def register_state_change_callback(
        self, callback: Callable[[StateTransition], None]
    ) -> None:
        """Register a callback to be called on state transitions.
        
        Used for server reporting and logging.
        
        Args:
            callback: Function accepting StateTransition
        """
        self._state_change_callbacks.append(callback)
    
    def transition_history(self) -> list[StateTransition]:
        """Get all state transitions (for debugging/audit)."""
        return self._transitions.copy()
    
    def to_dict(self) -> dict[str, object]:
        """Serialize state machine for API response."""
        return {
            "workerId": self.worker_id,
            "currentState": self.state_name(),
            "stateChangedAt": self._state_changed_at,
            "uptime": "PT0S",  # TODO: calculate from first transition
        }
    
    @staticmethod
    def _now_iso() -> str:
        """Get current UTC time in ISO format."""
        return datetime.now(timezone.utc).isoformat()


# Convenience state transition diagram (for documentation):
#
# IDLE <---> ACTIVE <---> PAUSED
#  ^           ^            ^
#  |           |            |
#  +-----------+-----------+
#             |
#          STOPPED
#             ^
#             |
#        IDLE, ACTIVE, PAUSED (can stop from any state)
#
# Key transitions:
#  IDLE -> ACTIVE: Worker acquired a queue item and started processing
#  ACTIVE -> IDLE: Worker finished processing the item
#  ACTIVE -> PAUSED: Operator paused worker or system condition triggered pause
#  PAUSED -> ACTIVE: Operator resumed worker
#  * -> PAUSED: Can pause from any state (except continuing from IDLE/PAUSED)
#  * -> STOPPED: Can stop from IDLE, ACTIVE, or PAUSED
#  STOPPED -> IDLE: Can restart from STOPPED (must be explicit)
