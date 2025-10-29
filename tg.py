import logging
import sqlite3
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardRemove
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# Конфигурация
BOT_TOKEN = "8295659050:AAH49krRO745v1scXU5n1Ifza7NkEAnphTQ"
ADMIN_IDS = [1226869556]
DB_NAME = "phone_accounts_bot.db"
MIN_APPROVED_FOR_WITHDRAWAL = 3  # Минимальное количество одобренных аккаунтов для вывода

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


# Инициализация базы данных
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Таблица пользователей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            phone TEXT,
            status TEXT DEFAULT 'active',
            balance REAL DEFAULT 0,
            approved_count INTEGER DEFAULT 0,
            registered_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица заявок на вывод
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            amount REAL,
            details TEXT,
            status TEXT DEFAULT 'pending',
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    # Таблица настроек (для цены)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''')

    # Таблица рассылок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            message_text TEXT,
            sent_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            users_reached INTEGER DEFAULT 0
        )
    ''')

    # Таблица верификации кодов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS verifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            phone TEXT,
            code TEXT,
            status TEXT DEFAULT 'pending',
            admin_id INTEGER,
            created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (user_id)
        )
    ''')

    # Установка начальной цены, если её нет
    cursor.execute('''
        INSERT OR IGNORE INTO settings (key, value) 
        VALUES ('current_price', '15')
    ''')

    conn.commit()
    conn.close()


# Функции для работы с базой данных
def get_db_connection():
    return sqlite3.connect(DB_NAME)


def get_current_price():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM settings WHERE key = 'current_price'")
    result = cursor.fetchone()
    conn.close()
    return float(result[0]) if result else 15.0


def set_current_price(price):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE settings SET value = ? WHERE key = 'current_price'", (str(price),))
    conn.commit()
    conn.close()


def get_user(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user


def create_user(user_id, username):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR IGNORE INTO users (user_id, username) 
        VALUES (?, ?)
    ''', (user_id, username))
    conn.commit()
    conn.close()


def update_user_phone(user_id, phone):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET phone = ?, status = 'pending_verification' WHERE user_id = ?", (phone, user_id))
    conn.commit()
    conn.close()


def create_verification_request(user_id, phone, admin_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO verifications (user_id, phone, admin_id, status) 
        VALUES (?, ?, ?, 'waiting_code')
    ''', (user_id, phone, admin_id))
    conn.commit()
    conn.close()


def get_verification_request(verification_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM verifications WHERE id = ?", (verification_id,))
    verification = cursor.fetchone()
    conn.close()
    return verification


def update_verification_code(verification_id, code):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE verifications SET code = ?, status = 'code_received' WHERE id = ?", (code, verification_id))
    conn.commit()
    conn.close()


def approve_user_phone(user_id):
    price = get_current_price()
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET status = 'approved', balance = balance + ?, approved_count = approved_count + 1 WHERE user_id = ?",
        (price, user_id))
    conn.commit()
    conn.close()


