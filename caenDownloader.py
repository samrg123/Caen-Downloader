import html
import os
import re
import time
from typing import Final, Any, Type, TypeVar, Union, Dict

import json
from bs4 import BeautifulSoup

import requests
import urllib.parse

from datetime import datetime, timedelta
from pwinput import pwinput

from utils.logging import *
from utils.ArgParser import *

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


# TODO: pull out to parse util

class ParseException(Exception):
    def __init__(self, message:str) -> None:
        super().__init__(message)

    def __str__(self) -> str:
        return f"Parse Exception - {super().__str__()}"

KeyT   = TypeVar("KeyT"  )
ValueT = TypeVar("ValueT")
class ParsableDictionary(Dict[Type[KeyT], Type[ValueT]]):

    ParseT = TypeVar("ParseT")
    def parse(self, key, ParseT:Type[ParseT]=Any) -> Type[ParseT]:

        if key not in self:
            raise ParseException(f"Missing required '{key}' key in: '{self}'")

        value = self[key]
        if ParseT == Any:
            return value

        if not isinstance(value, ParseT):
            raise ParseException(f"Unexpected '{key}' value type in: '{self}'. Got '{type(value)}', Expected: '{ParseT}'")        
        
        return value

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


def parseHtmlElements(html:str, elementName:str, attrs:dict = {}, requiredAttributes:list[str] = []) -> BeautifulSoup:
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
        log(f"Duo api message | url: {url} | payload: {payload} | apiResponse: {apiResponseJson}", logLevel=LOG_LEVEL_VERBOSE)

        apiResponseStat = apiResponseJson.parse("stat", str)
        if apiResponseStat.lower() != "ok":
            warn(f"Unexpected duo api response status: '{apiResponseStat}', was expecting 'ok'")

        # grab api response
        responseDict = apiResponseJson.parse("response", dict)

        return ParsableDictionary(responseDict)

    def login(self, session:requests.Session, maxLoginAttempts:int = 1) -> str|None:
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
        log(f"Request: {method} @ '{url}' | [ {kwargs.get('data')} ] -> '{response.url}'", logLevel=LOG_LEVEL_VERBOSE)

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
    kRelativeApiUrl: Final = "player/api/product"

    kDefaultSanitizeSymbol:Final = ""

    _loggedIn:bool = False

    def __init__(self) -> None:
        self.session = DebugSession() 

    def login(self, maxLoginAttempts:int = 3) -> bool:

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

            self._loggedIn = True 
            return True

        return False

    def getCourseAnchors(self, startYear:int, stopYear:int) -> list[BeautifulSoup]:

        if startYear < CaenDownloader.kMinYear:
            warn(f"Invalid startYear: {startYear}, clamping to {CaenDownloader.kMinYear}")
            startYear = CaenDownloader.kMinYear 

        if stopYear > CaenDownloader.kMaxYear:
            warn(f"Invalid stopYear: {stopYear}, clamping to {CaenDownloader.kMaxYear}")
            stopYear = CaenDownloader.kMaxYear

        if not self._loggedIn and not self.login():
            raise CaenDownloaderException(f"Failed to login")

        courses = []
        for year in range(startYear, stopYear+1):

            url = urllib.parse.urljoin(self.kLeccapBaseUrl, str(year))
            response = self.session.get(url)

            courses+= parseHtmlElements(response.text, "a", attrs={"class": "list-group-item"}, requiredAttributes=["href"])

        return courses

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


    def downloadRecordings(self, pageHtml:str, dir:str) -> None:

        apiUrl = urllib.parse.urljoin(self.kLeccapBaseUrl, self.kRelativeApiUrl)

        # parse recordings javascript variable 
        decodedHtml = pageHtml.encode().decode('unicode-escape')
        unescapedHtml = html.unescape(decodedHtml)
        recordingsMatch = re.search(r"var\s+recordings\s*=\s*([^;]*);", unescapedHtml)
        if recordingsMatch is None:
            raise CaenDownloaderException(f"Failed to parse recordings from pageHtml")

        recordingsJsonStr = recordingsMatch.group(1)
        recordingsJsonList = parseJsonList(recordingsJsonStr)

        for recordingDict in recordingsJsonList: 

            # parse recording key
            recordingUrl = recordingDict.parse("url", str)
            splitRecordingUrl = recordingUrl.split("/")

            if len(splitRecordingUrl) == 0:
                raise CaenDownloaderException(f"Failed to extract video key from recording url '{recordingUrl}'")

            recordingKey = splitRecordingUrl[-1]
            
            # request recording information
            apiResponse = self.session.get(apiUrl, params={"rk": recordingKey})
            apiResponseJson = parseJsonDict(apiResponse.text)

            # parse recording information
            title = apiResponseJson.parse("title", str)
            siteKey = apiResponseJson.parse("sitekey", str)
            mediaPrefix = apiResponseJson.parse("mediaPrefix", str)
 
            info = ParsableDictionary(apiResponseJson.parse("info", dict))
            products = info.parse("products", list)

            if len(products) != 1:
                raise CaenDownloaderException(f"Expected 1 product for recording '{title}', got '{len(products)}'")
            
            product = ParsableDictionary(products[0])

            videoName = product.parse("movie_exported_name", str)
            videoExtension = product.parse("movie_type", str)

            mediaUrl = urllib.parse.urljoin(self.kLeccapBaseUrl, mediaPrefix)
            videoUrl = urllib.parse.urljoin(mediaUrl, f"{siteKey}/{videoName}.{videoExtension}" )

            # download video
            videoSaveName = self.sanitizeName(title) + f".{videoExtension}"
            videoSavePath = os.path.join(dir, videoSaveName)
            print(f"-> Downloading '{videoSavePath}'")

            # TODO: is there a way to just stream this to disk?... would save a lot of ram and speed up debugger
            videoResponse = self.session.get(videoUrl)
            if videoResponse.status_code != 200:
                warn(f"Failed to download '{videoUrl}'. Expected status code 200, got '{videoResponse.status_code}'")
                continue

            # write video to disk
            if not os.path.exists(dir):
                os.makedirs(dir)
                log(f"Created dir: '{dir}'", logLevel=LOG_LEVEL_VERBOSE)            

            with open(videoSavePath, "wb") as file:
                file.write(videoResponse.content)


    def downloadCourses(self, startYear:int, stopYear:int, dir:str) -> None:

        courseAnchors = self.getCourseAnchors(startYear=startYear, stopYear=stopYear)
        for courseAnchor in courseAnchors:
            
            saveDir = os.path.join(dir, self.sanitizeName(courseAnchor.text))
            print(f"Downloading recordings for '{courseAnchor.text}':")

            courseHref:str = courseAnchor.attrs["href"]
            recordingsUrl = urllib.parse.urljoin(self.kLeccapBaseUrl, courseHref) 

            response = self.session.get(recordingsUrl)
            self.downloadRecordings(response.text, saveDir)

    def listCourses(self, startYear:int, stopYear:int) -> None:
        
        courseAnchors = self.getCourseAnchors(startYear=startYear, stopYear=stopYear)

        print(f"Courses from {startYear} - {stopYear}:")
        for courseAnchor in courseAnchors:
            print(f"\t{courseAnchor.text}")


    def getRecordingIds(self, startYear:int, stopYear:int) -> None:
        pass

