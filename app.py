import logging
import aiohttp
import asyncio
import xml.etree.ElementTree as ET
import nest_asyncio  # Для запуска в PyCharm/Windows без ошибки event loop

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Применяем патч для nested loop (нужен в PyCharm)
nest_asyncio.apply()

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)
logger = logging.getLogger(__name__)

# --- КОНСТАНТЫ ---
BOT_TOKEN = '8202181253:AAG_UlLDzR_Xq0XjcKZRz-h_PMVcY_uIp_M'

OPERATOR_USERNAME = 'POIZONDPR'

CBR_DAILY_XML_URL = 'https://www.cbr.ru/scripts/XML_daily.asp'

FIXED_DELIVERY_COST_RUB = 1500
EXCHANGE_RATE_MARKUP = 1.20  # 20% наценка
EURO_THRESHOLD_FOR_TAX = 200
ADDITIONAL_TAX_PERCENT = 0.15  # 15%


# --- ПОЛУЧЕНИЕ КУРСОВ ---
async def get_rates() -> dict:
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            async with session.get(CBR_DAILY_XML_URL) as response:
                response.raise_for_status()
                text = await response.text(encoding='windows-1251')

        root = ET.fromstring(text)

        raw_date = root.attrib.get('Date', 'неизвестно')
        day, month, year = raw_date.split('.')
        months = {'01': 'января', '02': 'февраля', '03': 'марта', '04': 'апреля',
                  '05': 'мая', '06': 'июня', '07': 'июля', '08': 'августа',
                  '09': 'сентября', '10': 'октября', '11': 'ноября', '12': 'декабря'}
        formatted_date = f"{int(day)} {months.get(month, month)} {year}"

        eur_rate = None
        cny_rate = None

        for valute in root.findall('Valute'):
            charcode = valute.find('CharCode').text
            nominal = int(valute.find('Nominal').text)
            value_str = valute.find('Value').text.replace(',', '.')
            rate = float(value_str) / nominal

            if charcode == 'EUR':
                eur_rate = rate
            elif charcode == 'CNY':
                cny_rate = rate

        if eur_rate is None or cny_rate is None:
            logger.error("Не найдены курсы EUR или CNY в XML.")
            return {"eur": None, "cny": None, "date": None}

        logger.info(f"Курсы получены на {formatted_date}: EUR {eur_rate:.4f}, CNY {cny_rate:.4f}")
        return {"eur": eur_rate, "cny": cny_rate, "date": formatted_date}

    except Exception as e:
        logger.error(f"Ошибка при получении курсов: {e}")
        return {"eur": None, "cny": None, "date": None}


# --- ОБРАБОТЧИКИ КОМАНД ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await update.message.reply_html(
        f"Привет, {user.mention_html()}! 👋\n\n"
        "Я бот-помощник по доставке товаров из Китая (Poizon и др.) в РФ.\n\n"
        "Доступные команды:\n"
        "/calc — Калькулятор стоимости\n"
        "/poizon — Инструкция по Poizon\n"
        "/operator — Связь с оператором\n\n"
        "Или просто отправьте цену в юанях (например: 500) для быстрого расчёта."
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Доступные команды:\n"
        "/start — Главное меню\n"
        "/calc — Калькулятор\n"
        "/operator — Связаться с оператором\n"
        "/poizon — Инструкция по Poizon\n\n"
        "Для расчёта просто отправьте цену товара в юанях (например: 500)."
    )


async def calc_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Введите цену товара в юанях (только число, например: 500)"
    )


async def calculate_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text.strip().replace(",", ".")

    try:
        price_cny = float(text)
        if price_cny <= 0:
            raise ValueError
    except ValueError:
        if update.message.text.startswith("/"):
            return
        await update.message.reply_text(
            "Не удалось распознать число. Пожалуйста, введите только цену в юанях.\n"
            "Пример: 500\n\n"
            "Или используйте команды: /calc, /operator, /poizon"
        )
        return

    rates = await get_rates()
    if rates["cny"] is None or rates["eur"] is None:
        await update.message.reply_text(
            f"Не удалось получить курсы валют от ЦБ РФ. Попробуйте позже или напишите оператору: @{OPERATOR_USERNAME}"
        )
        return

    cny_rate = rates["cny"]
    eur_rate = rates["eur"]

    price_rub_base = price_cny * cny_rate
    price_rub_with_markup = price_rub_base * EXCHANGE_RATE_MARKUP
    price_eur = price_rub_with_markup / eur_rate

    additional_tax_rub = 0.0
    if price_eur > EURO_THRESHOLD_FOR_TAX:
        taxable_eur = price_eur - EURO_THRESHOLD_FOR_TAX
        additional_tax_rub = taxable_eur * ADDITIONAL_TAX_PERCENT * eur_rate

    total_rub = price_rub_with_markup + FIXED_DELIVERY_COST_RUB + additional_tax_rub

    # Форматирование рублей с пробелами
    def rub_format(value: float) -> str:
        return f"{value:,.0f}".replace(",", " ")

    response = (
        "<b>Примерная цена доставки за одну пару кроссовок\n"
        "(до 1,5 кг с учётом упаковки):</b>\n\n"
        f"<b>{rub_format(total_rub)} ₽</b>\n\n"
    )

    if price_eur > EURO_THRESHOLD_FOR_TAX:
        response += (
            "<i>В стоимость включён дополнительный налог 15%\n"
            f"за превышение лимита в 200€ (цена товара ≈ {price_eur:.0f}€).</i>\n\n"
        )

    response += "Для точного расчёта и оформления заказа свяжитесь с оператором:"

    keyboard = [[InlineKeyboardButton("Написать оператору", url=f"https://t.me/{OPERATOR_USERNAME}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_html(response, reply_markup=reply_markup)


async def operator_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("Написать оператору", url=f"https://t.me/{OPERATOR_USERNAME}")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Для оформления заказа или консультации нажмите кнопку ниже:",
        reply_markup=reply_markup
    )


async def poizon_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[InlineKeyboardButton("Перейти к инструкции", url="https://t.me/poizondn/5")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "Инструкция по заказу через Poizon:",
        reply_markup=reply_markup
    )


# --- ЗАПУСК БОТА ---
async def main() -> None:
    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("calc", calc_command))
    application.add_handler(CommandHandler("operator", operator_command))
    application.add_handler(CommandHandler("poizon", poizon_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, calculate_price))

    logger.info("Бот запущен...")
    await application.run_polling()


if __name__ == '__main__':
    if 'YOUR_TELEGRAM_BOT_TOKEN' in BOT_TOKEN:
        print("ОШИБКА: Замените BOT_TOKEN на реальный токен от BotFather!")
    else:
        asyncio.run(main())
