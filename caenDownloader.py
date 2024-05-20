import concurrent
import concurrent.futures
from contextlib import redirect_stdout
import dataclasses
import html
import json
import os
import re
import requests
import sys
import time
import types
import urllib.parse

from bs4 import BeautifulSoup
from collections.abc import Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from copy import copy
from datetime import datetime, timedelta
from pwinput import pwinput
from typing import Final, Any, Type, TypeVar, Union, Dict, get_args, get_origin

from utils.ArgParser import *
from utils.logging import *
from utils.ThreadedStdOut import *

def inputListSelection(options:list, prompt:str = "Select an Option") -> int:

    numOptions = len(options)
    if numOptions == 0:
        return -1

    if len(options) == 1:
        return 0

    message = prompt+"\n" + f"\n".join([ f"[{i+1}] {str(option)}" for i,option in enumerate(options)]) + "\nSelection: "

    while True:

        selectionStr = input(message).strip()

        # Note: we add some spacing between inputs to keep things readable
        print("")

        try:
            selectionNumber = int(selectionStr)
            
            if selectionNumber > 0 and selectionNumber <= numOptions:
                return selectionNumber-1

            print(f"'{selectionNumber}' is out of range. Please choose a value between 1 and {numOptions}.")

        except ValueError:
            print(f"'{selectionStr}' is not an integer. Try Again.")

        print("-----\n")


def humanReadableSize(size:int) -> str:
    for suffix in ["bytes", "KB", "MB", "GB", "TB", "PB"]:
        if size < 1024:
            return f"{size:.3f} {suffix}"
        size/= 1024 

# TODO: pull out to parse util

def parseType(value:str|type) -> type | None:

    valueOrigin = get_origin(value)
    valueType = type( valueOrigin if valueOrigin else value )

    if valueType is type:
        return value

    if valueType is str:

        # TODO!!! Add support for dynamically loading modules
        # module, n = None, 0
        # while n < len(parts):
        #     nextmodule = safeimport('.'.join(parts[:n+1]), forceload)
        #     if nextmodule: module, n = nextmodule, n + 1
        #     else: break
        # if module:
        #     object = module
        # else:
        #     object = builtins

        splitValue = [name for name in value.split('.')]        
        result = sys.modules[__name__]
        for name in splitValue:

            result = getattr(result, name, None)
            if result is None:
                return None

        return result

    return None 

class ParseException(Exception):
    def __init__(self, message:str) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        return f"Parse Exception - {super().__str__()}"

KeyT   = TypeVar("KeyT"  )
ValueT = TypeVar("ValueT")
class ParsableDictionary(Dict[Type[KeyT], Type[ValueT]]):

    ObjT = TypeVar("ObjT")
    @staticmethod
    def instantiateValue(value, ObjT:Type[ObjT]) -> Type[ObjT]:

        if isinstance(value, dict):

            if ObjT == ParsableDictionary:        
                return ParsableDictionary(value)
            
            elif dataclasses.is_dataclass(ObjT):

                try:
                
                    # parse fields to make sure everything initializes to correct type
                    parsedArgs = {}
                    for field in fields(ObjT):
                        if field.name in value:
                            parsedArgs[field.name] = ParsableDictionary.parseValue(value[field.name], parseType(field.type))

                    return ObjT(**parsedArgs)
                

                except Exception as e:
                    raise ParseException(f"Failed to instantiate '{ObjT}' dataclass from dictionary type '{type(value)}'. Exception: {e}")         

        return None

    ParseT = TypeVar("ParseT")
    @staticmethod
    def parseValue(value, ParseT:Type[ParseT]=Any) -> Type[ParseT]:

        # Note: we return a copy of the value so modifying parsed data won't modify the original
        if ParseT == Any:
            return copy(value)

        ParseTOrigin = get_origin(ParseT)
        if ParseTOrigin in (Union, types.UnionType):
            for argT in get_args(ParseT):
                try:
                    return ParsableDictionary.parseValue(value, argT)
                except ParseException:
                    pass
            raise ParseException(f"Failed to parse value type '{type(value)}' as: '{ParseT}'. Value = '{value}' ")

        # try to instantiate object 
        instantiatedValue = ParsableDictionary.instantiate(value, ParseT)
        if instantiatedValue is not None:
            return instantiatedValue

        # Note: Python doesn't support checking isinstance(x, 'origin[args...]') yet, so we strip off args 
        ParseInstanceT = ParseTOrigin if ParseTOrigin else ParseT
        if not isinstance(value, ParseInstanceT):
            raise ParseException(f"Unexpected value type '{type(value)}'. Expected: '{ParseInstanceT}'")        
        
        if ParseTOrigin:
            
            # TODO: expand this to work with tuples, sets, and dicts
            supportedTypes = [list]
            if ParseTOrigin not in supportedTypes:
                raise ParseException(f"Unsupported parameterized generic type '{ParseTOrigin}'. Expected one of: '{supportedTypes}'")

            parseTArgs = get_args(ParseT)
            assert len(parseTArgs) == 1
            argT = parseType(parseTArgs[0])

            # parse all our elements to match argT
            return [ParsableDictionary.parseValue(v, argT) for v in value] 
        
        return copy(value)


    ParseT = TypeVar("ParseT")
    def parse(self, key, ParseT:Type[ParseT]=Any) -> Type[ParseT]:

        if key not in self:
            raise ParseException(f"Missing required '{key}' key in: '{self}'")

        return ParsableDictionary.parseValue(self[key], ParseT)
    
    
    ObjT = TypeVar("ObjT")
    def instantiate(self, ObjT:Type[ObjT]) -> Type[ObjT]:
        return ParsableDictionary.instantiateValue(self, ObjT)
         
    def __str__(self) -> str:
        return f"ParsableDictionary {{ {super().__str__()} }}"
    

