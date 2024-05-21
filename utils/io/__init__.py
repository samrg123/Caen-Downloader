
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