def reject_user_phone(user_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET status = 'rejected', phone = NULL WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


def complete_verification(verification_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE verifications SET status = ? WHERE id = ?", (status, verification_id))
    conn.commit()
    conn.close()


def create_withdrawal(user_id, amount, details):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO withdrawals (user_id, amount, details) 
        VALUES (?, ?, ?)
    ''', (user_id, amount, details))
    conn.commit()
    conn.close()


def get_pending_withdrawals():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM withdrawals WHERE status = 'pending'")
    withdrawals = cursor.fetchall()
    conn.close()
    return withdrawals


def update_withdrawal_status(withdrawal_id, status):
    conn = get_db_connection()
    cursor = conn.cursor()
    if status == 'approved':
        # Вычитаем сумму из баланса пользователя
        cursor.execute('''
            UPDATE users 
            SET balance = balance - (SELECT amount FROM withdrawals WHERE id = ?) 
            WHERE user_id = (SELECT user_id FROM withdrawals WHERE id = ?)
        ''', (withdrawal_id, withdrawal_id))

    cursor.execute("UPDATE withdrawals SET status = ? WHERE id = ?", (status, withdrawal_id))
    conn.commit()
    conn.close()


def get_pending_verifications():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT v.id, v.user_id, v.phone, v.status, u.username 
        FROM verifications v 
        JOIN users u ON v.user_id = u.user_id 
        WHERE v.status IN ('waiting_code', 'code_received')
    ''')
    verifications = cursor.fetchall()
    conn.close()
    return verifications


def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = [row[0] for row in cursor.fetchall()]
    conn.close()
    return users


def get_statistics():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM users WHERE status = 'approved'")
    approved_users = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM verifications WHERE status IN ('waiting_code', 'code_received')")
    pending_verifications = cursor.fetchone()[0]

    cursor.execute("SELECT SUM(balance) FROM users")
    total_balance = cursor.fetchone()[0] or 0

    cursor.execute("SELECT COUNT(*) FROM users WHERE approved_count >= ?", (MIN_APPROVED_FOR_WITHDRAWAL,))
    users_with_withdrawal_access = cursor.fetchone()[0]

    conn.close()

    return {
        'total_users': total_users,
        'approved_users': approved_users,
        'pending_verifications': pending_verifications,
        'total_balance': total_balance,
        'users_with_withdrawal_access': users_with_withdrawal_access
    }


def can_user_withdraw(user_id):
    """Проверяет, может ли пользователь выводить средства"""
    user = get_user(user_id)
    if user:
        return user[5] >= MIN_APPROVED_FOR_WITHDRAWAL  # approved_count
    return False


# Команда /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    username = update.effective_user.username

    create_user(user_id, username)

    if user_id in ADMIN_IDS:
        await show_admin_panel(update, context)
    else:
        await show_main_menu(update, context)


# Главное меню пользователя
async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📱 Продать номер", callback_data="sell_phone")],
        [InlineKeyboardButton("👤 Мой профиль", callback_data="my_profile")],
        [InlineKeyboardButton("💵 Вывод средств", callback_data="withdraw")],
        [InlineKeyboardButton("💰 Актуальный прайс", callback_data="current_price")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Главное меню:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "Главное меню:",
            reply_markup=reply_markup
        )


# Админ панель
async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("💰 Изменить прайс", callback_data="change_price")],
        [InlineKeyboardButton("📢 Сделать рассылку", callback_data="broadcast")],
        [InlineKeyboardButton("📊 Статистика", callback_data="statistics")],
        [InlineKeyboardButton("⏳ Ожидают верификации", callback_data="pending_verifications")],
        [InlineKeyboardButton("💸 Заявки на вывод", callback_data="pending_withdrawals")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            "Админ-панель:",
            reply_markup=reply_markup
        )
    else:
        await update.message.reply_text(
            "Админ-панель:",
            reply_markup=reply_markup
        )


# Обработка инлайн кнопок
async def handle_button_click(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id
    data = query.data

    if data == "sell_phone":
        await show_sell_phone_menu(query, context)
    elif data == "my_profile":
        await show_user_profile(query, context)
    elif data == "withdraw":
        await request_withdrawal(query, context)
    elif data == "current_price":
        await show_current_price(query, context)
    elif data == "change_price" and user_id in ADMIN_IDS:
        await request_new_price(query, context)
    elif data == "broadcast" and user_id in ADMIN_IDS:
        await request_broadcast_message(query, context)
    elif data == "statistics" and user_id in ADMIN_IDS:
        await show_statistics(query, context)
    elif data == "pending_verifications" and user_id in ADMIN_IDS:
        await show_pending_verifications_list(query, context)
    elif data == "pending_withdrawals" and user_id in ADMIN_IDS:
        await show_pending_withdrawals_list(query, context)
    elif data == "continue_sell":
        await request_phone_number(query, context)
    elif data == "main_menu":
        if user_id in ADMIN_IDS:
            await show_admin_panel(update, context)
        else:
            await show_main_menu(update, context)
    elif data.startswith("request_code_"):
        user_id_to_verify = int(data.split("_")[2])
        await request_verification_code(query, context, user_id_to_verify)
    elif data.startswith("reject_phone_"):
        user_id_to_reject = int(data.split("_")[2])
        await reject_phone_number(query, context, user_id_to_reject)
    elif data.startswith("approve_with_code_"):
        verification_id = int(data.split("_")[3])
        await approve_with_code(query, context, verification_id)
    elif data.startswith("reject_with_code_"):
        verification_id = int(data.split("_")[3])
        await reject_with_code(query, context, verification_id)
    elif data.startswith("approve_withdrawal_"):
        withdrawal_id = int(data.split("_")[2])
        await approve_withdrawal(query, context, withdrawal_id)
    elif data.startswith("reject_withdrawal_"):
        withdrawal_id = int(data.split("_")[2])
        await reject_withdrawal(query, context, withdrawal_id)
    elif data == "confirm_broadcast":
        await send_broadcast(query, context)
    elif data == "cancel_broadcast":
        await cancel_broadcast(query, context)


# Продажа номера
async def show_sell_phone_menu(query, context):
    price = get_current_price()
    keyboard = [
        [InlineKeyboardButton("✅ Продолжить", callback_data="continue_sell")],
        [InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"Сейчас мы покупаем аккаунты по цене: **${price}**\n\n"
        "Нажмите 'Продолжить' чтобы отправить номер телефона:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )


async def request_phone_number(query, context):
    await query.edit_message_text(
        "Пожалуйста, введите номер телефона в международном формате:\n"
        "Пример: +79123456789",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="sell_phone")]])
    )
    context.user_data['waiting_for_phone'] = True


