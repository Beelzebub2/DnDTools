import logging
import os
import sys
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from src.models.appdirs import get_appdata_dir, is_frozen

def get_logs_dir():
    """Get or create logs directory in AppData"""
    logs_dir = os.path.join(get_appdata_dir(), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    return logs_dir

def cleanup_old_logs(logs_dir, keep_days=3):
    """Delete logs older than keep_days from current date"""
    try:
        cutoff_date = datetime.now().date() - timedelta(days=keep_days)
        
        if not os.path.exists(logs_dir):
            return

        for f in os.listdir(logs_dir):
            file_path = os.path.join(logs_dir, f)
            if not os.path.isfile(file_path):
                continue
            
            file_date = None
            name_lower = f.lower()
            
            # Format 1: dndtools_YYYYMMDD.log (Legacy)
            if name_lower.startswith('dndtools_') and '-' not in name_lower:
                try:
                    # dndtools_20251126.log or dndtools_20251126.log.1
                    parts = f.split('_')
                    if len(parts) >= 2:
                        date_str = parts[1].split('.')[0]
                        if len(date_str) == 8 and date_str.isdigit():
                            file_date = datetime.strptime(date_str, "%Y%m%d").date()
                except (IndexError, ValueError):
                    pass

            # Format 2: DnDTools_YYYY-MM-DD.log (New)
            elif name_lower.startswith('dndtools_') and '-' in name_lower:
                try:
                    # DnDTools_2025-11-26.log
                    parts = f.split('_')
                    if len(parts) >= 2:
                        date_str = parts[1].split('.')[0]
                        # Basic validation for YYYY-MM-DD
                        if len(date_str) == 10 and date_str.count('-') == 2:
                            file_date = datetime.strptime(date_str, "%Y-%m-%d").date()
                except (IndexError, ValueError):
                    pass
            
            if file_date and file_date < cutoff_date:
                try:
                    os.remove(file_path)
                except OSError:
                    pass
    except Exception:
        pass

def setup_logging(level=logging.WARNING):
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
    
    # Console handler for development
    console_handler = logging.StreamHandler()
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
    
    # Cleanup logs if directory exists (regardless of frozen state)
    try:
        logs_dir = get_logs_dir()
        if os.path.exists(logs_dir):
            cleanup_old_logs(logs_dir, keep_days=3)
    except Exception:
        pass
    
    # Always log to file when in executable mode
    if is_frozen():
        logs_dir = get_logs_dir()
        
        # Use new human readable format
        log_file = os.path.join(logs_dir, f'DnDTools_{datetime.now().strftime("%Y-%m-%d")}.log')
        
        # Set up rotating file handler - 5MB per file, keep 5 backup files
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=5 * 1024 * 1024,  # 5 MB
            backupCount=5,
            encoding='utf-8'
        )
        file_handler.setLevel(level)  # Logs based on requested level
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


def set_logging_level(level: int) -> None:
    """Update the global log level for all root handlers."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    for handler in root_logger.handlers:
        try:
            handler.setLevel(level)
        except Exception:
            continue

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