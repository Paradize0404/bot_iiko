# handlers/document_router.py
from aiogram import Router, F
from aiogram.types import Message

from keyboards.main_keyboard import main_menu_keyboard, document_menu_keyboard

router = Router()          # «локальный» роутер модуля


# ---------- показать меню документов ----------
@router.message(F.text == "Создание документа")
async def show_document_menu(message: Message) -> None:
    await message.answer(
        "Выберите тип документа:",
        reply_markup=document_menu_keyboard(),
    )


# ---------- три отдельных хендлера ----------
@router.message(F.text == "🧾 Акт приготовления")
async def handle_prep_act(message: Message) -> None:
    await message.answer("🧾 Создаём «Акт приготовления»…")
    # TODO: здесь ваша логика (FSM, БД, и т.д.)


@router.message(F.text == "📉 Акт списания")
async def handle_write_off(message: Message) -> None:
    await message.answer("📉 Создаём «Акт списания»…")
    # TODO


@router.message(F.text == "🔄 Внутреннее перемещение")
async def handle_internal_move(message: Message) -> None:
    await message.answer("🔄 Создаём «Внутреннее перемещение»…")
    # TODO


# ---------- возврат в главное меню ----------
@router.message(F.text == "⬅️ Назад")
async def back_to_main(message: Message) -> None:
    await message.answer(
        "Главное меню:",
        reply_markup=main_menu_keyboard(),
    )