def main():
    
    # TODO: Add support for matching course name and/or recording titles with regex
    # TODO: have --update option that only downloads files if it doesn't already exist in output directory
    # TODO: add ability to download captions if available and convert the to srt file saved alongside video
    # TODO: add ability to download thumbnails? why would we want this
    # TODO: figure out waveform.audiomap is used for ... just looks like a binary blob
    # TODO: add multi-threading support for faster downloads
    class MainArgs(Args):
        dir     = Arg(longName="--dir",     metavar="str",  type=str,   default="./recordings",            help=f"Specifies the directory to output downloaded recordings to.")
        start   = Arg(longName="--start",   metavar="int",  type=int,   default=CaenDownloader.kMinYear, help=f"Specifies the year to start parsing courses.")
        stop    = Arg(longName="--stop",    metavar="int",  type=int,   default=datetime.today().year,   help=f"Specifies the year to stop parsing courses.")
        verbose = Arg(longName="--verbose", metavar="int",  type=int,   default=LOG_LEVEL_DEFAULT,       help=f"Specifies the verbose level. Larger values enable more verbose output.")
        list    = Arg(longName="--list",    action="store_true",        default=False,                   help=f"Lists available courses to download")

    argParser = ArgParser(
        prog = "Caen Downloader",
        description = "A lightweight python utility for downloading recorded CAEN lectures from the University of Michigan."        
    )

    args = argParser.Parse(MainArgs())

    setLogLevel(args.verbose.value)

    argStr = "\n".join([f"\t{name} [{type(arg.value)}] = '{arg.value}'" for name, arg in args.ArgDict().items()])
    log(f"Using args: {{\n{argStr}\n}}", logLevel=LOG_LEVEL_VERBOSE)

    dirPath   = args.dir.value
    startYear = args.start.value
    stopYear  = args.stop.value

    caenDownloader = CaenDownloader()
    if args.list.value:

        caenDownloader.listCourses(startYear=startYear, stopYear=stopYear)

    else:
        caenDownloader.downloadCourses(
            startYear = startYear, 
            stopYear  = stopYear,
            dir       = dirPath,
        )

if __name__ == "__main__":
    main()