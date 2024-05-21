import requests
import time
import urllib.parse

from datetime import timedelta
from typing import Final

from utils.HtmlForm import HtmlForm
from utils.io import inputListSelection

from utils.logging import *
from utils.parse import *

# TODO: Make this extend HtmlForm
class DuoForm:
    
    kId: Final = "plugin_form" 
    kVersion: Final = "v4" 

    # TODO: parse hostURL from session response so we're more generic
    kHostUrl: Final = "https://api-d9c5afcf.duosecurity.com/frame/"
    kRelativeAuthUrl: Final   = "frameless/v4/auth"
    # kRelativeDataUrl: Final   = "v4/auth/prompt/data"
    kRelativePromptUrl: Final = "v4/prompt"
    kRelativeStatusUrl: Final = "v4/status"
    kRelativeExitUrl: Final   = "v4/oidc/exit"

    kLoginTimeoutSec: Final          = 3*60
    kLoginMinQueryIntervalSec: Final = 1

    # TODO: See if duo and our login system also works with GET
    kSupportedMethods: Final = ["POST"]
    
    kLoginFormId: Final   = "login-form"
    kExitFormClass: Final = "oidc-exit-form"

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
