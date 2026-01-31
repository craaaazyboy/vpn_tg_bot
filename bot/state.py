from aiogram.fsm.state import State, StatesGroup

class RequestPeer(StatesGroup):
    waiting_name = State()

class RequestIkev2(StatesGroup):
    waiting_device_name = State()

class SupportDialog(StatesGroup):
    waiting_subject = State()
    waiting_text = State()

class AdminReply(StatesGroup):
    waiting_text = State()  # в data FSM храним ticket_id