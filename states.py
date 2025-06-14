from aiogram.fsm.state import State, StatesGroup


class RegisterStates(StatesGroup):
    waiting_for_name = State()

class SalaryStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()

