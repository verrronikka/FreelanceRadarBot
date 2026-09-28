from aiogram.fsm.state import State, StatesGroup


class ProfileSetup(StatesGroup):
    waiting_for_name = State()
    waiting_for_sources = State()
    waiting_for_budget = State()
    waiting_for_keywords = State()
    waiting_for_ai_prompt = State()
    confirmation = State()


class HideSimilar(StatesGroup):
    waiting_for_word = State()


class AddSource(StatesGroup):
    waiting_for_url = State()