def parseGetParams(url:str) -> ParsableDictionary:
    parsedUrl = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsedUrl.query)    

    result = ParsableDictionary() 
    for key, val in params.items():

        if len(val) != 1:
            raise ParseException(f"Expected 1 value for get param '{key}', got '{len(val)}'")

        result[key] = val[0]

    return result


def parseJsonDict(jsonStr:str) -> ParsableDictionary:
    rawDict = json.loads(jsonStr)
    return ParsableDictionary(rawDict)

def parseJsonList(jsonStr:str) -> list[ParsableDictionary]:
    rawList = json.loads(jsonStr)
    return [ ParsableDictionary(elmt) for elmt in rawList ]


def parseSoup(htmlSoup:BeautifulSoup, requiredAttributes:list[str] = []) -> BeautifulSoup:

    # make sure soup has required attributes
    for attributeName in requiredAttributes:    
        if attributeName not in htmlSoup.attrs:
            raise ParseException(f"Missing required '{attributeName}' attribute in html soup: '{htmlSoup}'")

    return htmlSoup

def parseSoupElements(htmlSoup:BeautifulSoup, elementName:str, attrs:dict = {}, requiredAttributes:list[str] = []) -> list[BeautifulSoup]:
    elements:list[BeautifulSoup] = htmlSoup.find_all(elementName, attrs=attrs)
    return [parseSoup(elmt) for elmt in elements]

def parseSoupElement(htmlSoup:BeautifulSoup, elementName:str, attrs:dict = {}, requiredAttributes:list[str] = []) -> BeautifulSoup:

    # Grab html element
    elements:list[BeautifulSoup] = htmlSoup.find_all(elementName, attrs=attrs)

    numElements = len(elements)
    if numElements != 1:
        raise ParseException(f"Expected 1 '{elementName}' element, got {numElements}")
    
    elementSoup = elements[0]
    return parseSoup(elementSoup, requiredAttributes=requiredAttributes)


def parseSoupElementsByName(htmlSoup:BeautifulSoup, nameAttribute:str, requiredAttributes:list[str] = []) -> list[BeautifulSoup]:

    elements = htmlSoup.find_all(attrs={"name": nameAttribute})
    return [parseSoup(elmt, requiredAttributes=requiredAttributes) for elmt in elements] 


def parseHtmlElements(html:str, elementName:str, attrs:dict = {}, requiredAttributes:list[str] = []) -> list[BeautifulSoup]:
    return parseSoupElements(BeautifulSoup(html, 'html.parser'), elementName=elementName, attrs=attrs, requiredAttributes=requiredAttributes)

