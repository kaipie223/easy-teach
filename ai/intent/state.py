"""对话状态机 — 管理意图理解的状态转移"""

from enum import Enum


class State(str, Enum):
    INIT = "init"
    PROBING = "probing"
    CONFIRMING = "confirming"
    LOCKED = "locked"


TRANSITIONS = {
    State.INIT: [State.PROBING],
    State.PROBING: [State.PROBING, State.CONFIRMING],
    State.CONFIRMING: [State.LOCKED, State.PROBING],
    State.LOCKED: [],
}


class IntentStateMachine:

    def __init__(self):
        self.state: State = State.INIT

    def transition(self, is_complete: bool, locked: bool = False) -> State:
        if locked:
            self.state = State.LOCKED
            return self.state

        if self.state in (State.INIT, State.PROBING):
            self.state = State.CONFIRMING if is_complete else State.PROBING

        return self.state

    def reset(self):
        self.state = State.INIT

    @property
    def can_modify(self) -> bool:
        return self.state != State.LOCKED
