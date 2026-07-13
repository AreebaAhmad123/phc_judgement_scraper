"""Logging configuration, shared by the CLI and the scheduler."""

# tracks events that happen when some software runs. 
import logging
#While the standard logging module decides what information to capture, logging.handlers determines where that information is sent.File Rotation: Keeps log files from growing too large by splitting or overwriting them automatically.
from logging.handlers import RotatingFileHandler
# for os related functions like directory changing and command line commands running, differ between sys and os is od is related to computer physical environement and sys is related to python interpreter.
import os

#imports config.py from same folder
from . import config

# Retrieve a unique logger for the php scraper module. This logger will be used to log messages related to the scraper's activities. The name "phc_scraper" is used to identify this specific logger, allowing for better organization and filtering of log messages.
logger = logging.getLogger("phc_scraper")


#Defines the function. It accepts an optional parameter called level which defaults to logging.INFO. This means by default, it will capture standard progress updates, warnings, and errors, but ignore deep debugging spam (logging.DEBUG).
def configure_logging(level=logging.INFO):
    #A StreamHandler sends the logs to your terminal screen.

    #A RotatingFileHandler sends the logs to a text file.
    #first time log will be created as logger.handlers list is false and then next time it will be true and it will return the logger object.
    if logger.handlers:
        return logger
    #sets the logger level to basic info not deep down debuging. This means that only messages at this level or higher (like warnings and errors) will be captured.
    logger.setLevel(level)
    # defines format of logging meassage acstime--> timestamp of the log entry, -8s --> 8 spaces, levelname--> severity of the log entry, message--> actual log message content.
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s")
    # sets the stream handler so that it print logs on terminal screen. its formater to fmt and adds it to the logger object.
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)

    # sets the file handler so that it print logs on log file using rotatingfilehandler. sets the location as in congif's LOG_DIR and file name as phc_scraper.log. maxBytes=5 * 1024 * 1024 --> 5MB --> if exceeds 5MB it will make a new file , backupCount=5 --> keeps 5 old log files and if exceeds 5 files it will delete the oldest one, encoding="utf-8" --> ensures that the log file can handle a wide range of characters.

    file_handler = RotatingFileHandler(
        os.path.join(config.LOG_DIR, "phc_scraper.log"),
        maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8",
    )

    # # sets the file formater to fmt and adds it to the logger object.
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    return logger
