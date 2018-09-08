import win32com.client
import win32ui
import win32api
import win32gui
import win32con
import win32clipboard

'''
def window_enum_handler(hwnd, resultList):
    if win32gui.IsWindowVisible(hwnd) and win32gui.GetWindowText(hwnd) != '':
        resultList.append((hwnd, win32gui.GetWindowText(hwnd)))


def get_app_list(handles=[]):
    mlst = []
    win32gui.EnumWindows(window_enum_handler, handles)
    for handle in handles:
        mlst.append(handle)
    return mlst
'''

class NotYoutube(Exception):
    def __init__(self, message):

        # Call the base class constructor with the parameters it needs
        super().__init__(message)

def main():
    print("Opening cmd to download...")
    shell = win32com.client.Dispatch("WScript.Shell")
    shell.Run("cmd.exe")
    shell.AppActivate("cmd.exe")
    win32api.Sleep(100)  # need this or else the SendKeys freaks out

    win32clipboard.OpenClipboard()
    # clipboard_as_encoded_bytes = (win32clipboard.GetClipboardData(win32con.CF_TEXT))
    clipboard_as_string = (win32clipboard.GetClipboardData(win32con.CF_TEXT)).decode(
        "utf-8")  # must decode from bytes to string
    if clipboard_as_string.startswith("https://www.youtube.com/watch?v=") is False:
        raise NotYoutube("The value on the clipboard is not a YouTube URL.")
    win32clipboard.CloseClipboard()

    # clipboard = "https://www.youtube.com/watch?v=u7bfD7GqgtU"  # TODO: Make this the actual clipboard data
    shell.SendKeys("cd /d C:\\Users\\vince{Enter}")
    shell.SendKeys("youtube-dl " + clipboard_as_string + " && exit")
    shell.SendKeys("{Enter}")
    handle = win32gui.FindWindow(0, "C:\\Windows\\System32\\cmd.exe")  # Has to be spelled out for some reason
    win32gui.CloseWindow(handle)

    '''
    appwindows = get_app_list()
    for i in appwindows:
        print(i)
    '''

    print("done!")


if __name__ == "__main__":
    main()
