import sqlite3
from datetime import datetime
import csv
import matplotlib.pyplot as plt
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram.constants import ParseMode  # Import ParseMode for formatting

# Connect to the SQLite database
conn = sqlite3.connect('transactions.db')
c = conn.cursor()

# Ensure that the users table exists to store user chat IDs
c.execute('''CREATE TABLE IF NOT EXISTS users
             (chat_id INTEGER PRIMARY KEY)''')
conn.commit()

# Function to add a user to the users table
def add_user(chat_id):
    c.execute("INSERT OR IGNORE INTO users (chat_id) VALUES (?)", (chat_id,))
    conn.commit()

# Option 1: Add category column if it doesn't exist
try:
    c.execute('ALTER TABLE transactions ADD COLUMN category TEXT')
except sqlite3.OperationalError:
    pass  # Ignore if the column already exists

conn.commit()

# Handle messages with numbers that start with + or -
async def handle_message(update: Update, context):
    text = update.message.text.strip()
    chat_id = update.message.chat.id  # Use the chat ID (can be group or individual)

    # Add the user to the users list if they interact with the bot
    add_user(chat_id)

    # Check if the message starts with + or -
    if text.startswith('+') or text.startswith('-'):
        try:
            # If the message contains a multiplication, evaluate it
            if '*' in text:
                sign = text[0]  # Get the + or - sign
                expression = text[1:].split('*')  # Get the numbers to multiply (ignore the first character)
                
                if len(expression) == 2:
                    operand1 = float(expression[0].strip())  # First number
                    operand2 = float(expression[1].strip())  # Second number
                    result = operand1 * operand2  # Perform multiplication
                    
                    # Apply the sign to the result
                    amount = result if sign == '+' else -result
                else:
                    raise ValueError("Invalid multiplication expression")
            else:
                # Handle simple + or - numbers
                amount = float(text)

            date = datetime.now().strftime("%Y-%m-%d")
            category = "general"  # Default category

            # Insert the number along with the chat_id into the database
            c.execute('INSERT INTO transactions (amount, date, category, chat_id) VALUES (?, ?, ?, ?)', (amount, date, category, chat_id))
            conn.commit()

            # Get the total amount for the specific chat
            c.execute('SELECT SUM(amount) FROM transactions WHERE chat_id = ?', (chat_id,))
            total = c.fetchone()[0]

            await update.message.reply_text(f"Amount added: {amount}\nYour current total: {total}")
        except ValueError:
            pass
    else:
        pass

# Set custom report time
async def set_report_time(update: Update, context):
    user_id = update.message.chat.id
    if len(context.args) == 1:
        try:
            time = context.args[0]
            hour, minute = map(int, time.split(":"))
            user_report_times[user_id] = (hour, minute)
            await update.message.reply_text(f"Your report time is set to {time}.")
        except:
            await update.message.reply_text("Invalid time format. Please use HH:MM.")
    else:
        await update.message.reply_text("Please provide the time in HH:MM format.")

# Send daily report with totals for all chats
async def send_daily_report(context):
    user_ids = list(user_report_times.keys())

    for user_id in user_ids:
        chat_report = []
        chats = c.execute("SELECT DISTINCT chat_id FROM transactions WHERE chat_id != ?", (user_id,)).fetchall()

        for chat in chats:
            chat_id = chat[0]
            c.execute("SELECT SUM(amount) FROM transactions WHERE chat_id = ?", (chat_id,))
            total = c.fetchone()[0]
            if total is None:
                total = 0
            
            try:
                chat_obj = await context.bot.get_chat(chat_id)
                chat_name = chat_obj.title or chat_obj.username or str(chat_id)
            except:
                chat_name = f"Chat ID: {chat_id}"

            chat_report.append(f"{chat_name} (ID: {chat_id}) - Total: {total}")

        if chat_report:
            report_message = "Daily Report of Transactions Across Chats:\n\n" + "\n".join(chat_report)
        else:
            report_message = "No transactions found for today."

        await context.bot.send_message(chat_id=user_id, text=report_message)

# Export transactions as CSV
async def export_transactions(update: Update, context):
    user_id = update.message.chat.id
    transactions = c.execute("SELECT * FROM transactions WHERE chat_id = ?", (user_id,)).fetchall()

    # Write to a CSV file
    with open(f'transactions_{user_id}.csv', 'w', newline='') as file:
        writer = csv.writer(file)
        writer.writerow(["ID", "Amount", "Date", "Category", "Chat ID"])
        writer.writerows(transactions)
    
    # Send the file to the user
    await context.bot.send_document(chat_id=user_id, document=open(f'transactions_{user_id}.csv', 'rb'))

