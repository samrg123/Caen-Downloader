import concurrent
import concurrent.futures
import html
import os
import re
import requests
import traceback
import urllib.parse

from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from pwinput import pwinput
from typing import Final

from utils.DuoForm import DuoForm
from utils.HtmlForm import HtmlForm
from utils.io.ThreadedStdOut import ThreadedStdOut

from utils.parse import *
from utils.logging import *


class LeccapException(Exception):
    def __init__(self, message:str) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        return f"LeccapException Exception - {super().__str__()}"    

class Leccap():

    kMinYear: Final = 2006
    kMaxYear: Final = datetime.now().year

    kLeccapBaseUrl: Final = "https://leccap.engin.umich.edu/leccap/"
    kApiUrl: Final = "https://leccap.engin.umich.edu/leccap/player/api/product/"

    kDefaultSanitizeSymbol: Final = ""
    kDownloadChunkSize: Final = 1*1024*1024 

    loggedIn:bool = False
    
    # TODO: pull out dataclasses to make Leccap more readable?
    # Note: marking dataclass as eq and frozen enables hashing
    @dataclass(eq=True, frozen=True)
    class CourseAnchor:
        year: int
        text: str
        href: str

    @dataclass(eq=True, frozen=True)
    class Recording():
        url: str
        title: str
        date: str
        timestamp: int
        fileUnder: str | None
        description: str | None
        captions: bool


    @dataclass(eq=True, frozen=True)
    class RecordingProduct():
        movie_exported_video_layout: str
        audio_waveform_image: str | None
        grind_time_total: str
        movie_length_as_recorded: str
        audio_detect_end: str | None
        audio_detected: str | None
        movie_exported_width: str
        movie_exported_slides_present: str
        force_lecture_copy_complete: str
        slides_folder: str
        movie_exported_duration: str
        movie_exported_video_left: str
        movie_exported_video_width: str
        grinder_version: int
        movie_exported_filesize: str
        product_total_filesize: str
        movie_type: str
        preservation_movie_name: str | None
        grinder_build: int
        movie_exported_name: str
        audio_waveform_map: str
        movie_exported_video_top: str
        grinder_sha_short: str
        movie_exported_audio_bitrate_kbps: str
        movie_exported_height: str
        audio_detect_start: str | None
        movie_exported_video_height: str
        audio_length: str | None
        movie_exported_video_present: str
        slide_count: str | None
        movie_exported_visual_bitrate_kbps: str
        has_content_motion: str
        thumbnail_count: str | None
        movie_exported_slides_left: str | None
        movie_exported_slides_width: str | None
        movie_exported_slides_height: str | None
        movie_exported_slides_top: str | None
        used_content_motion: str | None
        used_movie_visual_bitrate_fallback: str | None
        viewer_name: str
        codec_id: int
        codec_order: int
        codec_description: str
        raw_total_filesize: str | None = None
        preservation_slides_size: str | None = None
        movie_audio_channel_count: str | None = None

    @dataclass(eq=True, frozen=True)
    class RecordingCaption():
        text: str
        intime: float | int
        outtime: float | int

    @dataclass(eq=True, frozen=True)
    class RecordingAuxSource():
        kind: str
        prefix: str
        name: str
        thumbnails: list[list[int]]
        images: list[list[str | int]]
        width: str
        height: str
        folder: str
        thumbWidth: int
        thumbHeight: int

    @dataclass(eq=True, frozen=True)
    class RecordingProductInfo():
        products: list["Leccap.RecordingProduct"]
        aux_sources: list["Leccap.RecordingAuxSource"] | None
        captions: list["Leccap.RecordingCaption"] | str
        cid: int
        words: list[str]
        words_reviewed: bool
        thumbnails_folder: str | None = None
        thumbnails: list[list[int]] | None = None

    @dataclass(eq=True, frozen=True)
    class RecordingInfo():
        id: int
        title: str
        date: str
        sitekey: str
        sitename: str
        orgLogo: str | None
        enable_playlist: int
        show_site_title: int
        recordingkey: str
        description: str
        statsURL: str
        error: str | None
        published: bool
        sendPostInterval: int
        canManage: bool
        viewerUID: str
        mediaPrefix: str
        info: "Leccap.RecordingProductInfo"

        def __post_init__(self) -> None:
            numRecordingProducts = len(self.info.products)
            if numRecordingProducts != 1:
                raise LeccapException(f"Expected 1 recording product for '{self.title}', got '{numRecordingProducts}'")

        def getProduct(self) -> "Leccap.RecordingProduct":
            return self.info.products[0]

    class DebugSession(requests.Session):
        def __init__(self) -> None:
            super().__init__()

            self.request = self.debugRequest

        def debugRequest(self, method, url, **kwargs):

            response = super().request(method, url, **kwargs)
            log(f"Request: {method} @ '{url}' | [ {kwargs.get('data')} ] -> '{response.url}'", logLevel=LogLevel.Verbose)

            return response

    def __init__(self, numThreads:int) -> None:
        self.session = Leccap.DebugSession()
        self.threadPool = ThreadPoolExecutor(max_workers=numThreads)
        

    def login(self, maxLoginAttempts:int = 3) -> None:

        for i in range(1, maxLoginAttempts+1):

            print(f"login attempt {i}/{maxLoginAttempts}")

            uniqueId = input(f"UMich uniqueId: ")
            password = pwinput(f"UMich password: ", mask="*")
            print("")

            # TODO: detect if username / password is wrong here (IE we get an error message response?)
            #       right now we are just relying on catching a ParseException 
            try:

                # SAML login
                # TODO: should we use a separate login url here? (https://wolverineaccess.umich.edu/authenticate)
                response = self.session.get(self.kLeccapBaseUrl)
                samlRequest = HtmlForm.parseResponse(response, requiredInputs=["RelayState", "SAMLRequest"])
                response = samlRequest.submit(self.session)

                loginPayload = {
                    "j_username": uniqueId,
                    "j_password": password
                }
                samlResponse = HtmlForm.parseResponse(response, requiredInputs=loginPayload.keys())
                response = samlResponse.submit(self.session, loginPayload)

                # try to parse duo form
                duoForm = DuoForm.parseResponse(response)

            except ParseException as e:
                traceback.print_exc()
                print(f"Encounter unexpected/malformed response from server | Exception: '{e}'"
                       "\n--------\n")
                continue

            response = duoForm.login(self.session, maxLoginAttempts=maxLoginAttempts)
            if not response:
                print(f"Failed to complete duo dual factor authentication")
                continue

            print(f"Login success!")

            # SAML redirect to original requested page
            samlResponse = HtmlForm.parseResponse(response, requiredInputs=["SAMLResponse"])
            response = samlResponse.submit(self.session)

            self.loggedIn = True
            return 

        raise LeccapException(f"Failed to login")
    
    
    def getCourseAnchors(self, year) -> list[CourseAnchor]:

        if year < Leccap.kMinYear or year > Leccap.kMaxYear:
            raise LeccapException(f"Invalid course year '{year}'. Valid Range [{Leccap.kMinYear}, {Leccap.kMaxYear}]") 

        url = urllib.parse.urljoin(Leccap.kLeccapBaseUrl, str(year))
        response = self.session.get(url)
        
        anchorSoups = parseHtmlElements(response.text, "a", attrs={"class": "list-group-item"}, requiredAttributes=["href"])
        anchors = [ 
            Leccap.CourseAnchor(
                year = year,
                text = soup.text,
                href = soup.attrs["href"]
            ) 
            for soup in anchorSoups 
        ]

        return anchors

    def getRecordings(self, courseAnchor:CourseAnchor) -> list[Recording]:

        # fetch download page
        recordingsUrl = urllib.parse.urljoin(self.kLeccapBaseUrl, courseAnchor.href) 
        response = self.session.get(recordingsUrl)
        
        # parse recordings javascript variable 
        decodedHtml = response.text.replace("\\u0026", "&").replace("\\u0023", "#") 
        unescapedHtml = html.unescape(decodedHtml)
        recordingsMatch = re.search(R"var\s+recordings\s*=\s*(\[(?:(?!]\s*;)(?:.|\n))*\])", unescapedHtml)
        if recordingsMatch is None:
            raise LeccapException(f"Failed to parse recordings from pageHtml")

        recordingsJsonStr = recordingsMatch.group(1)
        recordingsJsonList = parseJsonList(recordingsJsonStr)

        recordings = []
        for recordingDict in recordingsJsonList: 

            # parse recording struct
            recording = recordingDict.instantiate(Leccap.Recording)
            recordings.append(recording)

        return recordings

    def getRecordingInfo(self, recording:Recording) -> RecordingInfo:

        # parse recording key
        splitRecordingUrl = recording.url.split("/")
        if len(splitRecordingUrl) == 0:
            raise LeccapException(f"Failed to extract video key from recording url '{recording.url}'")
        
        recordingKey = splitRecordingUrl[-1]

        # request recording information
        apiResponse = self.session.get(Leccap.kApiUrl, params={"rk": recordingKey})
        apiResponseJson = parseJsonDict(apiResponse.text)

        # parse recording information
        recordingInfo = apiResponseJson.instantiate(Leccap.RecordingInfo)
        return recordingInfo


    def getFutureCourseAnchors(self, startYear:int, stopYear:int) -> dict[int, Future[list[CourseAnchor]]]: 

        futureAnchors = {
            year: self.threadPool.submit(self.getCourseAnchors, year)
            for year in range(startYear, stopYear+1)       
        }

        return futureAnchors


    def getFutureRecordings(self, futureCourseAnchors:Iterable[Future[list[CourseAnchor]]]) -> dict[CourseAnchor, Future[list[Recording]]]:

        futureRecordings = {}
        for future in concurrent.futures.as_completed(futureCourseAnchors):

            courseAnchors = future.result()
            for courseAnchor in courseAnchors:
                futureRecordings[courseAnchor] = self.threadPool.submit(self.getRecordings, courseAnchor) 

        return futureRecordings

    def getFutureRecordingsInfo(self, futureRecordings:Iterable[Future[list[Recording]]]) -> dict[Recording, Future[RecordingInfo]]:

        futureRecordingsInfo = {}
        for future in concurrent.futures.as_completed(futureRecordings):

            recordings = future.result()
            for recording in recordings:
                futureRecordingsInfo[recording] = self.threadPool.submit(self.getRecordingInfo, recording) 

        return futureRecordingsInfo


    @staticmethod
    def sanitizeName(name:str) -> str:
    
        illegalSymbols = {
            "<"  : "{",
            ">"  : "}",
            ":"  : ";",
            "\"" : "'",
            "/"  : "-",
            "\\" : "-",
            "|"  : ";",
            "?"  : Leccap.kDefaultSanitizeSymbol,
            "*"  : Leccap.kDefaultSanitizeSymbol,
            "."  : "_",
        }
    
        sanitizedName = ""
        for c in name.strip():            
            charCode = ord(c)

            if charCode < 32 or charCode > 126:
                sanitizedName+= Leccap.kDefaultSanitizeSymbol
            
            elif c in illegalSymbols:

                sanitizedName+= illegalSymbols[c]

            else:
                sanitizedName+= c

        return sanitizedName


    def downloadRecording(self, recording:Recording, recordingInfo:RecordingInfo, dir:str) -> None:

        product = recordingInfo.getProduct()

        videoExtension = product.movie_type
        recordingDate = datetime.fromtimestamp(recording.timestamp)
        
        videoName = f"{recordingDate.strftime('%Y-%m-%d [%H-%M-%S]')} - " + self.sanitizeName(f"{recordingInfo.title} ({recordingInfo.recordingkey})")
        videoSavePath = os.path.join(dir, f"{videoName}.{videoExtension}")

        if not os.path.exists(dir):
            os.makedirs(dir)
            log(f"Created dir: '{dir}'", logLevel=LogLevel.Verbose)

        else:
            # make sure we get a unique save name so we don't overwrite an existing videos
            videoSaveIndex = 0
            while os.path.exists(videoSavePath):            
                videoSaveIndex+= 1
                videoSavePath = os.path.join(dir, f"{videoName}_{videoSaveIndex}.{videoExtension}")

        mediaUrl = urllib.parse.urljoin(self.kLeccapBaseUrl, recordingInfo.mediaPrefix)
        videoUrl = urllib.parse.urljoin(mediaUrl, f"{recordingInfo.sitekey}/{product.movie_exported_name}.{videoExtension}" )

        videoBytes = int(product.movie_exported_filesize)
        videoBytesHumanStr = parseHumanReadableSize(videoBytes)
        print(f"-> Downloading '{videoSavePath}' [{videoBytesHumanStr}]")

        response = self.session.get(videoUrl, stream=True)
        response.raise_for_status()

        with open(videoSavePath, "wb") as file:
        
            totalBytesWritten = 0
            for chunk in response.iter_content(chunk_size=Leccap.kDownloadChunkSize):
                
                chunkLen = len(chunk)
                bytesWritten = file.write(chunk)

                if bytesWritten != chunkLen:
                    raise LeccapException(f"Failed to write chunk to '{videoSavePath}'. chunkLen: '{chunkLen}', bytesWritten: '{bytesWritten}'")

                totalBytesWritten+= bytesWritten

                percentComplete = totalBytesWritten / videoBytes 
                print(f"--> '{videoSavePath}': {100*percentComplete:.3f}% [{parseHumanReadableSize(totalBytesWritten)} / {videoBytesHumanStr} ]")

    def downloadCourses(self, startYear:int, stopYear:int, dir:str) -> None:
        
        stdOutHeader = f"--- Downloading recordings from '{startYear}' to '{stopYear}' (this may take some time) ---"
        with redirect_stdout(ThreadedStdOut(header=stdOutHeader)):

            futureCourseAnchors = self.getFutureCourseAnchors(startYear=startYear, stopYear=stopYear)
            futureRecordings = self.getFutureRecordings(futureCourseAnchors.values())

            def downloadThread(recording:Leccap.Recording, saveDir:str) -> None:
                print(f"Waiting on recording info for: '{saveDir}'")
                recordingInfo = self.getRecordingInfo(recording)

                self.downloadRecording(recording, recordingInfo, saveDir)
                print(f"Done downloading to '{saveDir}'")

            downloadFutures = []
            for year, courseAnchorsFuture in futureCourseAnchors.items():

                print(f"Status: Getting courses for '{year}'")
                courseAnchors = courseAnchorsFuture.result()
                for courseAnchor in courseAnchors:
    
                    saveDir = os.path.normpath(os.path.join(dir, str(year), self.sanitizeName(courseAnchor.text)))
                    
                    print(f"Status: Getting recordings for '{saveDir}'")
                    recordings = futureRecordings[courseAnchor].result()
                    for recording in recordings:
                        downloadFuture = self.threadPool.submit(downloadThread, recording, saveDir)
                        downloadFutures.append(downloadFuture)

                print(f"Status: Waiting for download threads to finish...")
                concurrent.futures.wait(downloadFutures)

    def listCourses(self, startYear:int, stopYear:int) -> None:
        
        print(f"--- Listing courses from '{startYear}' to '{stopYear}' (this may take some time) ---")
        
        futureCourseAnchors = self.getFutureCourseAnchors(startYear=startYear, stopYear=stopYear)
        
        totalCourses = 0
        for year, future in futureCourseAnchors.items():
            
            courseAnchors = future.result()
            numCourses = len(courseAnchors) 
            
            # skip over blank years
            if numCourses == 0:
                continue

            print(f"{year}:")           
            for courseAnchor in courseAnchors:
                print(f"\t{courseAnchor.text}")    
            
            totalCourses+= numCourses
        
        print(f"\n--- {totalCourses} courses listed ---\n")

    def listRecordings(self, startYear:int, stopYear:int) -> None:

        print(f"--- Listing recordings from '{startYear}' to '{stopYear}' (this may take some time) ---")        
        
        futureCourseAnchors = self.getFutureCourseAnchors(startYear=startYear, stopYear=stopYear)
        futureRecordings = self.getFutureRecordings(futureCourseAnchors.values())
        futureRecordingsInfo = self.getFutureRecordingsInfo(futureRecordings.values())

        totalRecordings = 0
        totalRecordingsBytes = 0

        for year, future in futureCourseAnchors.items():
            courseAnchors = future.result()

            # skip over empty years
            if len(courseAnchors) == 0:
                continue

            print(f"{year}:")

            numCourseRecordings = 0
            numCourseRecordingsBytes = 0
            for courseAnchor in courseAnchors:
                print(f"\t{courseAnchor.text}:") 

                recordings = futureRecordings[courseAnchor].result()
                
                numRecordings = len(recordings)
                numRecordingsBytes = 0
                for recording in recordings:

                    recordingInfo = futureRecordingsInfo[recording].result()
                    recordingProduct = recordingInfo.getProduct()
                    recordingBytes = int(recordingProduct.movie_exported_filesize)
                    
                    print(f"\t\t{recording.title} [Recorded {recording.date} | {parseHumanReadableSize(recordingBytes)}]")    

                    numRecordingsBytes+= recordingBytes

                print(f"\t\t--- {numRecordings} recordings [{parseHumanReadableSize(numRecordingsBytes)}] ---\n")    

                numCourseRecordings+= numRecordings
                numCourseRecordingsBytes+= numRecordingsBytes 

            print(f"\t--- {numCourseRecordings} course recordings [{parseHumanReadableSize(numCourseRecordingsBytes)}] ---\n")    
            totalRecordings+= numCourseRecordings
            totalRecordingsBytes+= numCourseRecordingsBytes


        print(f"\n--- Total {totalRecordings} recordings listed [{parseHumanReadableSize(totalRecordingsBytes)}] ---\n")