# Профиль пользователя
async def show_user_profile(query, context):
    user = get_user(query.from_user.id)
    if user:
        balance = user[4]
        approved_count = user[5]
        price = get_current_price()

        status_map = {
            'active': 'Активен',
            'pending_verification': 'Ожидает верификации',
            'approved': 'Верифицирован',
            'rejected': 'Отклонен'
        }
        status = status_map.get(user[3], user[3])

        # Проверка доступа к выводу
        can_withdraw = can_user_withdraw(query.from_user.id)
        withdrawal_status = "✅ Доступен" if can_withdraw else f"❌ Недоступен (нужно {MIN_APPROVED_FOR_WITHDRAWAL} одобренных аккаунтов)"

        text = (
            f"**Ваш профиль:**\n"
            f"💰 Баланс: ${balance}\n"
            f"📱 Статус: {status}\n"
            f"✅ Одобрено номеров: {approved_count}/{MIN_APPROVED_FOR_WITHDRAWAL}\n"
            f"💳 Вывод средств: {withdrawal_status}\n"
            f"💵 Цена за аккаунт: ${price}"
        )

        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


# Запрос вывода средств
async def request_withdrawal(query, context):
    user = get_user(query.from_user.id)
    if not user:
        await query.edit_message_text(
            "Ошибка: пользователь не найден.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
        )
        return

    balance = user[4]
    approved_count = user[5]

    # Проверка минимального количества одобренных аккаунтов
    if not can_user_withdraw(query.from_user.id):
        await query.edit_message_text(
            f"❌ Вывод средств доступен после одобрения {MIN_APPROVED_FOR_WITHDRAWAL} аккаунтов.\n\n"
            f"📊 Ваша статистика:\n"
            f"• Одобрено аккаунтов: {approved_count}/{MIN_APPROVED_FOR_WITHDRAWAL}\n"
            f"• Баланс: ${balance}\n\n"
            f"Продолжайте сдавать номера чтобы получить доступ к выводу!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
        )
        return

    if balance > 0:
        await query.edit_message_text(
            f"💰 Ваш баланс: ${balance}\n"
            f"✅ Доступ к выводу: {approved_count}/{MIN_APPROVED_FOR_WITHDRAWAL} аккаунтов\n\n"
            "Введите сумму для вывода и реквизиты в формате:\n"
            "`Сумма Реквизиты`\n\n"
            "Пример: `15 79123456789`",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
        )
        context.user_data['waiting_for_withdrawal'] = True
    else:
        await query.edit_message_text(
            "Недостаточно средств для вывода.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
        )


# Текущая цена
async def show_current_price(query, context):
    price = get_current_price()
    await query.edit_message_text(
        f"💰 Актуальная цена за аккаунт: **${price}**",
        parse_mode='Markdown',
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
    )