# Generate graphical report (matplotlib)
async def send_graph(update: Update, context):
    user_id = update.message.chat.id
    transactions = c.execute("SELECT date, SUM(amount) FROM transactions WHERE chat_id = ? GROUP BY date", (user_id,)).fetchall()

    dates = [row[0] for row in transactions]
    totals = [row[1] for row in transactions]

    plt.plot(dates, totals)
    plt.title('Transaction History')
    plt.xlabel('Date')
    plt.ylabel('Total Amount')

    plt.savefig('transaction_graph.png')
    await context.bot.send_photo(chat_id=user_id, photo=open('transaction_graph.png', 'rb'))

# Reset user transactions
async def reset_transactions(update: Update, context):
    user_id = update.message.chat.id
    c.execute('DELETE FROM transactions WHERE chat_id = ?', (user_id,))
    conn.commit()
    await update.message.reply_text("All your transactions have been reset.")

# Broadcast message to all users with Markdown formatting support
async def broadcast_message(update: Update, context):
    if context.args:
        message = " ".join(context.args)
        users = c.execute("SELECT chat_id FROM users").fetchall()
        failed_count = 0
        for user in users:
            try:
                # Send message with Markdown formatting
                await context.bot.send_message(chat_id=user[0], text=message, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                failed_count += 1  # Track failed deliveries

        success_count = len(users) - failed_count
        await update.message.reply_text(f"Broadcast sent to {success_count} users.\nFailed to send to {failed_count} users.")
    else:
        await update.message.reply_text("Please provide a message to broadcast.")

# Summary of all transactions across all chats
async def summary(update: Update, context):
    admin_chat_id = update.message.chat.id
    summary_report = []
    chats = c.execute("SELECT DISTINCT chat_id FROM transactions").fetchall()

    for chat in chats:
        chat_id = chat[0]
        c.execute("SELECT SUM(amount) FROM transactions WHERE chat_id = ?", (chat_id,))
        total = c.fetchone()[0]
        if total is None:
            total = 0

        try:
            chat_obj = await context.bot.get_chat(chat_id)
            chat_name = chat_obj.title or chat_obj.username or str(chat_id)
        except:
            chat_name = f"Chat ID: {chat_id}"

        summary_report.append(f"{chat_name} (ID: {chat_id}) - Total: {total}")

    if summary_report:
        summary_message = "Summary of Transactions Across All Chats:\n\n" + "\n".join(summary_report)
    else:
        summary_message = "No transactions found across all chats."

    # Send the summary report to the admin (your private chat)
    await context.bot.send_message(chat_id=admin_chat_id, text=summary_message)

# Help command listing available commands
async def helpme(update: Update, context):
    help_text = (
        "/start - Start the bot\n"
        "/setreporttime HH:MM - Set daily report time\n"
        "/export - Export your transactions as a CSV file\n"
        "/graph - Get a graphical report of your transactions\n"
        "/reset - Reset all your transactions\n"
        "/broadcast [message] - Send a broadcast message (admin only)\n"
        "/summary - Get a summary of all transactions across all chats\n"
        "/helpme - Display this help message"
    )
    await update.message.reply_text(help_text)

# Start command handler
async def start(update: Update, context):
    await update.message.reply_text("Welcome! Send me a number with + or -, and I'll track it for this chat. If you need any help, just type /helpme")

def main():
    # Create the application
    application = Application.builder().token('7457442840:AAG5ioBPW415GnIasz5oWPmDrDphGunImoY').build()

    # Add command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("setreporttime", set_report_time))  # /setreporttime HH:MM
    application.add_handler(CommandHandler("export", export_transactions))  # /export
    application.add_handler(CommandHandler("graph", send_graph))  # /graph
    application.add_handler(CommandHandler("reset", reset_transactions))  # /reset
    application.add_handler(CommandHandler("broadcast", broadcast_message))  # /broadcast Your message
    application.add_handler(CommandHandler("helpme", helpme))  # /helpme
    application.add_handler(CommandHandler("summary", summary))  # /summary

    # Add a message handler
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Schedule daily report to be sent at custom times
    scheduler = AsyncIOScheduler()
    scheduler.add_job(send_daily_report, 'cron', hour=0, minute=0, args=[application])  # Default time
    scheduler.start()

    # Run the bot and start polling for updates
    application.run_polling()

if __name__ == '__main__':
    main()
