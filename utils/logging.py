import traceback
from datetime import datetime

from typing import Final

LOG_LEVEL_ERROR  : Final[int] = 0
LOG_LEVEL_DEFAULT: Final[int] = 1
LOG_LEVEL_VERBOSE: Final[int] = 2

gLogLevel:int = LOG_LEVEL_DEFAULT

def setLogLevel(level:int) -> int:
    """Sets gLogLevel to `level` and returns the previously set level"""

    global gLogLevel
    oldLevel = gLogLevel
    gLogLevel = level

    log(f"Changed gLogLevel from '{oldLevel}' to '{gLogLevel}'", logLevel=LOG_LEVEL_VERBOSE)

    return oldLevel

def log(msg, prefix:str="MSG", logLevel:int=LOG_LEVEL_DEFAULT) -> None:

    if gLogLevel >= logLevel:

        timeStr = datetime.now().strftime("%H:%M:%S:%f") 
    
        msgStart = f"{timeStr} -- {prefix}[{logLevel}]: "
        msgBody = str(msg).replace("\n", "\n"+" "*len(msgStart))    
    
        print(msgStart + msgBody)

def panic(msg) -> None:
    log(msg, "PANIC", logLevel=LOG_LEVEL_ERROR)
    traceback.print_stack()
    exit(1)

def warn(msg) -> None:
    log(msg, prefix="WARN", logLevel=LOG_LEVEL_ERROR)

def error(msg) -> None:
    log(msg, prefix="ERROR", logLevel=LOG_LEVEL_ERROR)    