# Админ: изменение цены
async def request_new_price(query, context):
    current_price = get_current_price()
    await query.edit_message_text(
        f"Текущая цена: ${current_price}\n\n"
        "Введите новую цену за аккаунт в USD:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
    )
    context.user_data['waiting_for_price'] = True


# Админ: рассылка
async def request_broadcast_message(query, context):
    await query.edit_message_text(
        "Введите сообщение для рассылки:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
    )
    context.user_data['waiting_for_broadcast'] = True


# Админ: статистика
async def show_statistics(query, context):
    stats = get_statistics()
    price = get_current_price()

    text = (
        f"**Статистика бота:**\n"
        f"👥 Всего пользователей: {stats['total_users']}\n"
        f"✅ Одобрено аккаунтов: {stats['approved_users']}\n"
        f"⏳ Ожидают верификации: {stats['pending_verifications']}\n"
        f"💰 Общий баланс: ${stats['total_balance']}\n"
        f"💳 Пользователей с доступом к выводу: {stats['users_with_withdrawal_access']}\n"
        f"💵 Текущая цена: ${price}\n"
        f"🔢 Минимум для вывода: {MIN_APPROVED_FOR_WITHDRAWAL} аккаунтов"
    )

    keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')


# Админ: ожидающие верификации
async def show_pending_verifications_list(query, context):
    verifications = get_pending_verifications()

    if not verifications:
        text = "Нет номеров, ожидающих верификации."
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]]
    else:
        text = "Номера, ожидающие верификации:\n\n"
        keyboard = []

        for verification in verifications:
            verification_id, user_id, phone, status, username = verification
            status_text = "Ожидает код" if status == "waiting_code" else "Код получен"

            text += f"🆔 ID верификации: {verification_id}\n"
            text += f"👤 User: {user_id} (@{username})\n"
            text += f"📱 Номер: {phone}\n"
            text += f"📊 Статус: {status_text}\n"

            if status == "waiting_code":
                request_btn = InlineKeyboardButton(
                    f"🔐 Запросить код {verification_id}",
                    callback_data=f"request_code_{user_id}"
                )
                reject_btn = InlineKeyboardButton(
                    f"❌ Отклонить {verification_id}",
                    callback_data=f"reject_phone_{user_id}"
                )
                keyboard.append([request_btn, reject_btn])
            else:  # code_received
                approve_btn = InlineKeyboardButton(
                    f"✅ Оплатить {verification_id}",
                    callback_data=f"approve_with_code_{verification_id}"
                )
                reject_btn = InlineKeyboardButton(
                    f"❌ Отклонить {verification_id}",
                    callback_data=f"reject_with_code_{verification_id}"
                )
                keyboard.append([approve_btn, reject_btn])

            text += "---\n"

        keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)


# Админ: заявки на вывод
async def show_pending_withdrawals_list(query, context):
    withdrawals = get_pending_withdrawals()

    if not withdrawals:
        text = "Нет заявок на вывод."
        keyboard = [[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]]
    else:
        text = "Заявки на вывод:\n\n"
        keyboard = []

        for withdrawal in withdrawals:
            withdrawal_id, user_id, amount, details, status, created_date = withdrawal
            user = get_user(user_id)
            approved_count = user[5] if user else 0

            text += f"🆔 ID: {withdrawal_id}\n"
            text += f"👤 User: {user_id}\n"
            text += f"💰 Сумма: ${amount}\n"
            text += f"📋 Реквизиты: {details}\n"
            text += f"✅ Одобрено аккаунтов: {approved_count}/{MIN_APPROVED_FOR_WITHDRAWAL}\n\n"

            approve_btn = InlineKeyboardButton(
                f"✅ Выплатить {withdrawal_id}",
                callback_data=f"approve_withdrawal_{withdrawal_id}"
            )
            reject_btn = InlineKeyboardButton(
                f"❌ Отклонить {withdrawal_id}",
                callback_data=f"reject_withdrawal_{withdrawal_id}"
            )
            keyboard.append([approve_btn, reject_btn])
            text += "---\n"

        keyboard.append([InlineKeyboardButton("↩️ Назад", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(text, reply_markup=reply_markup)


# Запрос кода верификации
async def request_verification_code(query, context, user_id_to_verify):
    user = get_user(user_id_to_verify)
    if user:
        phone = user[2]
        create_verification_request(user_id_to_verify, phone, query.from_user.id)

        # Уведомление пользователю
        try:
            await context.bot.send_message(
                user_id_to_verify,
                "🔐 Администратор запросил код подтверждения для вашего номера.\n\n"
                "Пожалуйста, отправьте код подтверждения в ответ на это сообщение."
            )
        except:
            pass  # Пользователь мог заблокировать бота

        await query.edit_message_text(
            f"✅ Запрос кода отправлен пользователю {user_id_to_verify}.\n"
            "Ожидайте получения кода...",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("↩️ Назад", callback_data="pending_verifications")]])
        )


