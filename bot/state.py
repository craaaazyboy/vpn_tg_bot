from aiogram.fsm.state import State, StatesGroup

class RequestPeer(StatesGroup):
    waiting_name = State()