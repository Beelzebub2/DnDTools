import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from src.models.appdirs import get_appdata_dir, is_frozen

def get_logs_dir():
    """Get or create logs directory in AppData"""
    logs_dir = os.path.join(get_appdata_dir(), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir

def setup_logging(level=logging.INFO):
    """
    Set up logging for the application.
    - When running as an executable, logs to AppData/Local/DnDTools/logs/
    - Configures console and file handlers with different log levels
    - Implements log rotation to manage file sizes
    """
    # Get root logger
    root_logger = logging.getLogger()
    # Clear any existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)
    
    root_logger.setLevel(level)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler for development - less verbose
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Always log to file when in executable mode
    if is_frozen():
        logs_dir = get_logs_dir()
        log_file = os.path.join(logs_dir, f'dndtools_{datetime.now().strftime("%Y%m%d")}.log')
        
        # Set up rotating file handler - 5MB per file, keep 5 backup files
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.INFO)  # Logs INFO and above to file
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
        
        # Redirect standard output and error to log file in executable mode
        # This ensures that print statements are also captured
        sys.stdout = LoggerWriteAdapter(logging.getLogger('stdout'), logging.INFO)
        sys.stderr = LoggerWriteAdapter(logging.getLogger('stderr'), logging.ERROR)
        
        logging.info(f"Logging to file: {log_file}")
    
    # Create specialized loggers
    setup_specialized_loggers()
    
    return root_logger

def setup_specialized_loggers():
    """Set up specialized loggers for different components"""
    # Network capture logging - more verbose
    capture_logger = logging.getLogger('src.models.capture')
    capture_logger.setLevel(logging.INFO)
    
    # Data processing logging
    data_logger = logging.getLogger('src.models.stash_manager')
    data_logger.setLevel(logging.INFO)
    
    # UI/Flask logging - less verbose
    flask_logger = logging.getLogger('werkzeug')
    flask_logger.setLevel(logging.WARNING)  # Reduce Flask logging noise

class LoggerWriteAdapter:
    """Adapter to make a logger act like a file object for redirecting stdout/stderr"""
    def __init__(self, logger, level):
        self.logger = logger
        self.level = level
        self.buffer = ''

    def write(self, message):
        if message and message.strip() and not message.isspace():
            self.logger.log(self.level, message.rstrip())
            
    def flush(self):
        pass

if __name__ == "__main__":
    # Test logging
    setup_logging()
    logging.debug("This is a debug message")
    logging.info("This is an info message")
    logging.warning("This is a warning message")
    logging.error("This is an error message")
    logging.critical("This is a critical message")