# Отклонение номера
async def reject_phone_number(query, context, user_id_to_reject):
    reject_user_phone(user_id_to_reject)

    # Уведомление пользователя
    try:
        await context.bot.send_message(
            user_id_to_reject,
            "❌ Ваш номер отклонен администратором."
        )
    except:
        pass

    await query.edit_message_text(
        f"Номер пользователя {user_id_to_reject} отклонен.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="pending_verifications")]])
    )


# Одобрение с кодом
async def approve_with_code(query, context, verification_id):
    verification = get_verification_request(verification_id)
    if verification:
        user_id = verification[1]
        approve_user_phone(user_id)
        complete_verification(verification_id, 'approved')
        price = get_current_price()

        # Проверяем, получил ли пользователь доступ к выводу
        user = get_user(user_id)
        approved_count = user[5] if user else 0

        # Уведомление пользователя
        try:
            if approved_count >= MIN_APPROVED_FOR_WITHDRAWAL:
                message = (
                    f"✅ Ваш номер верифицирован! На ваш баланс зачислено ${price}.\n\n"
                    f"🎉 Поздравляем! Вы сдали {approved_count} аккаунтов и получили доступ к выводу средств!\n"
                    f"Теперь вы можете выводить деньги через раздел '💵 Вывод средств'."
                )
            else:
                message = (
                    f"✅ Ваш номер верифицирован! На ваш баланс зачислено ${price}.\n\n"
                    f"📊 Прогресс доступа к выводу: {approved_count}/{MIN_APPROVED_FOR_WITHDRAWAL} аккаунтов\n"
                    f"Осталось сдать {MIN_APPROVED_FOR_WITHDRAWAL - approved_count} аккаунтов для получения доступа к выводу."
                )

            await context.bot.send_message(user_id, message)
        except:
            pass

        await query.edit_message_text(
            f"✅ Номер пользователя {user_id} верифицирован. Баланс пополнен на ${price}.\n"
            f"Всего одобрено аккаунтов: {approved_count}",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("↩️ Назад", callback_data="pending_verifications")]])
        )


# Отклонение с кодом
async def reject_with_code(query, context, verification_id):
    verification = get_verification_request(verification_id)
    if verification:
        user_id = verification[1]
        reject_user_phone(user_id)
        complete_verification(verification_id, 'rejected')

        # Уведомление пользователя
        try:
            await context.bot.send_message(
                user_id,
                "❌ Ваш номер отклонен после проверки кода."
            )
        except:
            pass

        await query.edit_message_text(
            f"Номер пользователя {user_id} отклонен.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("↩️ Назад", callback_data="pending_verifications")]])
        )


# Одобрение выплаты
async def approve_withdrawal(query, context, withdrawal_id):
    update_withdrawal_status(withdrawal_id, 'approved')

    # Получаем информацию о выплате для уведомления пользователя
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount FROM withdrawals WHERE id = ?", (withdrawal_id,))
    result = cursor.fetchone()
    conn.close()

    if result:
        user_id, amount = result

        # Уведомление пользователя
        try:
            await context.bot.send_message(
                user_id,
                f"✅ Ваша заявка на вывод ${amount} одобрена и выплачена!\n\n"
                f"Спасибо за сотрудничество! 🎉"
            )
        except:
            pass

    await query.edit_message_text(
        f"✅ Заявка на вывод #{withdrawal_id} одобрена и выплачена.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="pending_withdrawals")]])
    )


