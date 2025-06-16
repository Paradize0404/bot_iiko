from aiogram.fsm.state import State, StatesGroup


class RegisterStates(StatesGroup):
    waiting_for_name = State()

class SalaryStates(StatesGroup):
    selecting_start = State()
    selecting_end = State()

class DocumentFlow(StatesGroup):
    picking_src = State()   # выбираем «с какого склада»
    picking_dst = State()   # (если нужно) «на какой склад»