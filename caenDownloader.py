import os

from datetime import datetime

from utils.Leccap import Leccap

from utils.ArgParser import *
from utils.logging import *
from utils.parse import *

def main():
    
    # TODO: Add support for matching course name and/or recording titles with regex
    # TODO: add ability to download captions if available and convert the to srt file saved alongside video
    # TODO: add ability to download thumbnails? why would we want this
    # TODO: figure out waveform.audiomap is used for ... just looks like a binary blob
    # TODO: have --update option that only downloads files if it doesn't already exist in output directory

    class MainArgs(Args):        
        dir            = Arg(longName="--dir",              metavar="str",  type=str,   default="./recordings",          help=f"Specifies the directory to output downloaded recordings to.")
        listCourses    = Arg(longName="--listCourses",      action="store_true",        default=False,                   help=f"Lists available courses to download and exits")
        listRecordings = Arg(longName="--listRecordings",   action="store_true",        default=False,                   help=f"Lists available recordings to download and exits")
        start          = Arg(longName="--start",            metavar="int",  type=int,   default=Leccap.kMinYear, help=f"Specifies the year to start parsing courses.")
        stop           = Arg(longName="--stop",             metavar="int",  type=int,   default=datetime.today().year,   help=f"Specifies the year to stop parsing courses.")
        threads        = Arg(longName="--threads",          metavar="int",  type=int,   default=min(12, os.cpu_count()), help=f"Specifies the number of threads to use while downloading.")
        verbose        = Arg(longName="--verbose",          metavar="int",  type=int,   default=LogLevel.Default,        help=f"Specifies the verbose log level. Larger values enable more verbose output. Log Levels: {LogLevel.getMapping()}")

    argParser = ArgParser(
        description = "A lightweight python utility for downloading recorded CAEN leccap lectures from the University of Michigan."        
    )

    args = argParser.Parse(MainArgs())

    setLogLevel(args.verbose.value)

    argStr = "\n".join([f"\t{name} [{type(arg.value)}] = '{arg.value}'" for name, arg in args.ArgDict().items()])
    log(f"Using args: {{\n{argStr}\n}}", logLevel=LogLevel.Verbose)

    dirPath    = args.dir.value
    startYear  = args.start.value
    stopYear   = args.stop.value
    numThreads = args.threads.value

    if numThreads < 1:
        warn(f"Invalid numThreads '{numThreads}'. Clamping numThreads to 1")
        numThreads = 1

    if startYear < Leccap.kMinYear:
        warn(f"Invalid startYear: {startYear}, clamping to {Leccap.kMinYear}")
        startYear = Leccap.kMinYear 

    if stopYear > Leccap.kMaxYear:
        warn(f"Invalid stopYear: {stopYear}, clamping to {Leccap.kMaxYear}")
        stopYear = Leccap.kMaxYear    

    leccap = Leccap(numThreads=numThreads)
    leccap.login()
    
    if args.listCourses.value:

        if args.listRecordings.value:
            warn(f"Ignoring '{args.listRecordings.longName}'")
        
        leccap.listCourses(startYear=startYear, stopYear=stopYear)
        exit()

    if args.listRecordings.value:
        leccap.listRecordings(startYear=startYear, stopYear=stopYear)
        exit()

    leccap.downloadCourses(
        startYear = startYear, 
        stopYear  = stopYear,
        dir       = dirPath,
    )

if __name__ == "__main__":
    main()