# Отклонение выплаты
async def reject_withdrawal(query, context, withdrawal_id):
    update_withdrawal_status(withdrawal_id, 'rejected')

    # Получаем информацию о выплате для уведомления пользователя
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, amount FROM withdrawals WHERE id = ?", (withdrawal_id,))
    result = cursor.fetchone()
    conn.close()

    if result:
        user_id, amount = result

        # Уведомление пользователя
        try:
            await context.bot.send_message(
                user_id,
                f"❌ Ваша заявка на вывод ${amount} отклонена.\n\n"
                f"По вопросам обращайтесь к администратору."
            )
        except:
            pass

    await query.edit_message_text(
        f"Заявка на вывод #{withdrawal_id} отклонена.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="pending_withdrawals")]])
    )


# Отправка рассылки
async def send_broadcast(query, context):
    broadcast_text = context.user_data.get('broadcast_text')
    users = get_all_users()
    successful = 0

    for user_id in users:
        try:
            await context.bot.send_message(user_id, broadcast_text)
            successful += 1
        except:
            continue

    # Сохранение в историю рассылок
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO broadcasts (message_text, users_reached) VALUES (?, ?)",
        (broadcast_text, successful)
    )
    conn.commit()
    conn.close()

    await query.edit_message_text(
        f"✅ Рассылка завершена!\nДоставлено: {successful} пользователей",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
    )


# Отмена рассылки
async def cancel_broadcast(query, context):
    if 'broadcast_text' in context.user_data:
        del context.user_data['broadcast_text']

    await query.edit_message_text(
        "❌ Рассылка отменена.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Назад", callback_data="main_menu")]])
    )


