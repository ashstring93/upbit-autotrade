import logging
from logging.handlers import RotatingFileHandler
import config

log_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('TradingBot')
logger.setLevel(logging.INFO)

if not logger.handlers:
    file_handler = RotatingFileHandler(config.LOG_FILE, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8')
    file_handler.setFormatter(log_formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(log_formatter)
    logger.addHandler(console_handler)