def parseHtmlElement(html:str, elementName:str, attrs:dict = {}, requiredAttributes:list[str] = []) -> BeautifulSoup:
    return parseSoupElement(BeautifulSoup(html, 'html.parser'), elementName=elementName, attrs=attrs, requiredAttributes=requiredAttributes)

def parseHtmlElementsByName(html:str, nameAttribute:str, requiredAttributes:list[str] = []) -> list[BeautifulSoup]:
    return parseSoupElementsByName(BeautifulSoup(html, 'html.parser'), nameAttribute=nameAttribute, requiredAttributes=requiredAttributes)


class HtmlForm:
    url:str
    method:str
    inputs:ParsableDictionary[str, Union[None, str, list]]

    @staticmethod
    def parseResponse(response:requests.Response, attrs:dict = {}, requiredInputs:list[str] = []) -> "HtmlForm":

        htmlForm = HtmlForm()
        formSoup = parseHtmlElement(response.text, "form", attrs=attrs, requiredAttributes=["action", "method"])

        # Grab action url
        relativeUrl = urllib.parse.unquote(formSoup.get("action").strip())
        htmlForm.url = urllib.parse.urljoin(response.url, relativeUrl)

        # parse form method
        htmlForm.method = formSoup.get("method").lower().strip()
        if htmlForm.method not in ["post", "get"]:
            raise ParseException(f"Unknown form method attribute: '{htmlForm.method}' in form: '{formSoup}'")

        # parse form inputs
        htmlForm.inputs = ParsableDictionary()
        formInput:BeautifulSoup
        for formInput in formSoup.find_all("input"):

            inputName  = str(formInput.get("name"))
            inputValue = formInput.get("value")

            if inputValue is not None:
                inputValue = urllib.parse.unquote(inputValue)

            if inputName not in htmlForm.inputs:
          
                # create new input
                htmlForm.inputs[inputName] = inputValue

            else:

                # append input
                previousValue = htmlForm.inputs[inputName]
                if type(previousValue) is list:
                    previousValue.append(inputValue)
                else:
                    htmlForm.inputs[inputName] = [previousValue, inputValue]

        # make sure we parsed the required inputs
        for key in requiredInputs:
            if key not in htmlForm.inputs:
                raise ParseException(f"Missing required input '{key}' in form: '{formSoup}'")
            
        return htmlForm
    
    def submit(self, session:requests.Session, payload:dict = {}) -> requests.Response:
        data = self.inputs|payload
        return session.request(method=self.method, url=self.url, data=data)