# Обработка текстовых сообщений
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text

    # Обработка кода подтверждения от пользователя
    if not await handle_verification_code(update, context, user_id, text):
        return

    if context.user_data.get('waiting_for_phone'):
        # Простая валидация номера
        if text.startswith('+') and len(text) > 5:
            update_user_phone(user_id, text)
            await update.message.reply_text(
                "✅ Номер принят и ожидает проверки администратором.",
                reply_markup=ReplyKeyboardRemove()
            )

            # Уведомление админов
            for admin_id in ADMIN_IDS:
                try:
                    keyboard = [
                        [
                            InlineKeyboardButton("🔐 Запросить код", callback_data=f"request_code_{user_id}"),
                            InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_phone_{user_id}")
                        ]
                    ]
                    reply_markup = InlineKeyboardMarkup(keyboard)

                    await context.bot.send_message(
                        admin_id,
                        f"📱 Новый номер для проверки:\n"
                        f"👤 User ID: {user_id}\n"
                        f"👤 Username: @{update.effective_user.username}\n"
                        f"📞 Номер: {text}",
                        reply_markup=reply_markup
                    )
                except:
                    continue

            context.user_data.pop('waiting_for_phone', None)
            await show_main_menu(update, context)
        else:
            await update.message.reply_text(
                "❌ Неверный формат номера. Используйте международный формат (например: +79123456789):"
            )

    elif context.user_data.get('waiting_for_withdrawal'):
        try:
            parts = text.split(' ', 1)
            amount = float(parts[0])
            details = parts[1] if len(parts) > 1 else ""

            user = get_user(user_id)
            if user and user[4] >= amount:
                # Дополнительная проверка доступа к выводу
                if not can_user_withdraw(user_id):
                    await update.message.reply_text(
                        f"❌ Вывод средств доступен после одобрения {MIN_APPROVED_FOR_WITHDRAWAL} аккаунтов.\n"
                        f"У вас одобрено: {user[5]}/{MIN_APPROVED_FOR_WITHDRAWAL}",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    context.user_data.pop('waiting_for_withdrawal', None)
                    await show_main_menu(update, context)
                    return

                create_withdrawal(user_id, amount, details)
                await update.message.reply_text(
                    f"✅ Заявка на вывод ${amount} создана и ожидает обработки.",
                    reply_markup=ReplyKeyboardRemove()
                )

                # Уведомление админов
                for admin_id in ADMIN_IDS:
                    try:
                        pending_withdrawals = get_pending_withdrawals()
                        if pending_withdrawals:
                            latest_withdrawal_id = pending_withdrawals[-1][0]

                            keyboard = [
                                [
                                    InlineKeyboardButton("✅ Выплатить",
                                                         callback_data=f"approve_withdrawal_{latest_withdrawal_id}"),
                                    InlineKeyboardButton("❌ Отклонить",
                                                         callback_data=f"reject_withdrawal_{latest_withdrawal_id}")
                                ]
                            ]
                            reply_markup = InlineKeyboardMarkup(keyboard)

                            await context.bot.send_message(
                                admin_id,
                                f"💸 Новая заявка на вывод:\n"
                                f"👤 User ID: {user_id}\n"
                                f"✅ Одобрено аккаунтов: {user[5]}/{MIN_APPROVED_FOR_WITHDRAWAL}\n"
                                f"💰 Сумма: ${amount}\n"
                                f"📋 Реквизиты: {details}",
                                reply_markup=reply_markup
                            )
                    except:
                        continue
            else:
                await update.message.reply_text("❌ Недостаточно средств.")

            context.user_data.pop('waiting_for_withdrawal', None)
            await show_main_menu(update, context)

        except (ValueError, IndexError):
            await update.message.reply_text(
                "❌ Неверный формат. Используйте: `Сумма Реквизиты`\nПример: `15 79123456789`",
                parse_mode='Markdown'
            )

    elif context.user_data.get('waiting_for_price') and user_id in ADMIN_IDS:
        try:
            new_price = float(text)
            set_current_price(new_price)
            await update.message.reply_text(
                f"✅ Цена успешно изменена на ${new_price}",
                reply_markup=ReplyKeyboardRemove()
            )
            context.user_data.pop('waiting_for_price', None)
            await show_admin_panel(update, context)
        except ValueError:
            await update.message.reply_text("❌ Введите корректное число:")

    elif context.user_data.get('waiting_for_broadcast') and user_id in ADMIN_IDS:
        context.user_data['broadcast_text'] = text

        keyboard = [
            [
                InlineKeyboardButton("✅ Разослать", callback_data="confirm_broadcast"),
                InlineKeyboardButton("❌ Отменить", callback_data="cancel_broadcast")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"Предпросмотр рассылки:\n\n{text}\n\n",
            reply_markup=reply_markup
        )
        context.user_data.pop('waiting_for_broadcast', None)


# Обработка кодов верификации от пользователей
async def handle_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int, text: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()

    # Ищем активную верификацию для этого пользователя
    cursor.execute('''
        SELECT v.id, v.admin_id, v.phone 
        FROM verifications v 
        WHERE v.user_id = ? AND v.status = 'waiting_code'
    ''', (user_id,))

    verification = cursor.fetchone()
    conn.close()

    if verification:
        verification_id, admin_id, phone = verification

        # Сохраняем код
        update_verification_code(verification_id, text)

        # Уведомляем администратора
        try:
            keyboard = [
                [
                    InlineKeyboardButton("✅ Оплатить", callback_data=f"approve_with_code_{verification_id}"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"reject_with_code_{verification_id}")
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await context.bot.send_message(
                admin_id,
                f"🔐 Получен код подтверждения:\n"
                f"👤 User ID: {user_id}\n"
                f"📱 Номер: {phone}\n"
                f"🔢 Код: {text}\n\n"
                f"Проверьте код и примите решение:",
                reply_markup=reply_markup
            )
        except:
            pass

        await update.message.reply_text(
            "✅ Код подтверждения отправлен администратору. Ожидайте решения.",
            reply_markup=ReplyKeyboardRemove()
        )
        return False

    return True


# Команда /admin
async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id in ADMIN_IDS:
        await show_admin_panel(update, context)
    else:
        await update.message.reply_text("У вас нет прав доступа к этой команде.")


# Основная функция
def main():
    init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    # Обработчики команд
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_command))

    # Обработчики кнопок
    application.add_handler(CallbackQueryHandler(handle_button_click))

    # Обработчики сообщений
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Запуск бота
    application.run_polling()


if __name__ == "__main__":
    main()