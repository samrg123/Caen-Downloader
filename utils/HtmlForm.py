import requests
import urllib.parse

from bs4 import BeautifulSoup
from typing import Union

from utils.parse import ParsableDictionary, ParseException, parseHtmlElement

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