# TODO: Make this extend HtmlForm
# TODO: Move this out to its own file
class DuoForm:
    
    kId:Final = "plugin_form" 
    kVersion:Final = "v4" 

    # TODO: parse hostURL from session response so we're more generic
    kHostUrl:Final = "https://api-d9c5afcf.duosecurity.com/frame/"
    kRelativeAuthUrl:Final   = "frameless/v4/auth"
    # kRelativeDataUrl:Final   = "v4/auth/prompt/data"
    kRelativePromptUrl:Final = "v4/prompt"
    kRelativeStatusUrl:Final = "v4/status"
    kRelativeExitUrl:Final   = "v4/oidc/exit"

    kLoginTimeoutSec:Final          = 3*60
    kLoginMinQueryIntervalSec:Final = 1

    # TODO: See if duo and our login system also works with GET
    kSupportedMethods:Final = ["POST"]
    
    kLoginFormId:Final   = "login-form"
    kExitFormClass:Final = "oidc-exit-form"

    tx:str
    sid:str
    method:str    

    requiredInputs:dict[str,str] = dict.fromkeys(["tx", "_xsrf", "version", "akey"])

    @staticmethod
    def parseResponse(response:requests.Response) -> "DuoForm":

        duoForm = DuoForm()
        formSoup = parseHtmlElement(
            response.text, 
            "form",
            requiredAttributes=["id", "method"]
        )
        formAttributes = formSoup.attrs

        getParams = parseGetParams(response.url)
        duoForm.sid = getParams.parse("sid")
        duoForm.tx  = getParams.parse("tx")

        # make sure we're parsing the right form
        if formAttributes["id"].strip() != DuoForm.kId:
            raise ParseException(f"Expected '{DuoForm.kId}' form id, got '{formAttributes['id']}'")

        # parse method
        duoForm.method = formAttributes["method"].strip().upper()
        if duoForm.method not in duoForm.kSupportedMethods:
            raise ParseException(f"Unexpected form method '{duoForm.method}', Expected one of '{duoForm.kSupportedMethods}'")

        # parse inputs
        for name in duoForm.requiredInputs.keys():

            soups = parseSoupElementsByName(formSoup, name, requiredAttributes=["value"])
            if len(soups) != 1:
                raise ParseException(f"Expected 1 form input with name '{name}', got '{len(soups)}'")

            duoForm.requiredInputs[name] = soups[0].attrs["value"].strip()

        # Make sure we're parsing the correct duoIframe version
        version = duoForm.requiredInputs["version"]
        if version != duoForm.kVersion:
            raise ParseException(f"Expected duo from version '{duoForm.kVersion}', got version '{version}'")

        return duoForm
    
    # TODO: should we cache login session in self?
    def sendApi(self, session:requests.Session, url:str, payload:dict = {}, timeout:float|None = None) -> ParsableDictionary | None:
        """
            Sends a post payload to provided duo api url and returns the parsed response.
            If `timeout` is non-None proved, function will block for at most `timeout` seconds while waiting
            for a response and return `None` if the timeout is exceeded. 
            Note: A negative or zero timeout will result in this function returning immediately without sending message  
        """

        # Note: negative timeout causes requests.post to throw value error and zero value is used for no timeout
        #       so we detect fail condition here
        if timeout is not None and timeout <= 0:
            return None

        # send api message
        try:
            apiResponse = session.post(url, data=payload, timeout=timeout)

        except requests.exceptions.Timeout:
            return None
        
        apiResponseJson = parseJsonDict(apiResponse.text)
        log(f"Duo api message | url: {url} | payload: {payload} | apiResponse: {apiResponseJson}", logLevel=LogLevel.Verbose)

        apiResponseStat = apiResponseJson.parse("stat", str)
        if apiResponseStat.lower() != "ok":
            warn(f"Unexpected duo api response status: '{apiResponseStat}', was expecting 'ok'")

        # grab api response
        responseDict = apiResponseJson.parse("response", dict)

        return ParsableDictionary(responseDict)

    def login(self, session:requests.Session, maxLoginAttempts:int = 1) -> str | None:
        """ 
            Returns the redirect url on success or None of failure 
            Note: This function will a throw a ParseException if it encounters malformed duo responses
        """

        for i in range(1, maxLoginAttempts+1):

            print(f"Duo Authentication Attempt {i}/{maxLoginAttempts}")

            # TODO: Should we pull this out of the login attempt loop?
            # Initialize login and get sid
            authUrl = urllib.parse.urljoin(self.kHostUrl, DuoForm.kRelativeAuthUrl)
            response = session.request(
                self.method,
                authUrl,
                params = {
                    "tx": self.tx,
                    "sid": self.sid,
                },
                data = self.requiredInputs                
            )
            
            # parse login form
            duoPromptForm = HtmlForm.parseResponse(response, attrs={"id": self.kLoginFormId}, requiredInputs=["sid", "factor"]) 
            sid = duoPromptForm.inputs["sid"]
            if sid != self.sid:
                raise ParseException(f"Mismatched sid - Got login sid '{sid}', expected duo form sid '{self.sid}'")

            # parse exit form
            oidcExitForm = HtmlForm.parseResponse(response, attrs={"class": self.kExitFormClass}, requiredInputs=["_xsrf"])
            _xsrf = oidcExitForm.inputs["_xsrf"]
            if _xsrf != self.requiredInputs["_xsrf"]:
                raise ParseException(f"Mismatched _xsrf - Got exit form _xsrf '{_xsrf}', expected duo form _xsrf '{self.requiredInputs['_xsrf']}'")

            # parse available devices        
            deviceMap:dict[str, str] = {}
            for deviceSoup in parseHtmlElementsByName(response.text, "device"):
                
                optionSoup = parseSoupElement(deviceSoup, "option", requiredAttributes=["value"])

                deviceValue = optionSoup.attrs["value"] 
                deviceText  = urllib.parse.unquote(optionSoup.getText())

                deviceMap[deviceValue] = deviceText

            # get selected device
            selectedIndex = inputListSelection(list(deviceMap.values()), "Select an Authentication Device")
            selectedDevice = list(deviceMap.keys())[selectedIndex]

            # get authentication factor
            selectedAuthenticationFactor:str
            authenticationFactors = duoPromptForm.inputs["factor"]
            if isinstance(authenticationFactors, list):

                selectedIndex = inputListSelection(authenticationFactors, "Select an Authentication Factor")
                selectedAuthenticationFactor = authenticationFactors[selectedIndex] 
            else:
                # Use the only possible option
                selectedAuthenticationFactor = authenticationFactors

            usingPasscode = (selectedAuthenticationFactor.strip().lower() == "passcode")

            # send duo factor authentication method prompt
            promptUrl = urllib.parse.urljoin(self.kHostUrl, self.kRelativePromptUrl)
            promptPayload = {
                "sid": sid,
                "device": selectedDevice,
                "factor": "sms" if usingPasscode else selectedAuthenticationFactor,
                "postAuthDestination": "OIDC_EXIT"
            }

            promptResponse = self.sendApi(
                session,
                promptUrl,
                payload = promptPayload
            )
            txid = promptResponse.parse("txid", str)

            statusUrl = urllib.parse.urljoin(self.kHostUrl, DuoForm.kRelativeStatusUrl)
            
            # Wait for duo authentication success
            queryStartTime = datetime.now()
            loginTimeoutTime = queryStartTime + timedelta(seconds=DuoForm.kLoginTimeoutSec)
            while True:

                if usingPasscode:
                    passcode = input("Enter Passcode: ").strip()
                    
                    # Initiate new prompt response using passcode
                    promptPayload|= {"passcode": passcode, "factor": selectedAuthenticationFactor }
                    promptResponse = self.sendApi(
                        session,
                        promptUrl,
                        payload = promptPayload
                    )
                    txid = promptResponse.parse("txid", str)
                    
                remainingTimeoutSec = (loginTimeoutTime - queryStartTime).total_seconds()
                statusResponse = self.sendApi(
                    session,
                    statusUrl,
                    timeout = remainingTimeoutSec,
                    payload = {
                        "sid": sid,
                        "txid": txid
                    }
                )

                if not statusResponse:
                    print(f"Failed to login to duo | Login Timeout of {DuoForm.kLoginTimeoutSec} seconds exceeded")
                    break

                statusCode = statusResponse.parse("status_code", str).lower()
                print(f"Status: '{statusCode}'")

                match statusCode:

                    case "allow":

                        exitUrl = urllib.parse.urljoin(self.kHostUrl, self.kRelativeExitUrl)
                        exitResponse = session.post(exitUrl, data={
                            "sid": sid,
                            "txid": txid,
                            "_xsrf": _xsrf,                            
                        })

                        return exitResponse
                    
                    case "deny":
                        print(f"Failed to login to duo | api Response: {statusResponse}")
                        break


                # sleep until end of current query interval
                queryEndTime = datetime.now()
                queryIntervalSec = (queryEndTime - queryStartTime).total_seconds()

                if queryIntervalSec < DuoForm.kLoginMinQueryIntervalSec:
                    sleepSec = DuoForm.kLoginMinQueryIntervalSec - queryIntervalSec

                    log(f"Sleeping {sleepSec:0.3} seconds until next duo api status query...")
                    time.sleep(sleepSec)

                queryStartTime = queryEndTime

            print(f"\n--------\n")

        return None

class DebugSession(requests.Session):
    def __init__(self) -> None:
        super().__init__()

        self.request = self.debugRequest

    def debugRequest(self, method, url, **kwargs):

        response = super().request(method, url, **kwargs)
        log(f"Request: {method} @ '{url}' | [ {kwargs.get('data')} ] -> '{response.url}'", logLevel=LogLevel.Verbose)

        return response

class CaenDownloaderException(Exception):
    def __init__(self, message:str) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        return f"CaenDowloader Exception - {super().__str__()}"    

class CaenDownloader():

    kMinYear: Final = 2006
    kMaxYear: Final = datetime.now().year

    kLeccapBaseUrl: Final = "https://leccap.engin.umich.edu/leccap/"
    kApiUrl: Final = "https://leccap.engin.umich.edu/leccap/player/api/product/"

    kDefaultSanitizeSymbol: Final = ""
    kDownloadChunkSize: Final = 1*1024*1024 

    loggedIn:bool = False
    
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
        products: list["CaenDownloader.RecordingProduct"]
        aux_sources: list["CaenDownloader.RecordingAuxSource"] | None
        captions: list["CaenDownloader.RecordingCaption"] | str
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
        info: "CaenDownloader.RecordingProductInfo"

        def __post_init__(self) -> None:
            numRecordingProducts = len(self.info.products)
            if numRecordingProducts != 1:
                raise CaenDownloaderException(f"Expected 1 recording product for '{self.title}', got '{numRecordingProducts}'")

        def getProduct(self) -> "CaenDownloader.RecordingProduct":
            return self.info.products[0]

    def __init__(self, numThreads:int) -> None:
        self.session = DebugSession()
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

        raise CaenDownloaderException(f"Failed to login")
    
    
    def getCourseAnchors(self, year) -> list[CourseAnchor]:

        if year < CaenDownloader.kMinYear or year > CaenDownloader.kMaxYear:
            raise CaenDownloaderException(f"Invalid course year '{year}'. Valid Range [{CaenDownloader.kMinYear}, {CaenDownloader.kMaxYear}]") 

        url = urllib.parse.urljoin(CaenDownloader.kLeccapBaseUrl, str(year))
        response = self.session.get(url)
        
        anchorSoups = parseHtmlElements(response.text, "a", attrs={"class": "list-group-item"}, requiredAttributes=["href"])
        anchors = [ 
            CaenDownloader.CourseAnchor(
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
            raise CaenDownloaderException(f"Failed to parse recordings from pageHtml")

        recordingsJsonStr = recordingsMatch.group(1)
        recordingsJsonList = parseJsonList(recordingsJsonStr)

        recordings = []
        for recordingDict in recordingsJsonList: 

            # parse recording struct
            recording = recordingDict.instantiate(CaenDownloader.Recording)
            recordings.append(recording)

        return recordings

    def getRecordingInfo(self, recording:Recording) -> RecordingInfo:

        # parse recording key
        splitRecordingUrl = recording.url.split("/")
        if len(splitRecordingUrl) == 0:
            raise CaenDownloaderException(f"Failed to extract video key from recording url '{recording.url}'")
        
        recordingKey = splitRecordingUrl[-1]

        # request recording information
        apiResponse = self.session.get(CaenDownloader.kApiUrl, params={"rk": recordingKey})
        apiResponseJson = parseJsonDict(apiResponse.text)

        # parse recording information
        recordingInfo = apiResponseJson.instantiate(CaenDownloader.RecordingInfo)
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
            "?"  : CaenDownloader.kDefaultSanitizeSymbol,
            "*"  : CaenDownloader.kDefaultSanitizeSymbol,
            "."  : "_",
        }
    
        sanitizedName = ""
        for c in name.strip():            
            charCode = ord(c)

            if charCode < 32 or charCode > 126:
                sanitizedName+= CaenDownloader.kDefaultSanitizeSymbol
            
            elif c in illegalSymbols:

                sanitizedName+= illegalSymbols[c]

            else:
                sanitizedName+= c

        return sanitizedName


    def downloadRecording(self, recordingInfo:RecordingInfo, dir:str) -> None:

        product = recordingInfo.getProduct()
        videoExtension = product.movie_type

        mediaUrl = urllib.parse.urljoin(self.kLeccapBaseUrl, recordingInfo.mediaPrefix)
        videoUrl = urllib.parse.urljoin(mediaUrl, f"{recordingInfo.sitekey}/{product.movie_exported_name}.{videoExtension}" )

        videoSaveName = self.sanitizeName(f"{recordingInfo.date} - {recordingInfo.title}") + f".{videoExtension}"
        videoSavePath = os.path.join(dir, videoSaveName)

        # TODO: update flag to not download if files already exist 
        if not os.path.exists(dir):
            os.makedirs(dir)
            log(f"Created dir: '{dir}'", logLevel=LogLevel.Verbose)            


        videoBytes = int(product.movie_exported_filesize)
        videoBytesHumanStr = humanReadableSize(videoBytes)
        print(f"-> Downloading '{videoSavePath}' [{videoBytesHumanStr}]")

        response = self.session.get(videoUrl, stream=True)
        response.raise_for_status()

        with open(videoSavePath, "wb") as file:
        
            totalBytesWritten = 0
            for chunk in response.iter_content(chunk_size=CaenDownloader.kDownloadChunkSize):
                
                chunkLen = len(chunk)
                bytesWritten = file.write(chunk)

                if bytesWritten != chunkLen:
                    raise CaenDownloaderException(f"Failed to write chunk to '{videoSavePath}'. chunkLen: '{chunkLen}', bytesWritten: '{bytesWritten}'")

                totalBytesWritten+= bytesWritten

                percentComplete = totalBytesWritten / videoBytes 
                print(f"--> '{videoSavePath}': {100*percentComplete:.3f}% [{humanReadableSize(totalBytesWritten)} / {videoBytesHumanStr} ]")

    def downloadCourses(self, startYear:int, stopYear:int, dir:str) -> None:
        
        stdOutHeader = f"--- Downloading recordings from '{startYear}' to '{stopYear}' (this may take some time) ---"
        with redirect_stdout(ThreadedStdOut(header=stdOutHeader)):

            futureCourseAnchors = self.getFutureCourseAnchors(startYear=startYear, stopYear=stopYear)
            futureRecordings = self.getFutureRecordings(futureCourseAnchors.values())

            def downloadThread(recording:CaenDownloader.Recording, saveDir:str) -> None:
                print(f"Waiting on recording info for: '{saveDir}'")
                recordingInfo = self.getRecordingInfo(recording)

                self.downloadRecording(recordingInfo, saveDir)
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
                    
                    print(f"\t\t{recording.title} [Recorded {recording.date} | {humanReadableSize(recordingBytes)}]")    

                    numRecordingsBytes+= recordingBytes

                print(f"\t\t--- {numRecordings} recordings [{humanReadableSize(numRecordingsBytes)}] ---\n")    

                numCourseRecordings+= numRecordings
                numCourseRecordingsBytes+= numRecordingsBytes 

            print(f"\t--- {numCourseRecordings} course recordings [{humanReadableSize(numCourseRecordingsBytes)}] ---\n")    
            totalRecordings+= numCourseRecordings
            totalRecordingsBytes+= numCourseRecordingsBytes


        print(f"\n--- Total {totalRecordings} recordings listed [{humanReadableSize(totalRecordingsBytes)}] ---\n")



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
        start          = Arg(longName="--start",            metavar="int",  type=int,   default=CaenDownloader.kMinYear, help=f"Specifies the year to start parsing courses.")
        stop           = Arg(longName="--stop",             metavar="int",  type=int,   default=datetime.today().year,   help=f"Specifies the year to stop parsing courses.")
        threads        = Arg(longName="--threads",          metavar="int",  type=int,   default=min(12, os.cpu_count()), help=f"Specifies the number of threads to use while downloading.")
        verbose        = Arg(longName="--verbose",          metavar="int",  type=int,   default=LogLevel.Default,        help=f"Specifies the verbose log level. Larger values enable more verbose output. Log Levels: {LogLevel.getMapping()}")

    argParser = ArgParser(
        description = "A lightweight python utility for downloading recorded CAEN lectures from the University of Michigan."        
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

    if startYear < CaenDownloader.kMinYear:
        warn(f"Invalid startYear: {startYear}, clamping to {CaenDownloader.kMinYear}")
        startYear = CaenDownloader.kMinYear 

    if stopYear > CaenDownloader.kMaxYear:
        warn(f"Invalid stopYear: {stopYear}, clamping to {CaenDownloader.kMaxYear}")
        stopYear = CaenDownloader.kMaxYear    

    caenDownloader = CaenDownloader(numThreads=numThreads)
    caenDownloader.login()
    
    if args.listCourses.value:

        if args.listRecordings.value:
            warn(f"Ignoring '{args.listRecordings.longName}'")
        
        caenDownloader.listCourses(startYear=startYear, stopYear=stopYear)
        exit()

    if args.listRecordings.value:
        caenDownloader.listRecordings(startYear=startYear, stopYear=stopYear)
        exit()

    caenDownloader.downloadCourses(
        startYear = startYear, 
        stopYear  = stopYear,
        dir       = dirPath,
    )

if __name__ == "__main__":
    main()